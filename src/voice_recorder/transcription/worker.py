"""Worker de transcrição: pega uma gravação com status 'recorded', transcreve
cada trilha via API da OpenAI e monta o .txt final — no Modo 1, mesclando
mic (Fabio) e loopback (Outros) por timestamp; no Modo 2, só a trilha de
loopback, sem tags (não é conversa)."""

import json
import logging
import re
import threading
from pathlib import Path
from typing import List, Tuple

from ..config import get_openai_api_key
from ..db import get_recording, update_recording
from .openai_client import TranscriptSegment, transcribe_track

logger = logging.getLogger("voice_recorder.transcription")

_LOOPBACK_SUFFIX_RE = re.compile(r"_loopback_.*\.wav$")


def _format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    return f"{total_seconds // 60:02d}:{total_seconds % 60:02d}"


def _content_transcript_path(loopback_paths: List[Path]) -> Path:
    first = loopback_paths[0]
    stem = _LOOPBACK_SUFFIX_RE.sub("", first.name)
    return first.with_name(f"{stem}.txt")


def _merge_call_segments(
    mic_segments: List[TranscriptSegment], loopback_segments: List[TranscriptSegment]
) -> str:
    tagged: List[Tuple[float, str, str]] = [
        (s.start_seconds, "Fabio", s.text) for s in mic_segments
    ]
    tagged += [(s.start_seconds, "Outros", s.text) for s in loopback_segments]
    tagged.sort(key=lambda item: item[0])
    return "\n".join(
        f"[{_format_timestamp(start)}] {speaker}: {text}" for start, speaker, text in tagged
    )


def _render_content_transcript(segments: List[TranscriptSegment]) -> str:
    return "\n".join(segment.text for segment in segments)


def transcribe_recording(recording_id: int, api_key: str) -> None:
    row = get_recording(recording_id)
    if row is None:
        logger.error("Gravação #%s não encontrada.", recording_id)
        return

    update_recording(recording_id, status="transcribing")

    try:
        loopback_paths = [Path(p) for p in json.loads(row["loopback_path"])]

        if row["mode"] == "call":
            mic_segments = transcribe_track(
                Path(row["mic_path"]), api_key, with_timestamps=True
            )
            loopback_segments: List[TranscriptSegment] = []
            for path in loopback_paths:
                loopback_segments.extend(
                    transcribe_track(path, api_key, with_timestamps=True)
                )
            transcript_text = _merge_call_segments(mic_segments, loopback_segments)
            reference_path = Path(row["mic_path"])
            transcript_path = reference_path.with_name(
                reference_path.name.replace("_mic.wav", ".txt")
            )
        else:
            loopback_segments = []
            for path in loopback_paths:
                loopback_segments.extend(
                    transcribe_track(path, api_key, with_timestamps=False)
                )
            transcript_text = _render_content_transcript(loopback_segments)
            transcript_path = _content_transcript_path(loopback_paths)

        transcript_path.write_text(transcript_text, encoding="utf-8")
        update_recording(
            recording_id, status="transcribed", transcript_path=str(transcript_path)
        )
        logger.info("Gravação #%s transcrita em %s", recording_id, transcript_path)
    except Exception:
        logger.exception("Falha ao transcrever gravação #%s", recording_id)
        update_recording(recording_id, status="error")


def enqueue_transcription(recording_id: int) -> None:
    """Dispara a transcrição em background. Sem chave configurada, marca a
    gravação como erro — fica pendente até o Fabio configurar em /settings
    e pedir pra transcrever de novo pela UI."""
    api_key = get_openai_api_key()
    if not api_key:
        logger.warning(
            "Chave da OpenAI não configurada — gravação #%s aguardando "
            "(configure em /settings e clique em Transcrever).",
            recording_id,
        )
        update_recording(recording_id, status="error")
        return

    thread = threading.Thread(
        target=transcribe_recording, args=(recording_id, api_key), daemon=True
    )
    thread.start()
