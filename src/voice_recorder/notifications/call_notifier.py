"""Notificação interativa do Windows exibida assim que uma call é detectada,
com o botão "Não gravar" (opt-out) — mesma ação do item "Descartar gravação
atual" da bandeja, só que disponível já na primeira notificação, conforme o
PRD (por padrão o sistema grava; é o Fabio quem avisa quando não é pra
gravar)."""

from typing import Callable

from windows_toasts import InteractableWindowsToaster, Toast, ToastActivatedEventArgs, ToastButton

APP_NAME = "Voice Recorder"
_DISCARD_ARGUMENT = "discard"

_toaster = InteractableWindowsToaster(APP_NAME)


def notify_recording_started(source_name: str, on_discard: Callable[[], None]) -> None:
    def _on_activated(args: ToastActivatedEventArgs) -> None:
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
