"""Pop-up fixo exibido assim que uma call é detectada: "Gravar e transcrever
esta conversa?". A gravação já começou quando ele aparece (esperar o "Sim"
perderia o começo da conversa); a resposta só decide o destino dela
(SPEC-popup-gravar-transcrever.md)."""

import logging
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

logger = logging.getLogger("voice_recorder")

APP_NAME = "Voice Recorder"
_YES_ARGUMENT = "yes"
_NO_ARGUMENT = "no"

# Sem notifierAUMID de propósito — ver notifications/__init__.py.
_toaster = InteractableWindowsToaster(APP_NAME)


def ask_record_and_transcribe_call(
    source_name: str,
    recording_id: int,
    on_yes: Callable[[int], None],
    on_no: Callable[[int], None],
) -> None:
    """`on_yes`/`on_no` recebem o `recording_id` deste pop-up. Ele fica na
    tela até a resposta, então pode ser respondido quando já existe outra
    call gravando — o clique nunca pode valer pra "gravação atual"."""

    # Os callbacks rodam numa thread do WinRT: sem o try/except, uma exceção
    # ali não chegaria ao arquivo de log.
    def _on_activated(args: ToastActivatedEventArgs) -> None:
        # Log incondicional (não só quando bate com uma ação esperada): caso
        # #74 — gravação descartada ~1s depois de detectada, sem o Fabio ter
        # clicado em nada. Se essa notificação disparar de novo com um
        # argumento inesperado, isso fica registrado.
        logger.info(
            "Notificação de call ativada com argumento: %r (gravação #%s).",
            args.arguments, recording_id,
        )
        try:
            if args.arguments == _YES_ARGUMENT:
                on_yes(recording_id)
            elif args.arguments == _NO_ARGUMENT:
                on_no(recording_id)
        except Exception:
            logger.exception("Falha ao aplicar a resposta do pop-up da gravação #%s.", recording_id)

    def _on_dismissed(args: ToastDismissedEventArgs) -> None:
        logger.info(
            "Pop-up da gravação #%s fechado sem resposta (%s).",
            recording_id, getattr(args.reason, "name", args.reason),
        )

    def _on_failed(args: ToastFailedEventArgs) -> None:
        logger.warning(
            "Pop-up da gravação #%s falhou ao ser exibido (%s).", recording_id, args.error_code
        )

    toast = Toast(
        [
            f"Call detectada — {source_name}",
            "Gravar e transcrever esta conversa?",
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
    _toaster.show_toast(toast)
