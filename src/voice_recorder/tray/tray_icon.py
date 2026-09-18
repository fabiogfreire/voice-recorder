"""Ícone na bandeja do sistema (pystray) com o menu básico do app.

Menu completo previsto no PRD (Abrir UI / Gravar isso / Descartar gravação
atual / Pausar detecção / Sair) — falta só "Pausar detecção".
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


def run_tray_icon(
    on_discard: Optional[Callable[[], None]] = None,
    on_toggle_content: Optional[Callable[[], None]] = None,
    is_content_recording: Optional[Callable[[], bool]] = None,
) -> None:
    """Bloqueia a thread atual rodando o loop do ícone — deve ser chamado
    numa thread dedicada (a UI/watcher já rodam nas suas próprias).

    `on_discard` é chamado ao clicar em "Descartar gravação atual" — mesmo
    controle de opt-out da notificação de call, disponível o tempo todo.
    `on_toggle_content`/`is_content_recording` controlam o Modo 2 (manual):
    o mesmo item liga e desliga a gravação de conteúdo/aula, trocando de
    rótulo conforme o estado."""

    def _discard(icon, item) -> None:
        if on_discard:
            on_discard()

    def _toggle_content(icon, item) -> None:
        if on_toggle_content:
            on_toggle_content()

    def _content_label(item) -> str:
        if is_content_recording and is_content_recording():
            return "Parar gravação de conteúdo"
        return "Gravar isso (conteúdo/aula)"

    icon = pystray.Icon(
        "voice-recorder",
        _build_icon_image(),
        "Voice Recorder",
        menu=pystray.Menu(
            pystray.MenuItem("Abrir UI", _open_ui, default=True),
            pystray.MenuItem(_content_label, _toggle_content),
            pystray.MenuItem("Descartar gravação atual", _discard),
            pystray.MenuItem("Sair", _quit),
        ),
    )
    icon.run()


def start_tray_icon_in_background(
    on_discard: Optional[Callable[[], None]] = None,
    on_toggle_content: Optional[Callable[[], None]] = None,
    is_content_recording: Optional[Callable[[], bool]] = None,
) -> threading.Thread:
    thread = threading.Thread(
        target=run_tray_icon,
        args=(on_discard, on_toggle_content, is_content_recording),
        daemon=True,
    )
    thread.start()
    return thread
