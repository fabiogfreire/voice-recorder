"""Entrypoint único do app: sobe a UI web (FastAPI/uvicorn), o watcher de
microfone (Modo 1) e o ícone da bandeja, tudo no mesmo processo, sempre em
segundo plano — não precisa ser "iniciado" manualmente além de rodar isto
(ou o .exe empacotado) uma vez, idealmente via Inicialização do Windows.
"""

import json
import logging
import threading
import time
import wave
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional, Set, Tuple

import uvicorn

from .db import (
    create_recording,
    get_recording,
    init_db,
    mark_stale_recordings_as_error,
    update_recording,
)
from .filenames import sanitize_for_filename
from .notifications.call_notifier import ask_record_and_transcribe_call
from .notifications.playback_notifier import notify_playback_detected
from .paths import get_app_data_dir, get_recordings_dir
from .transcription.openai_client import has_audio_signal
from .transcription.worker import enqueue_transcription
from .tray.tray_icon import refresh_menu, run_tray_icon
from .watcher.active_window import get_active_window_title
from .watcher.audio_capture import CallRecording, ContentRecording
from .watcher.audio_muter import AudioMuter
from .watcher.mic_watcher import MicWatcher, get_active_mic_apps, only_ignored_apps_playing
from .watcher.participants import watch_participants
from .watcher.playback_watcher import PlaybackWatcher
from .watcher.teams import is_teams_mic_label, is_teams_playing
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

# Áudio do Teams fora de call grava sem pop-up, então precisa se encerrar
# sozinho: para depois deste tempo sem som do Teams. `_teams_episode_active`
# dura até esse silêncio mesmo que o Fabio pare ou descarte antes — senão a
# gravação recomeçaria poucos segundos depois. Protegidos por `_content_lock`.
_TEAMS_SILENCE_SECONDS = 120.0
_TEAMS_POLL_SECONDS = 5.0
# Gravação de áudio do Teams mais nova que isto quando a call começa é só o
# toque de chamada.
_TEAMS_RINGING_SECONDS = 60.0
_teams_content_id: Optional[int] = None
_teams_content_started = 0.0
_teams_episode_active = False

_audio_muter = AudioMuter()

# Respostas dos pop-ups que só podem ser aplicadas quando a gravação fechar
# (SPEC-popup-gravar-transcrever.md). Em memória de propósito: se o app cair no
# meio, mark_stale_recordings_as_error() já cobre a gravação. O lock também
# cobre a leitura do status junto com a escrita no set, pra uma resposta não se
# perder se chegar enquanto a gravação está sendo salva.
_answers_lock = threading.Lock()
_auto_transcribe_ids: Set[int] = set()
_pending_discard_ids: Set[int] = set()

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

        # Falha ao mutar outros apps nunca pode impedir a gravacao da call.
        try:
            _audio_muter.mute_other_apps(active_apps)
        except Exception:
            logger.exception("Falha ao mutar outros apps; gravando a call mesmo assim.")

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
    if any(is_teams_mic_label(app) for app in active_apps):
        with _answers_lock:
            _auto_transcribe_ids.add(recording_id)
        logger.info("Call do Teams: gravação #%s será transcrita ao fim, sem pop-up.", recording_id)
        _close_teams_audio_for_call()
        return
    ask_record_and_transcribe_call(
        source_name, recording_id, on_yes=on_call_yes, on_no=on_call_no
    )


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
    logger.info("Call encerrada. Gravação #%s salva.", recording_id)
    _apply_popup_answer(recording_id)


def _discard_current(only_id: Optional[int] = None) -> bool:
    """Descarta a call em andamento. Com `only_id`, só se ela for essa
    gravação — a comparação acontece dentro do mesmo lock que a retira de
    "atual", pra não descartar uma call que tenha começado no meio tempo."""
    global _current_recording, _current_recording_id

    with _lock:
        if _current_recording is None:
            return False
        if only_id is not None and _current_recording_id != only_id:
            return False
        recording, recording_id = _current_recording, _current_recording_id
        _current_recording, _current_recording_id = None, None

    recording.discard()
    update_recording(
        recording_id, status="discarded", ended_at=datetime.now().isoformat()
    )
    with _answers_lock:
        _auto_transcribe_ids.discard(recording_id)
    logger.info("Gravação #%s descartada.", recording_id)
    return True


