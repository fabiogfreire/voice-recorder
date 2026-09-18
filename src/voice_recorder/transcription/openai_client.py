"""Cliente fino sobre a API de transcrição da OpenAI.

Pede timestamps por segmento (`verbose_json` + `timestamp_granularities`)
pra permitir mesclar as trilhas mic/loopback por ordem cronológica no Modo
1. Se o modelo não aceitar esses parâmetros (ainda não confirmado em uso
real — só testável com uma chave de API válida), cai pra uma transcrição
única sem timestamps (tratada como um segmento começando em 0s)."""

from dataclasses import dataclass
from pathlib import Path
from typing import List

from openai import BadRequestError, OpenAI

MODEL = "gpt-transcribe"


@dataclass
class TranscriptSegment:
    start_seconds: float
    text: str


def _client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key)


def transcribe_track(path: Path, api_key: str) -> List[TranscriptSegment]:
    client = _client(api_key)

    try:
        with open(path, "rb") as audio_file:
            result = client.audio.transcriptions.create(
                file=audio_file,
                model=MODEL,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        segments = getattr(result, "segments", None)
        if segments:
            return [
                TranscriptSegment(start_seconds=segment.start, text=segment.text.strip())
                for segment in segments
                if segment.text.strip()
            ]
        text = (getattr(result, "text", "") or "").strip()
        return [TranscriptSegment(0.0, text)] if text else []
    except BadRequestError:
        pass  # modelo não aceita verbose_json/timestamps — cai pro fallback abaixo

    with open(path, "rb") as audio_file:
        result = client.audio.transcriptions.create(
            file=audio_file, model=MODEL, response_format="text"
        )
    text = (result if isinstance(result, str) else getattr(result, "text", "")).strip()
    return [TranscriptSegment(0.0, text)] if text else []
