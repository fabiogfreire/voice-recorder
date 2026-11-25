"""Reconhece o Microsoft Teams como origem — no microfone (call) ou no som
que está tocando. Pra ele não há pop-up: grava e transcreve direto
(SPEC-popup-gravar-transcrever.md).

Cobre o app do Teams; o Teams aberto no navegador aparece como o próprio
navegador."""

import logging
import time

import psutil

from .audio_sessions import RENDER, init_com, iter_sessions

import comtypes
from pycaw.api.endpointvolume import IAudioMeterInformation

logger = logging.getLogger("voice_recorder")

_PROCESS_NAMES = {"ms-teams.exe", "teams.exe"}
_PACKAGE_PREFIXES = ("msteams_", "microsoftteams_")
# Mesmo limiar de silêncio do PlaybackWatcher.
_PEAK_THRESHOLD = 30.0 / 32767


def is_teams_mic_label(label: str) -> bool:
    """`label` no formato do MicWatcher: nome do pacote (ex:
    "MSTeams_8wekyb3d8bbwe") ou "NonPackaged\\C:#...#ms-teams.exe"."""
    name = label.lower()
    if name.startswith("nonpackaged\\"):
        return name.split("#")[-1] in _PROCESS_NAMES
    return name.startswith(_PACKAGE_PREFIXES)


def _belongs_to_teams(pid: int) -> bool:
    """O som do Teams sai de processos `msedgewebview2.exe` filhos dele, por
    isso olha também os processos pais."""
    try:
        process = psutil.Process(pid)
        return any(
            p.name().lower() in _PROCESS_NAMES for p in [process, *process.parents()]
        )
    except psutil.Error:
        return False


def _teams_has_signal_now() -> bool:
    for pid, control in iter_sessions(RENDER):
        try:
            peak = control.QueryInterface(IAudioMeterInformation).GetPeakValue()
        except comtypes.COMError:
            continue
        if peak >= _PEAK_THRESHOLD and _belongs_to_teams(pid):
            return True
    return False


def is_teams_playing(samples: int = 6, interval_seconds: float = 0.25) -> bool:
    """Amostra por ~1,5s, porque o medidor é instantâneo e uma pausa de fala
    daria falso negativo. Qualquer falha vira False: o pior caso é cair no
    pop-up comum."""
    init_com()
    try:
        for attempt in range(samples):
            if attempt:
                time.sleep(interval_seconds)
            if _teams_has_signal_now():
                return True
    except Exception:
        logger.exception("Falha ao verificar se o Teams está tocando áudio.")
    return False