def on_discard_current() -> None:
    """Item "Descartar gravação atual" da bandeja: vale pra call em andamento
    e, sem call, pra gravação de áudio do Teams, que começa sem pop-up."""
    if _discard_current():
        return
    if _teams_content_id is not None and _discard_content_recording(_teams_content_id):
        return
    logger.info("Nenhuma gravação em andamento pra descartar.")


def _discard_saved_recording(recording_id: int) -> None:
    """Descarta uma call já encerrada: apaga os áudios daquele registro."""
    row = get_recording(recording_id)
    if row is None:
        return

    paths = [row["mic_path"]] if row["mic_path"] else []
    if row["loopback_path"]:
        paths.extend(json.loads(row["loopback_path"]))
    for path in paths:
        Path(path).unlink(missing_ok=True)

    update_recording(recording_id, status="discarded")
    logger.info("Gravação #%s descartada.", recording_id)


def on_call_yes(recording_id: int) -> None:
    """"Sim" no pop-up da call: transcrever ao fim, ou já, se ela acabou."""
    with _answers_lock:
        row = get_recording(recording_id)
        status = row["status"] if row else None
        if status == "recording":
            _auto_transcribe_ids.add(recording_id)

    if status == "recording":
        logger.info("Gravação #%s será transcrita ao fim da call.", recording_id)
    elif status == "recorded":
        logger.info("Gravação #%s já encerrada; transcrevendo agora.", recording_id)
        enqueue_transcription(recording_id)
    else:
        logger.info("\"Sim\" ignorado: gravação #%s está com status %s.", recording_id, status)


def on_call_no(recording_id: int) -> None:
    """"Não" no pop-up da call: descarta aquela gravação, e só ela."""
    if _discard_current(only_id=recording_id):
        return

    with _answers_lock:
        _auto_transcribe_ids.discard(recording_id)
        row = get_recording(recording_id)
        status = row["status"] if row else None
        if status == "recording":
            # A call acabou de terminar e ainda está sendo salva.
            _pending_discard_ids.add(recording_id)

    if status == "recording":
        logger.info("Gravação #%s será descartada assim que for salva.", recording_id)
    elif status in ("recorded", "error"):
        _discard_saved_recording(recording_id)
    else:
        logger.info("\"Não\" ignorado: gravação #%s está com status %s.", recording_id, status)


def _apply_popup_answer(recording_id: int) -> None:
    """Chamar logo depois de a gravação virar `recorded`. Sem resposta no
    pop-up, não faz nada: a decisão fica com a UI."""
    with _answers_lock:
        discard = recording_id in _pending_discard_ids
        transcribe = recording_id in _auto_transcribe_ids
        _pending_discard_ids.discard(recording_id)
        _auto_transcribe_ids.discard(recording_id)

    if discard:
        _discard_saved_recording(recording_id)
    elif transcribe:
        logger.info("Gravação #%s: iniciando a transcrição automática.", recording_id)
        enqueue_transcription(recording_id)


def is_content_recording() -> bool:
    return _current_content_recording is not None


def is_call_recording() -> bool:
    return _current_recording is not None


def on_playback_detected() -> None:
    """Áudio tocando sem nenhuma gravação em andamento — pergunta se é pra
    gravar (opt-in: nem todo som que toca no PC merece virar transcrição,
    por isso não grava direto como no Modo 1).

    Verifica de novo se tem gravação ativa antes de notificar, pra evitar
    disparar a notificação redundantemente se o usuário iniciou uma gravação
    entre checagens do watcher."""
    if is_call_recording() or is_content_recording():
        return
    if is_teams_playing():
        _start_teams_audio_recording()
        return
    if only_ignored_apps_playing():
        logger.info("Áudio só de app ignorado; sem pop-up.")
        return
    logger.info("Áudio detectado tocando. Perguntando se é pra gravar.")
    notify_playback_detected(on_yes=on_playback_yes, on_no=on_playback_no)


def on_playback_yes() -> None:
    """"Sim" no pop-up de áudio: grava, já marcado pra transcrever ao parar.
    Não usa o toggle: o pop-up pode ser respondido com uma gravação de
    conteúdo já iniciada pela bandeja, e o toggle a pararia."""
    with _content_lock:
        if _current_content_recording is None:
            _start_content_recording()
        recording_id = _current_content_recording_id
        with _answers_lock:
            _auto_transcribe_ids.add(recording_id)
    logger.info("Gravação de conteúdo #%s será transcrita ao parar.", recording_id)


