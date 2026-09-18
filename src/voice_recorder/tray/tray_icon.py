"""Ícone na bandeja do sistema (pystray) com o menu básico do app.

Menu completo previsto no PRD (Abrir UI / Gravar isso / Descartar gravação
atual / Pausar detecção / Sair) — "Gravar isso" (Modo 2) e "Pausar
detecção" ainda entram numa próxima leva.
"""

import threading
import webbrowser
from typing import Callable, Optional

import pystray
from PIL import Image, ImageDraw

UI_URL = "http://localhost:8000"


def _build_icon_image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill=(200, 40, 40, 255))
    return image


def _open_ui(icon, item) -> None:
    webbrowser.open(UI_URL)


def _quit(icon, item) -> None:
    icon.stop()


def run_tray_icon(on_discard: Optional[Callable[[], None]] = None) -> None:
    """Bloqueia a thread atual rodando o loop do ícone — deve ser chamado
    numa thread dedicada (a UI/watcher já rodam nas suas próprias).

    `on_discard` é chamado ao clicar em "Descartar gravação atual" — é o
    mesmo controle de opt-out da notificação (ainda não implementada),
    disponível o tempo todo durante uma call, conforme o PRD."""

    def _discard(icon, item) -> None:
        if on_discard:
            on_discard()

    icon = pystray.Icon(
        "voice-recorder",
        _build_icon_image(),
        "Voice Recorder",
        menu=pystray.Menu(
            pystray.MenuItem("Abrir UI", _open_ui, default=True),
            pystray.MenuItem("Descartar gravação atual", _discard),
            pystray.MenuItem("Sair", _quit),
        ),
    )
    icon.run()


def start_tray_icon_in_background(
    on_discard: Optional[Callable[[], None]] = None,
) -> threading.Thread:
    thread = threading.Thread(target=run_tray_icon, args=(on_discard,), daemon=True)
    thread.start()
    return thread
