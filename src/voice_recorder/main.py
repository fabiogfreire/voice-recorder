"""Entrypoint único do app: sobe a UI web (FastAPI/uvicorn), o watcher de
microfone (Modo 1) e o ícone da bandeja, tudo no mesmo processo, sempre em
segundo plano — não precisa ser "iniciado" manualmente além de rodar isto
(ou o .exe empacotado) uma vez, idealmente via Inicialização do Windows.
"""

import logging
import threading

import uvicorn

from .db import init_db
from .tray.tray_icon import run_tray_icon
from .watcher.mic_watcher import MicWatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("voice_recorder")


def on_call_start(active_apps: list[str]) -> None:
    # TODO: disparar notificação com botão "Não gravar" e iniciar a captura
    # de áudio (mic + loopback) assim que o módulo de gravação existir.
    logger.info("Call detectada. Apps usando o microfone: %s", active_apps)


def on_call_end() -> None:
    # TODO: encerrar a captura de áudio em andamento e enfileirar para
    # transcrição, quando o módulo de gravação existir.
    logger.info("Call encerrada (microfone parou de ser usado).")


def start_web_server() -> None:
    uvicorn.run(
        "voice_recorder.web.app:app",
        host="127.0.0.1",
        port=8000,
        log_level="warning",
    )


def main() -> None:
    init_db()

    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()

    mic_watcher = MicWatcher(on_call_start=on_call_start, on_call_end=on_call_end)
    mic_watcher.start()

    logger.info("Voice Recorder rodando. UI em http://localhost:8000")

    # O ícone da bandeja bloqueia a thread principal — web server e watcher
    # já rodam nas suas próprias threads em background.
    run_tray_icon()


if __name__ == "__main__":
    main()
