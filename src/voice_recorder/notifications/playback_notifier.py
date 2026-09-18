"""Notificação interativa exibida quando o Modo 2 detecta áudio tocando
sem gravação em andamento — opt-in (ao contrário da call, que já grava
direto), porque nem todo som no PC merece virar transcrição. Existe pra
não depender só do Fabio lembrar de clicar em "Gravar isso" na bandeja.

Botão "Não" é só uma forma explícita de dispensar (o PlaybackWatcher já
não notifica de novo enquanto o áudio continuar tocando, mesmo sem
clicar em nada — ver silence_reset_seconds)."""

import logging
from typing import Callable

from windows_toasts import InteractableWindowsToaster, Toast, ToastActivatedEventArgs, ToastButton

APP_NAME = "Voice Recorder"
_RECORD_ARGUMENT = "record"
_DECLINE_ARGUMENT = "decline"

logger = logging.getLogger("voice_recorder")
_toaster = InteractableWindowsToaster(APP_NAME)


def notify_playback_detected(on_record: Callable[[], None]) -> None:
    def _on_activated(args: ToastActivatedEventArgs) -> None:
        if args.arguments == _RECORD_ARGUMENT:
            on_record()
        elif args.arguments == _DECLINE_ARGUMENT:
            logger.info("Notificação de áudio detectado recusada.")

    toast = Toast(
        [
            "Áudio detectado",
            'Tem algo tocando. Quer gravar pra transcrever depois?',
        ],
        actions=[
            ToastButton("Gravar", arguments=_RECORD_ARGUMENT),
            ToastButton("Não", arguments=_DECLINE_ARGUMENT),
        ],
        on_activated=_on_activated,
    )
    _toaster.show_toast(toast)
