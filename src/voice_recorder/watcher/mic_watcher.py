"""Detecta uso do microfone no Windows via o registro, o gatilho do Modo 1
(call): HKEY_CURRENT_USER\\...\\CapabilityAccessManager\\ConsentStore\\microphone.

Cada app que já pediu acesso ao microfone tem uma subchave com
LastUsedTimeStart/LastUsedTimeStop (FILETIME como QWORD). Quando
LastUsedTimeStop é 0 (ou ausente), o app está usando o microfone agora.
Apps Win32 comuns (Teams, Zoom, Discord, navegadores) ficam sob a subchave
"NonPackaged"; apps UWP ficam direto na raiz — por isso percorremos os dois.

Esse sinal é independente de qual app está em uso, por isso funciona pra
qualquer ferramenta de call sem precisar de uma lista configurada.
"""

import threading
import winreg
from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional, Tuple

CONSENT_STORE_PATH = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone"
)


@dataclass
class MicWatcherConfig:
    poll_interval_seconds: float = 1.5


def _read_qword(key, name: str) -> Optional[int]:
    try:
        value, _ = winreg.QueryValueEx(key, name)
        return value
    except FileNotFoundError:
        return None


def _iter_app_keys() -> Iterator[Tuple[str, "winreg.HKEYType", str]]:
    """Gera (rótulo, chave_pai, nome_subchave) para cada app registrado."""
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONSENT_STORE_PATH)
    except FileNotFoundError:
        return

    with root:
        index = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1

            if subkey_name == "NonPackaged":
                try:
                    nonpackaged = winreg.OpenKey(root, subkey_name)
                except OSError:
                    continue
                with nonpackaged:
                    j = 0
                    while True:
                        try:
                            app_name = winreg.EnumKey(nonpackaged, j)
                        except OSError:
                            break
                        j += 1
                        yield f"NonPackaged\\{app_name}", nonpackaged, app_name
            else:
                yield subkey_name, root, subkey_name


def _is_active(parent_key, app_name: str) -> bool:
    try:
        with winreg.OpenKey(parent_key, app_name) as app_key:
            start = _read_qword(app_key, "LastUsedTimeStart")
            stop = _read_qword(app_key, "LastUsedTimeStop")
            if start is None:
                return False
            return not stop
    except OSError:
        return False


def get_active_mic_apps() -> List[str]:
    """Retorna os rótulos dos apps com o microfone em uso agora."""
    return [
        label
        for label, parent_key, app_name in _iter_app_keys()
        if _is_active(parent_key, app_name)
    ]


class MicWatcher:
    """Faz polling do registro e dispara on_call_start/on_call_end quando o
    conjunto de apps usando o microfone alterna entre vazio e não-vazio."""

    def __init__(
        self,
        on_call_start: Callable[[List[str]], None],
        on_call_end: Callable[[], None],
        config: Optional[MicWatcherConfig] = None,
    ):
        self.on_call_start = on_call_start
        self.on_call_end = on_call_end
        self.config = config or MicWatcherConfig()
        self._was_active = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            active_apps = get_active_mic_apps()
            is_active = len(active_apps) > 0

            if is_active and not self._was_active:
                self.on_call_start(active_apps)
            elif not is_active and self._was_active:
                self.on_call_end()

            self._was_active = is_active
            self._stop_event.wait(self.config.poll_interval_seconds)
