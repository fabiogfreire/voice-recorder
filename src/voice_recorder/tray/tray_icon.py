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

# Referência ao ícone rodando, pra permitir forçar refresh do menu a
# partir de fora (main.py). O próprio pystray já atualiza o menu sozinho
# quando a ação é disparada por um clique NO menu (`_handler` em
# pystray/_win32.py chama update_menu() depois de cada callback de item)
# — mas o toggle do Modo 2 também é disparado pelo botão "Gravar" da
# notificação toast (playback_notifier.py), fora do menu, e nesse caminho
# o rótulo dinâmico ("Gravar isso" -> "Parar gravação de conteúdo") nunca
# era reavaliado: testado na prática, o menu ficava travado no texto
# antigo mesmo com a gravação ativa (clicar nele funcionava — só o texto
# é que mentia).
_icon: Optional[pystray.Icon] = None


def refresh_menu() -> None:
    """Força o pystray a reavaliar os rótulos dinâmicos do menu. No-op se
    o ícone ainda não subiu (ex: chamado antes de `run_tray_icon`)."""
    if _icon is not None:
        _icon.update_menu()


def _build_icon_image() -> Image.Image:
    """Microfone estilizado em fundo escuro arredondado — mais legível na
    bandeja (e mais "atual") do que uma bolinha vermelha lisa."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((4, 4, 60, 60), radius=16, fill=(30, 32, 36, 255))

    # Cápsula do microfone.
    draw.rounded_rectangle((25, 14, 39, 38), radius=7, fill=(235, 60, 70, 255))

    # Suporte (arco) por baixo da cápsula.
    draw.arc((17, 22, 47, 46), start=0, end=180, fill=(255, 255, 255, 255), width=3)

    # Haste e base.
    draw.line((32, 46, 32, 52), fill=(255, 255, 255, 255), width=3)
    draw.line((23, 52, 41, 52), fill=(255, 255, 255, 255), width=3)

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
    global _icon

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
    _icon = icon
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
