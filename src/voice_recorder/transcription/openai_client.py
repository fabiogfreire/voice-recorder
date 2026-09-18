"""Cliente fino sobre a API de transcrição da OpenAI.

Dois modelos, conforme testado com uma chave real:
- `whisper-1` no Modo 1 (call): é o único que aceita `verbose_json` com
  timestamp por segmento — necessário pra mesclar mic (Fabio) e loopback
  (Outros) em ordem cronológica. `gpt-transcribe` recusa esse
  response_format (testado: erro 400 "not compatible").
- `gpt-transcribe` no Modo 2 (conteúdo): trilha única, sem necessidade de
  timestamp — mais novo e mais barato, usado como texto simples.

A API tem limite de 25MB por arquivo. Em 16kHz mono isso dá ~13min de
áudio — uma aula de 26min (caso real que motivou isso) já estoura. Trilhas
maiores que o limite são divididas em pedaços antes do envio, e os
timestamps dos pedaços são realinhados pro tempo da gravação original.
"""

import shutil
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import numpy as np
from openai import OpenAI

MODEL_WITH_TIMESTAMPS = "whisper-1"
MODEL_PLAIN = "gpt-transcribe"

# Abaixo disso, tratamos como silêncio/ruído de fundo, não fala de verdade.
# Calibrado com testes reais: ruído de fundo típico fica na casa de 15-20,
# fala real fica bem acima de 300. Sem esse filtro, o Whisper "alucina"
# frases genéricas (ex: "Thanks for watching!") em trilhas praticamente
# vazias — visto na prática numa trilha de loopback sem áudio real.
_SILENCE_RMS_THRESHOLD = 60.0

# Margem de segurança abaixo do limite real de 25MB da API.
_MAX_CHUNK_BYTES = 24 * 1024 * 1024


def has_audio_signal(path: Path) -> bool:
    with wave.open(str(path), "rb") as wav_file:
        frames = wav_file.readframes(wav_file.getnframes())
    if not frames:
        return False
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float64)
    rms = float(np.sqrt(np.mean(data**2)))
    return rms >= _SILENCE_RMS_THRESHOLD


@dataclass
class TranscriptSegment:
    start_seconds: float
    text: str


def _client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key)


def _split_into_chunks(path: Path, max_bytes: int) -> Tuple[List[Tuple[Path, float]], Path]:
    """Divide `path` em pedaços de no máximo `max_bytes`, cada um num WAV
    temporário. Retorna a lista de (caminho_do_pedaço, offset_em_segundos)
    e o diretório temporário (None se não houve divisão, e o próprio
    `path` original é devolvido como único item)."""
    with wave.open(str(path), "rb") as wav_file:
        n_channels = wav_file.getnchannels()
        sampwidth = wav_file.getsampwidth()
        framerate = wav_file.getframerate()
        n_frames = wav_file.getnframes()
        frame_size = n_channels * sampwidth

        if n_frames * frame_size <= max_bytes:
            return [(path, 0.0)], None

        frames_per_chunk = max(1, max_bytes // frame_size)
        tmp_dir = Path(tempfile.mkdtemp(prefix="voice_recorder_chunk_"))
        chunks: List[Tuple[Path, float]] = []
        chunk_index = 0
        frame_index = 0

        while frame_index < n_frames:
            count = min(frames_per_chunk, n_frames - frame_index)
            data = wav_file.readframes(count)
            chunk_path = tmp_dir / f"chunk_{chunk_index}.wav"
            with wave.open(str(chunk_path), "wb") as chunk_file:
                chunk_file.setnchannels(n_channels)
                chunk_file.setsampwidth(sampwidth)
                chunk_file.setframerate(framerate)
                chunk_file.writeframes(data)
            chunks.append((chunk_path, frame_index / framerate))
            frame_index += count
            chunk_index += 1

        return chunks, tmp_dir


def transcribe_track(
    path: Path, api_key: str, with_timestamps: bool
) -> List[TranscriptSegment]:
    if not has_audio_signal(path):
        return []

    client = _client(api_key)
    chunks, tmp_dir = _split_into_chunks(path, _MAX_CHUNK_BYTES)

    try:
        all_segments: List[TranscriptSegment] = []
        for chunk_path, offset_seconds in chunks:
            if with_timestamps:
                with open(chunk_path, "rb") as audio_file:
                    result = client.audio.transcriptions.create(
                        file=audio_file,
                        model=MODEL_WITH_TIMESTAMPS,
                        response_format="verbose_json",
                        timestamp_granularities=["segment"],
                    )
                for segment in result.segments or []:
                    text = segment.text.strip()
                    if text:
                        all_segments.append(
                            TranscriptSegment(offset_seconds + segment.start, text)
                        )
            else:
                with open(chunk_path, "rb") as audio_file:
                    result = client.audio.transcriptions.create(
                        file=audio_file, model=MODEL_PLAIN, response_format="text"
                    )
                text = (
                    result if isinstance(result, str) else getattr(result, "text", "")
                ).strip()
                if text:
                    all_segments.append(TranscriptSegment(offset_seconds, text))

        return all_segments
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
