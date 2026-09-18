"""Ícone na bandeja do sistema (pystray) com o menu básico do app.

Menu completo previsto no PRD (Abrir UI / Gravar isso / Descartar gravação
atual / Pausar detecção / Sair) — "Gravar isso" (Modo 2) e "Descartar
gravação atual" entram junto com o módulo de captura de áudio, ainda não
implementado nesta primeira leva.
"""

import threading
import webbrowser

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


def run_tray_icon() -> None:
    """Bloqueia a thread atual rodando o loop do ícone — deve ser chamado
    numa thread dedicada (a UI/watcher já rodam nas suas próprias)."""
    icon = pystray.Icon(
        "voice-recorder",
        _build_icon_image(),
        "Voice Recorder",
        menu=pystray.Menu(
            pystray.MenuItem("Abrir UI", _open_ui, default=True),
            pystray.MenuItem("Sair", _quit),
        ),
    )
    icon.run()


def start_tray_icon_in_background() -> threading.Thread:
    thread = threading.Thread(target=run_tray_icon, daemon=True)
    thread.start()
    return thread
