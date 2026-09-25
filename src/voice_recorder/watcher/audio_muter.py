"""Muta automaticamente outros apps que estejam tocando som quando uma call
começa, e desfaz exatamente isso quando a call termina — ver
SPEC-auto-mute-outros-apps.md para o raciocínio completo (inclusive por que
capturar só o áudio do Teams por processo foi descartado).

Usa `pycaw` (wrapper de `IAudioSessionManager2` / `IAudioSessionControl2`,
a mesma infraestrutura do mixer de volume do Windows) pra enumerar as
sessões de áudio ativas no dispositivo de saída padrão, e muta via
`ISimpleAudioVolume::SetMute` quem estiver tocando som de verdade agora
(`IAudioMeterInformation::GetPeakValue() > 0`) — não apenas aberto em
segundo plano.

Sem a restrição de VoIP que bloqueou a captura por processo do Teams
(SPEC-auto-mute-outros-apps.md): aqui não estamos tentando capturar áudio
de outro processo, só mutar/desmutar, então funciona pra qualquer app.
"""

import logging
import os
import sys
from dataclasses import dataclass
from typing import List

# `watcher.audio_capture` usa `soundcard`, que inicializa COM sozinho
# (multithreaded, via chamada direta ao ole32) assim que é importado, e
# depende desse modo multithreaded pra gravar em threads de fundo sem
# precisar inicializar COM nelas de novo (testado: sem isso,
# `TrackRecorder._run` quebra com CO_E_NOTINITIALIZED). Só que a
# inicialização do `soundcard` também tem uma pegadinha: ela só tolera ser
# a *primeira* a chamar CoInitializeEx no processo (trata "modo trocado"
# como não-fatal, mas não trata "já inicializado no mesmo modo" — trata
# como erro genérico e derruba o import). Então, nessa ordem:
#   1. Garantir que `soundcard` seja importado (e inicialize COM) antes de
#      `comtypes` — não importa quem importou este módulo primeiro.
#   2. Fixar `sys.coinit_flags` pra multithreaded antes do primeiro
#      `import comtypes` do processo, senão o próprio comtypes tenta
#      inicializar COM como apartment-threaded e quebra por conflito de
#      modo (testado: reproduz com `import comtypes` isolado após
#      `soundcard`, mesmo sem nada deste módulo).
import soundcard  # noqa: F401 - import só para ordenar a inicialização de COM, ver acima

if not hasattr(sys, "coinit_flags"):
    sys.coinit_flags = 0  # COINIT_MULTITHREADED

import comtypes
from pycaw.api.endpointvolume import IAudioMeterInformation
from pycaw.utils import AudioSession, AudioUtilities

logger = logging.getLogger("voice_recorder")


def _call_app_exe_names(active_apps: List[str]) -> List[str]:
    """Extrai o nome do executável (ex: "ms-teams.exe") de cada rótulo do
    MicWatcher, pra excluir o app da call da lista de mutados. Rótulos
    "NonPackaged\\..." carregam o caminho inteiro com '#' no lugar de
    barras (mesmo formato de main.py:_derive_source_name); apps UWP não
    têm caminho nesse formato, então ficam de fora dessa lista — aceitável,
    porque o app de call normalmente não é UWP."""
    prefix = "NonPackaged\\"
    names = []
    for label in active_apps:
        if label.startswith(prefix):
            exe_name = label[len(prefix):].split("#")[-1]
            if exe_name:
                names.append(exe_name.lower())
    return names


@dataclass
class _MutedSession:
    pid: int
    exe_name: str
    session: AudioSession


class AudioMuter:
    """Guarda exatamente quais sessões foram mutadas por esta classe, pra
    restaurar só essas — nunca desmuta algo que já estava mudo antes da
    call (ex: o usuário mutou o Spotify de propósito), e não quebra se o
    app fechar antes do fim da call (mute de sessão não sobrevive ao
    processo, então não há dano permanente possível)."""

    def __init__(self) -> None:
        self._muted: List[_MutedSession] = []

    def mute_other_apps(self, active_apps: List[str]) -> None:
        if self._muted:
            logger.warning(
                "AudioMuter.mute_other_apps chamado com %d sessão(ões) já "
                "mutada(s) pendente(s) de restaurar; ignorando pra não "
                "perder o rastro de como desfazer.",
                len(self._muted),
            )
            return

        own_pid = os.getpid()
        call_exe_names = _call_app_exe_names(active_apps)

        try:
            comtypes.CoInitialize()
        except OSError:
            pass  # já inicializado nesta thread — comtypes trata como sucesso

        try:
            sessions = AudioUtilities.GetAllSessions()
        except comtypes.COMError:
            logger.exception("Falha ao enumerar sessões de áudio; nenhum app será mutado.")
            return

        for session in sessions:
            pid = session.ProcessId
            if pid == 0 or pid == own_pid:
                continue  # sessão do sistema, ou o próprio voice-recorder

            process = session.Process
            exe_name = process.name() if process else None
            if exe_name and exe_name.lower() in call_exe_names:
                continue  # o app da call (Teams/WhatsApp/etc) — não muta ele mesmo

            try:
                meter = session._ctl.QueryInterface(IAudioMeterInformation)
                peak = meter.GetPeakValue()
            except comtypes.COMError:
                continue  # sessão sumiu entre a enumeração e aqui

            if peak <= 0.0:
                continue  # não está tocando som agora — nada a mutar

            try:
                volume = session.SimpleAudioVolume
                if volume.GetMute():
                    continue  # já estava mudo — não é nosso pra restaurar depois
                volume.SetMute(True, None)
            except comtypes.COMError:
                logger.exception(
                    "Falha ao mutar %s (pid %s).", exe_name or "processo desconhecido", pid
                )
                continue

            self._muted.append(_MutedSession(pid=pid, exe_name=exe_name or str(pid), session=session))
            logger.info("App mutado automaticamente pra call: %s (pid %s).", exe_name, pid)

    def restore_muted_apps(self) -> None:
        muted, self._muted = self._muted, []
        if not muted:
            return

        try:
            comtypes.CoInitialize()
        except OSError:
            pass

        restored = 0
        for entry in muted:
            try:
                entry.session.SimpleAudioVolume.SetMute(False, None)
                restored += 1
            except comtypes.COMError:
                # Processo fechou (ex: crash) ou a sessão não existe mais —
                # sem dano possível, já que mute de sessão não sobrevive ao
                # processo. Só seguir pras próximas.
                logger.info(
                    "Sessão de %s (pid %s) não existe mais ao restaurar; ignorando.",
                    entry.exe_name,
                    entry.pid,
                )
                continue

        logger.info("%d app(s) desmutado(s) após fim da call.", restored)
