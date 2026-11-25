"""Detecta uso do microfone no Windows, o gatilho do Modo 1 (call), pelas
sessões de captura da API de áudio: um app com sessão ativa num microfone
está usando o microfone agora.

Esse sinal é independente de qual app está em uso, por isso funciona pra
qualquer ferramenta de call sem precisar de uma lista configurada.

Até 25/11/2026 a fonte era o registro (`CapabilityAccessManager\\
ConsentStore\\microphone`, LastUsedTimeStart/Stop). Depois das atualizações
do Windows de 14 e 15/11 ele parou de ser atualizado nesta máquina e o app
deixou de detectar calls, sem erro nenhum.
"""

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, List, Optional

import psutil

from .audio_sessions import CAPTURE, RENDER, init_com, iter_sessions

import comtypes
from pycaw.api.endpointvolume import IAudioMeterInformation

logger = logging.getLogger("voice_recorder")

_SESSION_ACTIVE = 1  # AudioSessionStateActive
# Mesmo limiar de silêncio do PlaybackWatcher.
_PEAK_THRESHOLD = 30.0 / 32767
_WEBVIEW_PROCESS = "msedgewebview2.exe"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Instância própria pra não alterar os argtypes do `ctypes.windll` global.
_kernel32 = ctypes.WinDLL("kernel32")
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.GetPackageFamilyName.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR
]
_kernel32.GetPackageFamilyName.restype = wintypes.LONG


@dataclass
class MicWatcherConfig:
    poll_interval_seconds: float = 1.5


def _package_family_name(pid: int) -> Optional[str]:
    """None se o processo não é de um app empacotado (MSIX/Store)."""
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        length = wintypes.DWORD(256)
        buf = ctypes.create_unicode_buffer(length.value)
        if _kernel32.GetPackageFamilyName(handle, ctypes.byref(length), buf) != 0:
            return None
        return buf.value
    finally:
        _kernel32.CloseHandle(handle)


def _app_process(pid: int) -> psutil.Process:
    """Apps baseados em WebView2 (ex: WhatsApp) podem usar o microfone por um
    `msedgewebview2.exe` filho; quem interessa é o app dono dele."""
    process = psutil.Process(pid)
    if process.name().lower() != _WEBVIEW_PROCESS:
        return process
    for parent in process.parents():
        if parent.name().lower() != _WEBVIEW_PROCESS:
            return parent
    return process


def _app_label(process: psutil.Process) -> str:
    """Nome do pacote (ex: "MSTeams_8wekyb3d8bbwe") ou, pra apps Win32,
    "NonPackaged\\C:#...#app.exe" — o formato que o resto do app já usa e que
    está gravado no banco."""
    family = _package_family_name(process.pid)
    if family:
        return family
    try:
        path = process.exe() or process.name()
    except psutil.Error:
        path = process.name()
    return f"NonPackaged\\{path.replace(chr(92), '#')}"


def is_ignored_app(label: str) -> bool:
    """Apps que nunca contam como call nem como áudio a gravar. Claude: o
    Fabio dita pra ele pelo microfone, e cada ditado abria um pop-up."""
    name = label.lower()
    return name.startswith("claude_") or name.split("#")[-1] == "claude.exe"


def only_ignored_apps_playing(samples: int = 6, interval_seconds: float = 0.25) -> bool:
    """True se há som saindo e ele vem só de apps ignorados. Amostra por
    ~1,5s, porque o medidor é instantâneo. Qualquer falha vira False: o pior
    caso é um pop-up a mais."""
    init_com()
    playing = set()
    try:
        for attempt in range(samples):
            if attempt:
                time.sleep(interval_seconds)
            for pid, control in iter_sessions(RENDER):
                try:
                    peak = control.QueryInterface(IAudioMeterInformation).GetPeakValue()
                    if peak >= _PEAK_THRESHOLD:
                        playing.add(_app_label(_app_process(pid)))
                except (comtypes.COMError, psutil.Error):
                    continue
    except Exception:
        logger.exception("Falha ao verificar quem está tocando áudio.")
        return False
    return bool(playing) and all(is_ignored_app(label) for label in playing)


def get_active_mic_apps() -> List[str]:
    """Retorna os rótulos dos apps com o microfone em uso agora, excluindo
    os ignorados e o próprio processo do Voice Recorder — senão o app nunca
    veria "call encerrada" enquanto está gravando."""
    init_com()
    own_pid = os.getpid()
    own_label = _app_label(psutil.Process(own_pid)).lower()

    labels: List[str] = []
    for pid, control in iter_sessions(CAPTURE):
        try:
            if pid == own_pid or control.GetState() != _SESSION_ACTIVE:
                continue
            label = _app_label(_app_process(pid))
        except (comtypes.COMError, psutil.Error):
            continue  # sessão ou processo sumiu no meio da varredura
        if label.lower() != own_label and not is_ignored_app(label) and label not in labels:
            labels.append(label)
    return labels


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
        # Qualquer excecao aqui antes matava a thread em silencio (o stderr do
        # .exe nao vai pro arquivo de log) e o app deixava de detectar calls ate
        # ser reiniciado. Agora o erro e logado e o loop continua.
        last_logged_apps: List[str] = []
        while not self._stop_event.is_set():
            try:
                active_apps = get_active_mic_apps()
                is_active = len(active_apps) > 0

                if sorted(active_apps) != sorted(last_logged_apps):
                    logger.info("Microfone em uso por: %s", active_apps or "nenhum app")
                    last_logged_apps = list(active_apps)

                if is_active and not self._was_active:
                    self._was_active = is_active
                    self.on_call_start(active_apps)
                elif not is_active and self._was_active:
                    self._was_active = is_active
                    self.on_call_end()
                else:
                    self._was_active = is_active
            except Exception:
                logger.exception("Erro no watcher de microfone; seguindo em frente.")
            self._stop_event.wait(self.config.poll_interval_seconds)
