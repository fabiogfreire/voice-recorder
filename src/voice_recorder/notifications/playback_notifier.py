"""Notificação interativa exibida quando o Modo 2 detecta áudio tocando
sem gravação em andamento — opt-in (ao contrário da call, que já grava
direto), porque nem todo som no PC merece virar transcrição. Existe pra
não depender só do Fabio lembrar de clicar em "Gravar isso" na bandeja."""

from typing import Callable

from windows_toasts import InteractableWindowsToaster, Toast, ToastActivatedEventArgs, ToastButton

APP_NAME = "Voice Recorder"
_RECORD_ARGUMENT = "record"

_toaster = InteractableWindowsToaster(APP_NAME)


def notify_playback_detected(on_record: Callable[[], None]) -> None:
    def _on_activated(args: ToastActivatedEventArgs) -> None:
        if args.arguments == _RECORD_ARGUMENT:
            on_record()

    toast = Toast(
        [
            "Áudio detectado",
            'Tem algo tocando. Clique em "Gravar" se quiser transcrever depois.',
        ],
        actions=[ToastButton("Gravar", arguments=_RECORD_ARGUMENT)],
        on_activated=_on_activated,
    )
    _toaster.show_toast(toast)
