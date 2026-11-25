"""Pop-up fixo exibido quando o Modo 2 detecta áudio tocando sem gravação em
andamento — opt-in (ao contrário da call, que já grava direto), porque nem
todo som no PC merece virar transcrição. Existe pra não depender só do Fabio
lembrar de clicar em "Gravar isso" na bandeja
(SPEC-popup-gravar-transcrever.md).

Como o pop-up fica na tela até a resposta, só existe um por vez: o
PlaybackWatcher volta a detectar áudio depois de um silêncio, e sem essa
trava os pop-ups se empilhariam."""

import logging
import threading
from typing import Callable

from windows_toasts import (
    InteractableWindowsToaster,
    Toast,
    ToastActivatedEventArgs,
    ToastButton,
    ToastDismissedEventArgs,
    ToastFailedEventArgs,
    ToastScenario,
)

APP_NAME = "Voice Recorder"
_YES_ARGUMENT = "yes"
_NO_ARGUMENT = "no"

logger = logging.getLogger("voice_recorder")
# Sem notifierAUMID de propósito — ver notifications/__init__.py.
_toaster = InteractableWindowsToaster(APP_NAME)

_pending_lock = threading.Lock()
_pending = False


def _set_pending(value: bool) -> None:
    global _pending
    with _pending_lock:
        _pending = value


def notify_playback_detected(on_yes: Callable[[], None], on_no: Callable[[], None]) -> None:
    global _pending
    with _pending_lock:
        if _pending:
            logger.info("Já há um pop-up de áudio sem resposta; não mostrando outro.")
            return
        _pending = True

    # Qualquer ativação (inclusive clique no corpo) ou fechamento tira o
    # pop-up da tela, então libera o próximo.
    def _on_activated(args: ToastActivatedEventArgs) -> None:
        logger.info("Notificação de áudio ativada com argumento: %r.", args.arguments)
        _set_pending(False)
        try:
            if args.arguments == _YES_ARGUMENT:
                on_yes()
            elif args.arguments == _NO_ARGUMENT:
                on_no()
        except Exception:
            logger.exception("Falha ao aplicar a resposta do pop-up de áudio.")

    def _on_dismissed(args: ToastDismissedEventArgs) -> None:
        _set_pending(False)
        logger.info(
            "Pop-up de áudio fechado sem resposta (%s).",
            getattr(args.reason, "name", args.reason),
        )

    def _on_failed(args: ToastFailedEventArgs) -> None:
        _set_pending(False)
        logger.warning("Pop-up de áudio falhou ao ser exibido (%s).", args.error_code)

    toast = Toast(
        [
            "Áudio detectado",
            "Gravar e transcrever o que está tocando?",
        ],
        # Reminder: não expira sozinho, só sai por um botão ou pelo X.
        scenario=ToastScenario.Reminder,
        actions=[
            ToastButton("Sim", arguments=_YES_ARGUMENT),
            ToastButton("Não", arguments=_NO_ARGUMENT),
        ],
        on_activated=_on_activated,
        on_dismissed=_on_dismissed,
        on_failed=_on_failed,
    )
    try:
        _toaster.show_toast(toast)
    except Exception:
        _set_pending(False)
        raise
