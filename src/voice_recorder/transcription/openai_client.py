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
import re
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

# Preço por minuto de áudio transcrito — vale pra whisper-1 e pra
# gpt-4o-transcribe (mesma tabela de preço da OpenAI pra transcrição;
# ver SPEC-transcricao-sob-demanda.md). Só uma estimativa: não cobre
# eventuais mudanças de preço nem descontos por volume.
_USD_PER_MINUTE = 0.006

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


def estimate_cost_usd(duration_seconds: float, track_count: int) -> float:
    """Estimativa de custo pra transcrever `track_count` trilhas, cada uma
    com `duration_seconds` de duração. Mic e loopback são gravados em
    paralelo e param juntos, então têm (aproximadamente) a mesma duração —
    por isso um único `duration_seconds` multiplicado pelo número de
    trilhas já cobre o total. Trilhas mudas não entram em `track_count`
    (ver `billable_tracks`, calculado com `has_audio_signal` ao fechar a
    gravação)."""
    minutes = duration_seconds / 60.0
    return minutes * track_count * _USD_PER_MINUTE


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


def _extract_clip(path: Path, start_seconds: float, duration_seconds: float) -> Path:
    """Recorta um trecho de `path` (a partir de `start_seconds`, com até
    `duration_seconds` de duração) num WAV temporário — usado pra prévia
    (SPEC-transcricao-sob-demanda.md). Mesma mecânica de `setpos()` +
    `readframes()` do `_split_into_chunks()`: lê só o trecho pedido, não o
    arquivo inteiro. Cabe ao chamador apagar `clip_path.parent` quando
    terminar (mesmo padrão de `tmp_dir` usado em `transcribe_track`)."""
    with wave.open(str(path), "rb") as wav_file:
        n_channels = wav_file.getnchannels()
        sampwidth = wav_file.getsampwidth()
        framerate = wav_file.getframerate()
        n_frames = wav_file.getnframes()

        start_frame = min(int(start_seconds * framerate), n_frames)
        frame_count = max(0, min(int(duration_seconds * framerate), n_frames - start_frame))

        wav_file.setpos(start_frame)
        data = wav_file.readframes(frame_count)

    tmp_dir = Path(tempfile.mkdtemp(prefix="voice_recorder_preview_"))
    clip_path = tmp_dir / "clip.wav"
    with wave.open(str(clip_path), "wb") as clip_file:
        clip_file.setnchannels(n_channels)
        clip_file.setsampwidth(sampwidth)
        clip_file.setframerate(framerate)
        clip_file.writeframes(data)

    return clip_path


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


# Modelo de texto puro pra atribuição heurística (Mecanismo B) — já
# liberado na conta da OpenAI, ao contrário de gpt-4o-transcribe-diarize
# (bloqueado, ver SPEC-identificacao-participantes.md). Não precisa do
# mesmo tratamento de configurável dos modelos de transcrição: essa
# chamada é texto->texto simples, sem restrição de formato de áudio.
_PARTICIPANT_ASSIGNMENT_MODEL = "gpt-4o-mini"

# O cabeçalho "Participantes: ..." que _merge_call_segments antepõe pra
# call em grupo (worker.py) — separado do corpo ANTES de mandar pro LLM
# e recolocado depois, em vez de confiar que o modelo preserva ele
# verbatim: testado na prática, o gpt-4o-mini removeu o cabeçalho mesmo
# com instrução explícita pra não adicionar/remover linhas.
_PARTICIPANTS_HEADER_RE = re.compile(r"\AParticipantes: .*?\n\n", re.DOTALL)


def identify_participants_in_transcript(
    transcript_text: str, participants: List[str], api_key: str
) -> str:
    """Mecanismo B (best-effort, só entra em jogo pra call em grupo — ver
    SPEC-identificacao-participantes.md): tenta relabelar linhas "Outros:"
    com o nome mais provável usando só o contexto textual (ex: alguém
    chamado pelo nome logo antes de falar). Continua "Outros:" quando o
    contexto não deixa claro — o resultado é um palpite, não diarização
    de verdade, por isso toda linha reatribuída é marcada com
    "(provável)" explicitamente no texto."""
    header_match = _PARTICIPANTS_HEADER_RE.match(transcript_text)
    header = header_match.group(0) if header_match else ""
    body = transcript_text[len(header):]

    client = _client(api_key)
    prompt = (
        "Você recebe a transcrição de uma reunião com várias pessoas além do "
        "Fabio, todas agrupadas sob o rótulo \"Outros\" porque a gravação "
        "não distingue vozes, só \"microfone do Fabio\" vs \"resto\". Os "
        "participantes reais desta reunião, além do Fabio, são: "
        f"{', '.join(participants)}.\n\n"
        "Reescreva a transcrição abaixo linha por linha. Troque \"Outros\" "
        "pelo nome mais provável SÓ quando o contexto deixar claro quem "
        "está falando (ex: alguém é chamado pelo nome logo antes ou se "
        "apresenta). Nesses casos, marque a linha assim: "
        "\"[MM:SS] Nome (provável): texto\". Quando não der pra saber com "
        "confiança, mantenha \"Outros:\" exatamente como está. Não mude "
        "nada nas linhas \"Fabio:\", não mude timestamps, não mude o "
        "texto de nenhuma fala, não adicione nem remova linhas nem "
        "comentários. Devolva só a transcrição reescrita.\n\n"
        f"{body}"
    )
    response = client.chat.completions.create(
        model=_PARTICIPANT_ASSIGNMENT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    text = response.choices[0].message.content
    rewritten_body = text.strip() if text else body
    # O modelo às vezes devolve espaços soltos no fim de cada linha
    # (estilo quebra de linha do markdown) — cosmético, mas polui o .txt.
    rewritten_body = "\n".join(line.rstrip() for line in rewritten_body.splitlines())
    return header + rewritten_body
