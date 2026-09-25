"""Identidade compartilhada das notificações do app.

`InteractableWindowsToaster` sem um `notifierAUMID` explícito usa por
padrão o AUMID do **cmd.exe** (`{1AC14E77-...}\\cmd.exe` — ver
`windows_toasts/toasters.py`, `InteractableWindowsToaster.__init__`).
Isso é uma identidade genérica compartilhada por qualquer processo no
sistema que use essa mesma conveniência da biblioteca pra mostrar toasts
interativos sem registrar um app de verdade — inclusive processos
`cmd.exe` avulsos, de qualquer script, rodando ao mesmo tempo.

Suspeita levantada por um caso real: gravações de call descartadas
sozinhas, sem o Fabio ter clicado em "Não gravar" (main.py:on_discard_current
disparado poucos milissegundos depois da call começar, em 3 ocasiões
diferentes, com apps diferentes). O Windows Shell roteia o clique num
botão de notificação pelo AUMID, não necessariamente pelo processo exato
que criou aquela notificação — com uma identidade genérica e compartilhada,
não dá pra descartar cruzamento com outro processo usando o mesmo AUMID
por coincidência. Não foi possível reproduzir sob demanda pra confirmar
100%, mas registrar uma identidade PRÓPRIA pro Voice Recorder elimina essa
categoria inteira de interferência, é a prática correta de qualquer
forma, e é barato o suficiente pra valer a pena mesmo sem certeza
absoluta da causa raiz."""

import ctypes
import logging

logger = logging.getLogger("voice_recorder")

APP_USER_MODEL_ID = "FabioGomesFreire.VoiceRecorder"

_shell32 = ctypes.windll.shell32
_shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]


def register_app_identity() -> None:
    """Registra o AUMID próprio pro processo atual — precisa rodar bem no
    início, antes de qualquer toast ser criado. Chamado 1x em main.py.
    Falha (silenciosa, só loga) não deve impedir o app de subir: pior
    caso, as notificações voltam a usar a identidade genérica do
    cmd.exe, igual hoje."""
    try:
        hr = _shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        if hr != 0:
            logger.warning("SetCurrentProcessExplicitAppUserModelID falhou (hr=%s).", hr)
    except OSError:
        logger.exception("Falha ao registrar AUMID do app.")