def on_playback_no() -> None:
    logger.info("Pop-up de áudio recusado; nada será gravado.")


def _start_teams_audio_recording() -> None:
    """Teams tocando áudio: grava já marcado pra transcrever, sem pop-up."""
    global _teams_content_id, _teams_content_started, _teams_episode_active

    # Com o Teams no microfone é uma call dele (ex: descartada há pouco pela
    # bandeja): quem cuida dela é on_call_start, não pode regravar por aqui.
    if any(is_teams_mic_label(app) for app in get_active_mic_apps()):
        return

    with _content_lock:
        if _teams_episode_active or _current_content_recording is not None:
            return
        _start_content_recording("Teams")
        recording_id = _current_content_recording_id
        _teams_content_id = recording_id
        _teams_content_started = time.monotonic()
        _teams_episode_active = True
        with _answers_lock:
            _auto_transcribe_ids.add(recording_id)

    logger.info("Áudio do Teams: gravação #%s será transcrita ao parar, sem pop-up.", recording_id)
    threading.Thread(target=_watch_teams_audio, args=(recording_id,), daemon=True).start()


def _watch_teams_audio(recording_id: int) -> None:
    global _teams_episode_active

    last_heard = time.monotonic()
    try:
        while time.monotonic() - last_heard < _TEAMS_SILENCE_SECONDS:
            time.sleep(_TEAMS_POLL_SECONDS)
            if is_teams_playing():
                last_heard = time.monotonic()
        if _stop_content_recording(only_id=recording_id):
            logger.info("Gravação #%s encerrada por silêncio do Teams.", recording_id)
    finally:
        with _content_lock:
            _teams_episode_active = False


def _close_teams_audio_for_call() -> None:
    """A call do Teams começou com o áudio dele já sendo gravado: a call
    passa a cobrir isso. Se a gravação é recente, era o toque de chamada."""
    recording_id = _teams_content_id
    if recording_id is None:
        return
    if time.monotonic() - _teams_content_started < _TEAMS_RINGING_SECONDS:
        _discard_content_recording(recording_id)
    else:
        _stop_content_recording(only_id=recording_id)


def _start_content_recording(source_name: Optional[str] = None) -> None:
    """Chamar só com `_content_lock` adquirido. Sem `source_name`, usa o
    título da janela ativa."""
    global _current_content_recording, _current_content_recording_id

    source_name = sanitize_for_filename(
        source_name or get_active_window_title() or "Conteúdo"
    )
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


def on_content_toggle() -> None:
    """Liga/desliga a gravação de conteúdo (Modo 2) — disparado pelo item
    "Gravar isso" da bandeja. Só a trilha de loopback, sem tag Fabio/Outros
    nem botão de descarte, já que quem inicia é o próprio Fabio de
    propósito."""
    with _content_lock:
        if _current_content_recording is None:
            _start_content_recording()
            return

    _stop_content_recording()


def _take_content_recording(
    only_id: Optional[int],
) -> Tuple[Optional[ContentRecording], Optional[int]]:
    """Retira a gravação de conteúdo de "atual" e a devolve. Com `only_id`,
    só se ela for essa gravação (mesma ideia de `_discard_current`)."""
    global _current_content_recording, _current_content_recording_id

    with _content_lock:
        recording, recording_id = _current_content_recording, _current_content_recording_id
        if recording is None or (only_id is not None and recording_id != only_id):
            return None, None
        _current_content_recording, _current_content_recording_id = None, None
    return recording, recording_id


def _discard_content_recording(only_id: int) -> bool:
    recording, recording_id = _take_content_recording(only_id)
    if recording is None:
        return False

    recording.discard()
    update_recording(
        recording_id, status="discarded", ended_at=datetime.now().isoformat()
    )
    with _answers_lock:
        _auto_transcribe_ids.discard(recording_id)
    logger.info("Gravação de conteúdo #%s descartada.", recording_id)
    refresh_menu()
    return True


def _stop_content_recording(only_id: Optional[int] = None) -> bool:
    recording, recording_id = _take_content_recording(only_id)
    if recording is None:
        return False

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
    logger.info("Gravação de conteúdo #%s salva.", recording_id)
    refresh_menu()
    _apply_popup_answer(recording_id)
    return True


def start_web_server() -> None:
    uvicorn.run(
        web_app,
        host="127.0.0.1",
        port=8000,
        log_level="warning",
    )


def main() -> None:
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
