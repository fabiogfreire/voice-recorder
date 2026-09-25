"""Entrypoint único do app: sobe a UI web (FastAPI/uvicorn), o watcher de
microfone (Modo 1) e o ícone da bandeja, tudo no mesmo processo, sempre em
segundo plano — não precisa ser "iniciado" manualmente além de rodar isto
(ou o .exe empacotado) uma vez, idealmente via Inicialização do Windows.
"""

import json
import logging
import threading
import wave
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional, Tuple

import uvicorn

from .db import create_recording, init_db, mark_stale_recordings_as_error, update_recording
from .filenames import sanitize_for_filename
from .notifications import register_app_identity
from .notifications.call_notifier import notify_recording_started
from .notifications.playback_notifier import notify_playback_detected
from .paths import get_app_data_dir, get_recordings_dir
from .transcription.openai_client import has_audio_signal
from .tray.tray_icon import refresh_menu, run_tray_icon
from .watcher.active_window import get_active_window_title
from .watcher.audio_capture import CallRecording, ContentRecording
from .watcher.audio_muter import AudioMuter
from .watcher.mic_watcher import MicWatcher
from .watcher.participants import watch_participants
from .watcher.playback_watcher import PlaybackWatcher
from .web.app import app as web_app


def _configure_logging() -> None:
    """O .exe empacotado roda sem console — sem um handler em arquivo,
    todo log se perde e não há como diagnosticar nada depois do fato."""
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = RotatingFileHandler(
        get_app_data_dir() / "voice-recorder.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


_configure_logging()
logger = logging.getLogger("voice_recorder")

_lock = threading.Lock()
_current_recording: Optional[CallRecording] = None
_current_recording_id: Optional[int] = None

_content_lock = threading.Lock()
_current_content_recording: Optional[ContentRecording] = None
_current_content_recording_id: Optional[int] = None

_audio_muter = AudioMuter()

# Nomes capturados via UI Automation pra call em andamento (ver
# watcher/participants.py) — separado de _current_recording porque a
# captura roda em thread própria, sem correr o risco de atrasar o começo
# da gravação (UI Automation pode levar segundos, e cada frame de áudio
# perdido nesse meio tempo é perdido de verdade).
_participants_lock = threading.Lock()
_current_participants: List[str] = []

# Intervalo do polling contínuo durante a call inteira (não só uma janela
# fixa no início) — testado ao vivo numa call real: a outra pessoa levou
# quase 2 minutos pra entrar (precisou de ajuda por telefone), e uma
# tentativa única logo no início simplesmente não a via. Ver
# watcher/participants.py:watch_participants.
_PARTICIPANTS_POLL_INTERVAL_SECONDS = 8.0


def _derive_source_name(active_apps: List[str]) -> str:
    """Extrai um rótulo legível (ex: "Teams.exe") do identificador bruto do
    registro. Apps Win32 (NonPackaged) ficam com o caminho todo, mas com
    '#' no lugar de barras (ex: "NonPackaged\\C:#Program Files#...#Teams.exe")
    — testado com um app real; sem esse tratamento o nome vira o caminho
    inteiro em vez de só "Teams.exe"."""
    if not active_apps:
        return "Desconhecido"

    label = active_apps[0]
    prefix = "NonPackaged\\"
    if label.startswith(prefix):
        raw_path = label[len(prefix):]
        return raw_path.split("#")[-1] or "Desconhecido"
    return label or "Desconhecido"


def _compute_recording_stats(
    mode: str, mic_path: Optional[Path], loopback_paths: List[Path]
) -> Tuple[Optional[float], int]:
    """Calcula duração e trilhas cobráveis 1x, ao fechar a gravação — não
    a cada page load da UI, que recarrega sozinha a cada 3s
    (SPEC-transcricao-sob-demanda.md). Duração vem só do header do WAV
    (getnframes()/getframerate()), sem ler o áudio; mic e loopback são
    gravados em paralelo e param juntos, então a duração de uma trilha já
    representa a gravação inteira. `billable_tracks` conta quantas
    trilhas têm sinal de verdade (`has_audio_signal`, já em blocos —
    P2 da SPEC.md — pra não estourar memória em WAVs grandes)."""
    tracks = ([mic_path] if mic_path else []) + list(loopback_paths)
    billable_tracks = sum(1 for path in tracks if path.exists() and has_audio_signal(path))

    duration_path = mic_path if mode == "call" else (loopback_paths[0] if loopback_paths else None)
    duration_seconds = None
    if duration_path is not None and duration_path.exists():
        with wave.open(str(duration_path), "rb") as wav_file:
            duration_seconds = wav_file.getnframes() / wav_file.getframerate()

    return duration_seconds, billable_tracks


def _capture_participants_async(source_app: str, recording_id: int) -> None:
    """Roda em thread própria — nunca no caminho crítico de começar a
    gravar (ver comentário de `_current_participants`). Faz polling
    durante a call inteira (`watch_participants`), atualizando
    `_current_participants` a cada nome novo encontrado — não dá pra
    esperar o polling acabar pra escrever o resultado: `on_call_end` lê
    `_current_participants` assim que a call termina, então a escrita
    tem que já estar feita antes disso, não depois."""
    global _current_participants

    def _should_continue() -> bool:
        return _current_recording_id == recording_id

    def _on_update(names: List[str]) -> None:
        global _current_participants
        with _participants_lock:
            _current_participants = names
        logger.info("Participantes identificados: %s.", ", ".join(names))

    watch_participants(
        source_app,
        should_continue=_should_continue,
        on_update=_on_update,
        interval_seconds=_PARTICIPANTS_POLL_INTERVAL_SECONDS,
    )


def on_call_start(active_apps: List[str]) -> None:
    global _current_recording, _current_recording_id, _current_participants

    with _lock:
        if _current_recording is not None:
            return

        _audio_muter.mute_other_apps(active_apps)

        source_name = _derive_source_name(active_apps)
        started_at = datetime.now()
        base_name = f"{started_at.strftime('%Y-%m-%d_%Hh%M')}_{source_name}"
        mic_path = get_recordings_dir() / f"{base_name}_mic.wav"

        recording_id = create_recording(
            mode="call", source_app=source_name, started_at=started_at.isoformat()
        )
        recording = CallRecording(mic_path, get_recordings_dir(), base_name)
        recording.start()

        _current_recording = recording
        _current_recording_id = recording_id

        with _participants_lock:
            _current_participants = []
        threading.Thread(
            target=_capture_participants_async,
            args=(source_name, recording_id),
            daemon=True,
        ).start()

    logger.info("Call detectada (%s). Gravando mic+loopback separados.", source_name)
    notify_recording_started(source_name, on_discard=on_discard_current)


def on_call_end() -> None:
    global _current_recording, _current_recording_id

    with _lock:
        recording, recording_id = _current_recording, _current_recording_id
        _current_recording, _current_recording_id = None, None

    _audio_muter.restore_muted_apps()

    if recording is None:
        return

    recording.stop()
    duration_seconds, billable_tracks = _compute_recording_stats(
        "call", recording.mic_path, recording.loopback_paths
    )
    with _participants_lock:
        participants = _current_participants
    update_recording(
        recording_id,
        ended_at=datetime.now().isoformat(),
        status="recorded",
        mic_path=str(recording.mic_path),
        loopback_path=json.dumps([str(p) for p in recording.loopback_paths]),
        duration_seconds=duration_seconds,
        billable_tracks=billable_tracks,
        participants=json.dumps(participants),
    )
    logger.info("Call encerrada. Gravação #%s salva (aguardando decisão de transcrever).", recording_id)


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


def is_call_recording() -> bool:
    return _current_recording is not None


def on_playback_detected() -> None:
    """Áudio tocando sem nenhuma gravação em andamento — notifica com
    opção de gravar (opt-in: nem todo som que toca no PC merece virar
    transcrição, por isso não grava direto como no Modo 1).

    Verifica de novo se tem gravação ativa antes de notificar, pra evitar
    disparar a notificação redundantemente se o usuário iniciou uma gravação
    entre checagens do watcher."""
    if is_call_recording() or is_content_recording():
        return
    logger.info("Áudio detectado tocando. Notificando opção de gravar.")
    notify_playback_detected(on_record=on_content_toggle)


def on_content_toggle() -> None:
    """Liga/desliga a gravação de conteúdo (Modo 2) — disparado pelo item
    "Gravar isso" da bandeja. Só a trilha de loopback, sem tag Fabio/Outros
    nem botão de descarte, já que quem inicia é o próprio Fabio de
    propósito."""
    global _current_content_recording, _current_content_recording_id

    with _content_lock:
        if _current_content_recording is None:
            source_name = sanitize_for_filename(get_active_window_title() or "Conteúdo")
            started_at = datetime.now()
            base_name = f"{started_at.strftime('%Y-%m-%d_%Hh%M')}_{source_name}"

            recording_id = create_recording(
                mode="content", source_app=source_name, started_at=started_at.isoformat()
            )
            recording = ContentRecording(get_recordings_dir(), base_name)
            recording.start()

            _current_content_recording = recording
            _current_content_recording_id = recording_id
            logger.info("Gravação de conteúdo iniciada (%s).", source_name)
            refresh_menu()
            return

        recording = _current_content_recording
        recording_id = _current_content_recording_id
        _current_content_recording = None
        _current_content_recording_id = None

    recording.stop()
    duration_seconds, billable_tracks = _compute_recording_stats(
        "content", None, recording.loopback_paths
    )
    update_recording(
        recording_id,
        ended_at=datetime.now().isoformat(),
        status="recorded",
        loopback_path=json.dumps([str(p) for p in recording.loopback_paths]),
        duration_seconds=duration_seconds,
        billable_tracks=billable_tracks,
    )
    logger.info("Gravação de conteúdo #%s salva (aguardando decisão de transcrever).", recording_id)
    refresh_menu()


def start_web_server() -> None:
    uvicorn.run(
        web_app,
        host="127.0.0.1",
        port=8000,
        log_level="warning",
    )


def main() -> None:
    register_app_identity()
    init_db()
    mark_stale_recordings_as_error()

    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()

    mic_watcher = MicWatcher(on_call_start=on_call_start, on_call_end=on_call_end)
    mic_watcher.start()

    playback_watcher = PlaybackWatcher(
        on_playback_start=on_playback_detected,
        should_check=lambda: not is_call_recording() and not is_content_recording(),
    )
    playback_watcher.start()

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
