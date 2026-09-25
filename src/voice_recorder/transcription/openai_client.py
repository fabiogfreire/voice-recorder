"""Cliente fino sobre a API de transcrição da OpenAI.

Modelos configuráveis via `config.py` (`get_transcription_model_timestamps()`
/ `get_transcription_model_plain()`), já que nem toda chave/projeto tem
acesso aos mesmos modelos:
- Modo 1 (call) precisa de um modelo que aceite `verbose_json` com
  timestamp por segmento — necessário pra mesclar mic (Fabio) e loopback
  (Outros) em ordem cronológica. Hoje só `whisper-1` faz isso; outro
  modelo configurado aqui vai falhar nessa chamada (erro 400 "not
  compatible"), e o erro chega até a UI (ver `worker.py`).
- Modo 2 (conteúdo): trilha única, sem necessidade de timestamp.

A API tem limite de 25MB por arquivo. Em 16kHz mono isso dá ~13min de
áudio — uma aula de 26min (caso real que motivou isso) já estoura. Trilhas
maiores que o limite são divididas em pedaços antes do envio, e os
timestamps dos pedaços são realinhados pro tempo da gravação original.
"""

import logging
import shutil
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Tuple

import numpy as np
from openai import APIConnectionError, OpenAI, PermissionDeniedError

from ..config import get_transcription_model_plain, get_transcription_model_timestamps

logger = logging.getLogger("voice_recorder.transcription")

# A OpenAI leva um tempo pra propagar mudanças de permissão de modelo (feito
# no dashboard) por todos os nós que atendem a API — confirmado na prática:
# depois de liberar um modelo, chamadas idênticas em sequência alternavam
# entre sucesso e 403 PermissionDeniedError por vários minutos. O SDK não
# reten essas por padrão (assume que 403 é permanente), então isso é feito
# aqui manualmente. Erros de conexão entram na mesma lógica por serem
# igualmente transitórios.
_RETRY_DELAYS_SECONDS: Tuple[int, ...] = (2, 4, 8)

# Abaixo disso, tratamos como silêncio/ruído de fundo, não fala de verdade.
# Calibrado com testes reais: ruído de fundo típico fica na casa de 15-20,
# fala/áudio real fica bem acima disso — mas nem sempre muito acima: um
# vídeo tocando baixo num monitor externo mediu RMS 56, que o limiar
# antigo (60) rejeitava por engano. Baixado com margem de segurança acima
# do ruído de fundo, mas abaixo de conteúdo real mesmo quieto. Sem esse
# filtro, o Whisper "alucina" frases genéricas (ex: "Thanks for
# watching!") em trilhas praticamente vazias.
_SILENCE_RMS_THRESHOLD = 30.0

# Margem de segurança abaixo do limite real de 25MB da API.
_MAX_CHUNK_BYTES = 24 * 1024 * 1024

# WAVs de call podem chegar a centenas de MB (ex: gravação travada que
# cresceu até 793MB) — carregar tudo de uma vez pra calcular o RMS já
# causou MemoryError. Lendo em blocos, o pico de memória fica limitado
# a um bloco (~2MB em int16 mono), não ao arquivo inteiro.
_RMS_BLOCK_FRAMES = 1_000_000


def has_audio_signal(path: Path) -> bool:
    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getnframes() == 0:
            return False
        sum_squares = 0.0
        sample_count = 0
        while True:
            frames = wav_file.readframes(_RMS_BLOCK_FRAMES)
            if not frames:
                break
            data = np.frombuffer(frames, dtype=np.int16).astype(np.float64)
            sum_squares += float(np.sum(data**2))
            sample_count += data.size
    if sample_count == 0:
        return False
    rms = (sum_squares / sample_count) ** 0.5
    return rms >= _SILENCE_RMS_THRESHOLD


@dataclass
class TranscriptSegment:
    start_seconds: float
    text: str


def _client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key)


def _create_transcription(client: OpenAI, **kwargs: Any):
    total_attempts = len(_RETRY_DELAYS_SECONDS) + 1
    audio_file = kwargs.get("file")
    for attempt in range(total_attempts):
        if attempt > 0 and hasattr(audio_file, "seek"):
            audio_file.seek(0)
        try:
            return client.audio.transcriptions.create(**kwargs)
        except (PermissionDeniedError, APIConnectionError) as exc:
            if attempt == len(_RETRY_DELAYS_SECONDS):
                raise
            delay = _RETRY_DELAYS_SECONDS[attempt]
            logger.warning(
                "Transcrição falhou (tentativa %s/%s): %s — retentando em %ss.",
                attempt + 1,
                total_attempts,
                exc,
                delay,
            )
            time.sleep(delay)


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
                    result = _create_transcription(
                        client,
                        file=audio_file,
                        model=get_transcription_model_timestamps(),
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
                    result = _create_transcription(
                        client,
                        file=audio_file,
                        model=get_transcription_model_plain(),
                        response_format="text",
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
