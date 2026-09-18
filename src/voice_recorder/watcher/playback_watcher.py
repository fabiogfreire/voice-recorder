"""Detecta quando algum áudio começa a tocar em qualquer dispositivo de
saída — gatilho da notificação opt-in do Modo 2 ("quer gravar isso?").

Faz uma checagem leve e periódica (grava um bloco curtinho de cada
dispositivo de saída e mede o nível), em vez de manter um loopback
full-time rodando só pra monitorar — mais barato em CPU."""

import threading
from typing import Callable, Optional

import numpy as np
import soundcard as sc

_CHECK_SAMPLE_RATE = 16000
_CHECK_FRAMES = 4096

# Mesma ideia do limiar de silêncio da transcrição (openai_client.py,
# ajustado pra 30/32767), mas em escala float (-1..1) já que aqui não
# convertemos pra int16.
_SIGNAL_THRESHOLD = 30.0 / 32767


def _any_device_has_signal() -> bool:
    for speaker in sc.all_speakers():
        try:
            microphone = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            with microphone.recorder(
                samplerate=_CHECK_SAMPLE_RATE, channels=1, blocksize=_CHECK_FRAMES
            ) as recorder:
                data = recorder.record(numframes=_CHECK_FRAMES)
        except Exception:
            continue

        rms = float(np.sqrt(np.mean(data.astype(np.float64) ** 2)))
        if rms >= _SIGNAL_THRESHOLD:
            return True
    return False


class PlaybackWatcher:
    """Poll periódico dos dispositivos de saída. Dispara `on_playback_start`
    quando detecta áudio por `consecutive_required` checagens seguidas (evita
    notificar por causa de um som curto tipo notificação/beep), e reseta ao
    voltar pro silêncio, permitindo notificar de novo na próxima vez."""

    def __init__(
        self,
        on_playback_start: Callable[[], None],
        should_check: Callable[[], bool],
        poll_interval_seconds: float = 3.0,
        consecutive_required: int = 2,
    ):
        self.on_playback_start = on_playback_start
        self.should_check = should_check
        self.poll_interval_seconds = poll_interval_seconds
        self.consecutive_required = consecutive_required
        self._consecutive_active = 0
        self._already_notified = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if not self.should_check():
                self._consecutive_active = 0
                self._already_notified = False
            elif _any_device_has_signal():
                self._consecutive_active += 1
                if (
                    self._consecutive_active >= self.consecutive_required
                    and not self._already_notified
                ):
                    self._already_notified = True
                    self.on_playback_start()
            else:
                self._consecutive_active = 0
                self._already_notified = False

            self._stop_event.wait(self.poll_interval_seconds)
