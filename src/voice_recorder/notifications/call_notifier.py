"""Notificação interativa do Windows exibida assim que uma call é detectada,
com o botão "Não gravar" (opt-out) — mesma ação do item "Descartar gravação
atual" da bandeja, só que disponível já na primeira notificação, conforme o
PRD (por padrão o sistema grava; é o Fabio quem avisa quando não é pra
gravar)."""

import logging
from typing import Callable

from windows_toasts import InteractableWindowsToaster, Toast, ToastActivatedEventArgs, ToastButton


logger = logging.getLogger("voice_recorder")

APP_NAME = "Voice Recorder"
_DISCARD_ARGUMENT = "discard"

# Sem notifierAUMID de propósito — ver notifications/__init__.py.
_toaster = InteractableWindowsToaster(APP_NAME)


def notify_recording_started(source_name: str, on_discard: Callable[[], None]) -> None:
    def _on_activated(args: ToastActivatedEventArgs) -> None:
        # Log incondicional (não só quando bate com discard): caso #74 —
        # gravação descartada ~1s depois de detectada, sem o Fabio ter
        # clicado em nada — se essa notificação disparar de novo com um
        # argumento inesperado, isso fica registrado em vez de
        # silenciosamente ignorado.
        logger.info("Notificação de call ativada com argumento: %r.", args.arguments)
        if args.arguments == _DISCARD_ARGUMENT:
            on_discard()

    toast = Toast(
        [
            "Gravando chamada",
            f"Origem: {source_name}. Clique em \"Não gravar\" pra descartar.",
        ],
        actions=[ToastButton("Não gravar", arguments=_DISCARD_ARGUMENT)],
        on_activated=_on_activated,
    )
    _toaster.show_toast(toast)
