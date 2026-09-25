"""Worker de transcrição: pega uma gravação com status 'recorded', transcreve
cada trilha via API da OpenAI e monta o .txt final — no Modo 1, mesclando
mic (Fabio) e loopback (Outros) por timestamp; no Modo 2, só a trilha de
loopback, sem tags (não é conversa)."""

import json
import logging
import re
import shutil
import threading
from pathlib import Path
from typing import List, Optional, Tuple

from ..config import get_openai_api_key
from ..db import get_recording, update_recording
from .openai_client import TranscriptSegment, _extract_clip, transcribe_track

logger = logging.getLogger("voice_recorder.transcription")

_LOOPBACK_SUFFIX_RE = re.compile(r"_loopback_.*\.wav$")

# Decisão do Fabio (SPEC-transcricao-sob-demanda.md): 1 minuto contado do
# início da gravação, suficiente pra responder "é essa gravação?" sem
# ouvir o áudio.
_PREVIEW_DURATION_SECONDS = 60.0


def _format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    return f"{total_seconds // 60:02d}:{total_seconds % 60:02d}"


def _content_transcript_path(loopback_paths: List[Path]) -> Path:
    first = loopback_paths[0]
    stem = _LOOPBACK_SUFFIX_RE.sub("", first.name)
    return first.with_name(f"{stem}.txt")


def _merge_call_segments(
    mic_segments: List[TranscriptSegment],
    loopback_segments: List[TranscriptSegment],
    participants: Optional[List[str]] = None,
) -> str:
    """Mescla mic (Fabio) e loopback (o resto) por timestamp. `participants`
    são os nomes reais capturados via UI Automation
    (SPEC-identificacao-participantes.md):
    - 0 nomes (falhou/app não suportado): "Outros" como sempre, sem
      ambiguidade nenhuma — comportamento inalterado.
    - 1 nome (call 1:1, o caso comum): sem outra voz pra confundir, troca
      "Outros" pelo nome real em todas as linhas.
    - 2+ nomes (grupo): não dá pra saber quem falou cada linha sem
      diarização (fora de escopo, ver módulo participants.py), então o
      corpo continua "Outros" e só um cabeçalho lista quem estava lá —
      o Mecanismo B (best-effort, botão "Identificar participantes" na UI)
      pode relabelar linhas individuais depois, por cima do .txt já salvo."""
    participants = participants or []
    other_label = participants[0] if len(participants) == 1 else "Outros"

    tagged: List[Tuple[float, str, str]] = [
        (s.start_seconds, "Fabio", s.text) for s in mic_segments
    ]
    tagged += [(s.start_seconds, other_label, s.text) for s in loopback_segments]
    tagged.sort(key=lambda item: item[0])
    body = "\n".join(
        f"[{_format_timestamp(start)}] {speaker}: {text}" for start, speaker, text in tagged
    )

    if len(participants) >= 2:
        return f"Participantes: {', '.join(participants)}\n\n{body}"
    return body


def _render_content_transcript(segments: List[TranscriptSegment]) -> str:
    return "\n".join(segment.text for segment in segments)


def transcribe_recording(recording_id: int, api_key: str) -> None:
    row = get_recording(recording_id)
    if row is None:
        logger.error("Gravação #%s não encontrada.", recording_id)
        return

    update_recording(recording_id, status="transcribing", error_message=None)

    try:
        loopback_paths = [Path(p) for p in json.loads(row["loopback_path"])]

        if row["mode"] == "call":
            participants = json.loads(row["participants"]) if row["participants"] else []
            mic_segments = transcribe_track(
                Path(row["mic_path"]), api_key, with_timestamps=True
            )
            loopback_segments: List[TranscriptSegment] = []
            for path in loopback_paths:
                loopback_segments.extend(
                    transcribe_track(path, api_key, with_timestamps=True)
                )
            transcript_text = _merge_call_segments(mic_segments, loopback_segments, participants)
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
    except Exception as exc:
        logger.exception("Falha ao transcrever gravação #%s", recording_id)
        update_recording(recording_id, status="error", error_message=str(exc)[:500])


def transcribe_preview(recording_id: int, api_key: str) -> None:
    """Transcreve só o primeiro minuto de cada trilha com sinal — mesmo
    caminho da transcrição completa (`transcribe_recording`), só que sobre
    clipes recortados. Diferença central: **nunca muda `status`**. A
    gravação segue `recorded` (ou o que já era) e continua elegível pra
    transcrição completa depois — erro aqui vira só `error_message`, sem
    derrubar o status (SPEC-transcricao-sob-demanda.md)."""
    row = get_recording(recording_id)
    if row is None:
        logger.error("Gravação #%s não encontrada.", recording_id)
        return

    clip_dirs: List[Path] = []
    try:
        loopback_paths = (
            [Path(p) for p in json.loads(row["loopback_path"])]
            if row["loopback_path"]
            else []
        )

        def _clip(path: Path) -> Path:
            clip_path = _extract_clip(path, 0.0, _PREVIEW_DURATION_SECONDS)
            clip_dirs.append(clip_path.parent)
            return clip_path

        if row["mode"] == "call":
            participants = json.loads(row["participants"]) if row["participants"] else []
            mic_segments = transcribe_track(
                _clip(Path(row["mic_path"])), api_key, with_timestamps=True
            )
            loopback_segments: List[TranscriptSegment] = []
            for path in loopback_paths:
                loopback_segments.extend(
                    transcribe_track(_clip(path), api_key, with_timestamps=True)
                )
            preview_text = _merge_call_segments(mic_segments, loopback_segments, participants)
        else:
            loopback_segments = []
            for path in loopback_paths:
                loopback_segments.extend(
                    transcribe_track(_clip(path), api_key, with_timestamps=False)
                )
            preview_text = _render_content_transcript(loopback_segments)

        update_recording(recording_id, preview_text=preview_text, error_message=None)
        logger.info("Prévia gerada pra gravação #%s.", recording_id)
    except Exception as exc:
        logger.exception("Falha ao gerar prévia da gravação #%s", recording_id)
        update_recording(recording_id, error_message=str(exc)[:500])
    finally:
        for clip_dir in clip_dirs:
            shutil.rmtree(clip_dir, ignore_errors=True)


def enqueue_transcription(recording_id: int) -> None:
    """Dispara a transcrição em background. Sem chave configurada, marca a
    gravação como erro — fica pendente até o Fabio configurar em /settings
    e pedir pra transcrever de novo pela UI."""
    api_key = get_openai_api_key()
    if not api_key:
        message = (
            "Chave da OpenAI não configurada — configure em /settings e "
            "clique em Transcrever de novo."
        )
        logger.warning("Gravação #%s aguardando: %s", recording_id, message)
        update_recording(recording_id, status="error", error_message=message)
        return

    thread = threading.Thread(
        target=transcribe_recording, args=(recording_id, api_key), daemon=True
    )
    thread.start()
