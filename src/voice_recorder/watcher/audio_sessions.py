"""Sessões de áudio do Windows (a mesma infraestrutura do mixer de volume),
usadas pra saber quem está no microfone e quem está tocando som."""

from typing import Iterator, Tuple

# Importado primeiro de propósito: fixa a ordem de inicialização de COM entre
# `soundcard` e `comtypes` (ver comentário em audio_muter.py).
from . import audio_muter  # noqa: F401

import comtypes
from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
from pycaw.constants import DEVICE_STATE, EDataFlow
from pycaw.utils import AudioUtilities

RENDER = EDataFlow.eRender.value  # saídas
CAPTURE = EDataFlow.eCapture.value  # microfones


def init_com() -> None:
    """Chamar na thread que for varrer as sessões."""
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass  # thread já inicializada em outro modo


def iter_sessions(flow: int) -> Iterator[Tuple[int, object]]:
    """Gera (pid, controle) das sessões de **todos** os dispositivos ativos do
    sentido `flow`, não só o padrão: apps de call costumam usar o headset,
    que pode não ser o padrão."""
    devices = AudioUtilities.GetDeviceEnumerator().EnumAudioEndpoints(
        flow, DEVICE_STATE.ACTIVE.value
    )
    for i in range(devices.GetCount()):
        try:
            manager = (
                devices.Item(i)
                .Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None)
                .QueryInterface(IAudioSessionManager2)
            )
            sessions = manager.GetSessionEnumerator()
            count = sessions.GetCount()
        except comtypes.COMError:
            continue  # dispositivo sumiu no meio da varredura

        for j in range(count):
            try:
                control = sessions.GetSession(j).QueryInterface(IAudioSessionControl2)
                pid = control.GetProcessId()
            except comtypes.COMError:
                continue  # sessão sumiu no meio da varredura
            if pid:  # 0 é a sessão de sons do sistema
                yield pid, control
