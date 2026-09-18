"""Cliente fino sobre a API de transcrição da OpenAI.

Dois modelos, conforme testado com uma chave real:
- `whisper-1` no Modo 1 (call): é o único que aceita `verbose_json` com
  timestamp por segmento — necessário pra mesclar mic (Fabio) e loopback
  (Outros) em ordem cronológica. `gpt-transcribe` recusa esse
  response_format (testado: erro 400 "not compatible").
- `gpt-transcribe` no Modo 2 (conteúdo): trilha única, sem necessidade de
  timestamp — mais novo e mais barato, usado como texto simples.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List

from openai import OpenAI

MODEL_WITH_TIMESTAMPS = "whisper-1"
MODEL_PLAIN = "gpt-transcribe"


@dataclass
class TranscriptSegment:
    start_seconds: float
    text: str


def _client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key)


def transcribe_track(
    path: Path, api_key: str, with_timestamps: bool
) -> List[TranscriptSegment]:
    client = _client(api_key)

    if with_timestamps:
        with open(path, "rb") as audio_file:
            result = client.audio.transcriptions.create(
                file=audio_file,
                model=MODEL_WITH_TIMESTAMPS,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        segments = result.segments or []
        return [
            TranscriptSegment(start_seconds=segment.start, text=segment.text.strip())
            for segment in segments
            if segment.text.strip()
        ]

    with open(path, "rb") as audio_file:
        result = client.audio.transcriptions.create(
            file=audio_file, model=MODEL_PLAIN, response_format="text"
        )
    text = (result if isinstance(result, str) else getattr(result, "text", "")).strip()
    return [TranscriptSegment(0.0, text)] if text else []
