"""Entrypoint único do app: sobe a UI web (FastAPI/uvicorn), o watcher de
microfone (Modo 1) e o ícone da bandeja, tudo no mesmo processo, sempre em
segundo plano — não precisa ser "iniciado" manualmente além de rodar isto
(ou o .exe empacotado) uma vez, idealmente via Inicialização do Windows.
"""

import logging
import re
import threading
from datetime import datetime
from typing import List, Optional

import uvicorn

from .db import create_recording, init_db, update_recording
from .notifications.call_notifier import notify_recording_started
from .paths import get_recordings_dir
from .transcription.worker import enqueue_transcription
from .tray.tray_icon import run_tray_icon
from .watcher.active_window import get_active_window_title
from .watcher.audio_capture import CallRecording, ContentRecording
from .watcher.mic_watcher import MicWatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("voice_recorder")

_lock = threading.Lock()
_current_recording: Optional[CallRecording] = None
_current_recording_id: Optional[int] = None

_content_lock = threading.Lock()
_current_content_recording: Optional[ContentRecording] = None
_current_content_recording_id: Optional[int] = None

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def _sanitize_for_filename(text: str, max_length: int = 60) -> str:
    return _INVALID_FILENAME_CHARS.sub("_", text).strip()[:max_length] or "Desconhecido"


def _derive_source_name(active_apps: List[str]) -> str:
    """Extrai um rótulo legível (ex: "Teams.exe") do identificador bruto do
    registro (ex: "NonPackaged\\C:\\...\\Teams.exe")."""
    if not active_apps:
        return "Desconhecido"
    return active_apps[0].split("\\")[-1] or "Desconhecido"


def on_call_start(active_apps: List[str]) -> None:
    global _current_recording, _current_recording_id

    with _lock:
        if _current_recording is not None:
            return

        source_name = _derive_source_name(active_apps)
        started_at = datetime.now()
        base_name = f"{started_at.strftime('%Y-%m-%d_%Hh%M')}_{source_name}"
        mic_path = get_recordings_dir() / f"{base_name}_mic.wav"
        loopback_path = get_recordings_dir() / f"{base_name}_loopback.wav"

        recording_id = create_recording(
            mode="call", source_app=source_name, started_at=started_at.isoformat()
        )
        recording = CallRecording(mic_path, loopback_path)
        recording.start()

        _current_recording = recording
        _current_recording_id = recording_id

    logger.info("Call detectada (%s). Gravando mic+loopback separados.", source_name)
    notify_recording_started(source_name, on_discard=on_discard_current)


def on_call_end() -> None:
    global _current_recording, _current_recording_id

    with _lock:
        recording, recording_id = _current_recording, _current_recording_id
        _current_recording, _current_recording_id = None, None

    if recording is None:
        return

    recording.stop()
    update_recording(
        recording_id,
        ended_at=datetime.now().isoformat(),
        status="recorded",
        mic_path=str(recording.mic_path),
        loopback_path=str(recording.loopback_path),
    )
    logger.info("Call encerrada. Gravação #%s salva.", recording_id)
    enqueue_transcription(recording_id)


def on_discard_current() -> None:
    global _current_recording, _current_recording_id

    with _lock:
        recording, recording_id = _current_recording, _current_recording_id
        _current_recording, _current_recording_id = None, None

    if recording is None:
        logger.info("Nenhuma gravação em andamento pra descartar.")
        return

    recording.discard()
    update_recording(
        recording_id, status="discarded", ended_at=datetime.now().isoformat()
    )
    logger.info("Gravação #%s descartada.", recording_id)


def is_content_recording() -> bool:
    return _current_content_recording is not None


def on_content_toggle() -> None:
    """Liga/desliga a gravação de conteúdo (Modo 2) — disparado pelo item
    "Gravar isso" da bandeja. Só a trilha de loopback, sem tag Fabio/Outros
    nem botão de descarte, já que quem inicia é o próprio Fabio de
    propósito."""
    global _current_content_recording, _current_content_recording_id

    with _content_lock:
        if _current_content_recording is None:
            source_name = _sanitize_for_filename(get_active_window_title() or "Conteúdo")
            started_at = datetime.now()
            base_name = f"{started_at.strftime('%Y-%m-%d_%Hh%M')}_{source_name}"
            loopback_path = get_recordings_dir() / f"{base_name}_loopback.wav"

            recording_id = create_recording(
                mode="content", source_app=source_name, started_at=started_at.isoformat()
            )
            recording = ContentRecording(loopback_path)
            recording.start()

            _current_content_recording = recording
            _current_content_recording_id = recording_id
            logger.info("Gravação de conteúdo iniciada (%s).", source_name)
            return

        recording = _current_content_recording
        recording_id = _current_content_recording_id
        _current_content_recording = None
        _current_content_recording_id = None

    recording.stop()
    update_recording(
        recording_id,
        ended_at=datetime.now().isoformat(),
        status="recorded",
        loopback_path=str(recording.loopback_path),
    )
    logger.info("Gravação de conteúdo #%s salva.", recording_id)
    enqueue_transcription(recording_id)


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
    run_tray_icon(
        on_discard=on_discard_current,
        on_toggle_content=on_content_toggle,
        is_content_recording=is_content_recording,
    )


if __name__ == "__main__":
    main()
