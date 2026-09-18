"""Captura de áudio em trilhas separadas via WASAPI, usando `soundcard` —
suporta loopback nativamente no Windows, sem driver extra tipo Stereo Mix.

Modo 1 (call): grava mic e loopback ao mesmo tempo, em arquivos WAV
separados, pra identificar "Fabio" (mic) vs "Outros" (loopback) por
construção, sem depender de diarização.
Modo 2 (conteúdo/aula): só a trilha de loopback.
"""

import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import soundcard as sc

SAMPLE_RATE = 48000
CHUNK_FRAMES = 4096


def get_loopback_microphone():
    """Retorna o "microfone" de loopback do dispositivo de saída padrão
    (o que está tocando pro Fabio ouvir vira uma fonte de gravação)."""
    speaker = sc.default_speaker()
    return sc.get_microphone(id=str(speaker.name), include_loopback=True)


def _float_to_pcm16(data: np.ndarray) -> bytes:
    clipped = np.clip(data, -1.0, 1.0)
    return (clipped * 32767).astype(np.int16).tobytes()


class TrackRecorder:
    """Grava uma única trilha (mic ou loopback) num WAV, numa thread
    dedicada, até stop() ser chamado. O número de canais é detectado no
    primeiro bloco lido, em vez de fixado, pra funcionar com qualquer
    dispositivo (mic mono, saída estéreo etc.)."""

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
                samplerate=SAMPLE_RATE, blocksize=CHUNK_FRAMES
            ) as recorder:
                first_chunk = recorder.record(numframes=CHUNK_FRAMES)
                channels = first_chunk.shape[1] if first_chunk.ndim > 1 else 1

                with wave.open(str(self.out_path), "wb") as wav_file:
                    wav_file.setnchannels(channels)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(SAMPLE_RATE)
                    wav_file.writeframes(_float_to_pcm16(first_chunk))

                    while not self._stop_event.is_set():
                        data = recorder.record(numframes=CHUNK_FRAMES)
                        wav_file.writeframes(_float_to_pcm16(data))
        except BaseException as exc:  # noqa: BLE001 - propagada em stop()
            self._error = exc


class CallRecording:
    """Gravação do Modo 1: mic + loopback em trilhas separadas."""

    def __init__(self, mic_path: Path, loopback_path: Path):
        self.mic_path = mic_path
        self.loopback_path = loopback_path
        self._mic_recorder = TrackRecorder(sc.default_microphone(), mic_path)
        self._loopback_recorder = TrackRecorder(get_loopback_microphone(), loopback_path)

    def start(self) -> None:
        self._mic_recorder.start()
        self._loopback_recorder.start()

    def stop(self) -> None:
        self._mic_recorder.stop()
        self._loopback_recorder.stop()

    def discard(self) -> None:
        self.stop()
        for path in (self.mic_path, self.loopback_path):
            path.unlink(missing_ok=True)


class ContentRecording:
    """Gravação do Modo 2: só loopback, trilha única."""

    def __init__(self, loopback_path: Path):
        self.loopback_path = loopback_path
        self._recorder = TrackRecorder(get_loopback_microphone(), loopback_path)

    def start(self) -> None:
        self._recorder.start()

    def stop(self) -> None:
        self._recorder.stop()

    def discard(self) -> None:
        self.stop()
        self.loopback_path.unlink(missing_ok=True)
