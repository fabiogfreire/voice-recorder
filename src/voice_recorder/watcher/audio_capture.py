"""Captura de áudio em trilhas separadas via WASAPI, usando `soundcard` —
suporta loopback nativamente no Windows, sem driver extra tipo Stereo Mix.

Modo 1 (call): grava mic e loopback ao mesmo tempo, em arquivos WAV
separados, pra identificar "Fabio" (mic) vs "Outros" (loopback) por
construção, sem depender de diarização.
Modo 2 (conteúdo/aula): só a trilha de loopback.

Loopback de TODOS os dispositivos de saída (não só o "padrão"): testado na
prática numa call de WhatsApp com monitor externo — o Windows pode trocar
o dispositivo de saída padrão durante a call (ex: pra um monitor com
caixa de som via HDMI) e voltar depois, então `sc.default_speaker()`
sozinho não é confiável nesse cenário. Gravando todos, o dispositivo que
realmente tiver o áudio da call é capturado de qualquer forma; os demais
ficam silenciosos e são descartados na transcrição (ver
`transcription.openai_client.has_audio_signal`).
"""

import threading
import wave
from pathlib import Path
from typing import List, Optional

import numpy as np
import soundcard as sc

from ..filenames import sanitize_for_filename

SAMPLE_RATE = 16000  # suficiente pra voz; ~6x menor que 48kHz estéreo, já
                      # que a API de transcrição tem limite de 25MB por
                      # arquivo (48kHz estéreo estourava isso em ~2min)
CHANNELS = 1  # cada trilha já é uma fonte só (mic = Fabio, loopback = Outros)
CHUNK_FRAMES = 4096


def _float_to_pcm16(data: np.ndarray) -> bytes:
    clipped = np.clip(data, -1.0, 1.0)
    return (clipped * 32767).astype(np.int16).tobytes()


class TrackRecorder:
    """Grava uma única trilha (mic ou loopback) num WAV, numa thread
    dedicada, até stop() ser chamado. `soundcard` reamostra de verdade pro
    samplerate/canais pedidos (testado: contagem de amostras bate com o
    16kHz mono solicitado, não é só um relabel do header)."""

    def __init__(self, microphone, out_path: Path):
        self.microphone = microphone
        self.out_path = out_path
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[BaseException] = None

    def start(self) -> None:
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        if self._error:
            raise self._error

    def _run(self) -> None:
        try:
            with self.microphone.recorder(
                samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=CHUNK_FRAMES
            ) as recorder, wave.open(str(self.out_path), "wb") as wav_file:
                wav_file.setnchannels(CHANNELS)
                wav_file.setsampwidth(2)
                wav_file.setframerate(SAMPLE_RATE)

                while not self._stop_event.is_set():
                    data = recorder.record(numframes=CHUNK_FRAMES)
                    wav_file.writeframes(_float_to_pcm16(data))
        except BaseException as exc:  # noqa: BLE001 - propagada em stop()
            self._error = exc


def _build_loopback_recorders(
    loopback_dir: Path, base_name: str
) -> List[TrackRecorder]:
    recorders = []
    for speaker in sc.all_speakers():
        label = sanitize_for_filename(str(speaker.name), max_length=30)
        path = loopback_dir / f"{base_name}_loopback_{label}.wav"
        microphone = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        recorders.append(TrackRecorder(microphone, path))
    return recorders


class CallRecording:
    """Gravação do Modo 1: mic + loopback de todos os dispositivos de
    saída, em trilhas separadas."""

    def __init__(self, mic_path: Path, loopback_dir: Path, base_name: str):
        self.mic_path = mic_path
        self._mic_recorder = TrackRecorder(sc.default_microphone(), mic_path)
        self._loopback_recorders = _build_loopback_recorders(loopback_dir, base_name)

    @property
    def loopback_paths(self) -> List[Path]:
        return [recorder.out_path for recorder in self._loopback_recorders]

    def start(self) -> None:
        self._mic_recorder.start()
        for recorder in self._loopback_recorders:
            recorder.start()

    def stop(self) -> None:
        self._mic_recorder.stop()
        for recorder in self._loopback_recorders:
            recorder.stop()

    def discard(self) -> None:
        self.stop()
        self.mic_path.unlink(missing_ok=True)
        for path in self.loopback_paths:
            path.unlink(missing_ok=True)


class ContentRecording:
    """Gravação do Modo 2: loopback de todos os dispositivos de saída (só
    um deles deve ter conteúdo real; os demais são filtrados na
    transcrição)."""

    def __init__(self, loopback_dir: Path, base_name: str):
        self._recorders = _build_loopback_recorders(loopback_dir, base_name)

    @property
    def loopback_paths(self) -> List[Path]:
        return [recorder.out_path for recorder in self._recorders]

    def start(self) -> None:
        for recorder in self._recorders:
            recorder.start()

    def stop(self) -> None:
        for recorder in self._recorders:
            recorder.stop()

    def discard(self) -> None:
        self.stop()
        for path in self.loopback_paths:
            path.unlink(missing_ok=True)
