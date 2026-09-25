"""Captura os nomes reais dos participantes de uma call via UI Automation
— a mesma árvore de acessibilidade que leitores de tela usam. 100% local,
sem Graph API, sem aprovação de administrador do Office. Ver
SPEC-identificacao-participantes.md.

Diarização (separar vozes dentro de "Outros") está FORA de escopo — depende
de `gpt-4o-transcribe-diarize`, que retorna 403 mesmo liberado no dashboard
da OpenAI (problema de provisionamento do lado deles, não daqui). Isto aqui
resolve só a identificação de QUEM está na call, não QUEM falou cada linha.

Protótipo exploratório feito ao vivo (Teams e WhatsApp reais, antes deste
código) achou duas estruturas bem diferentes:

- **Teams** (`ms-teams.exe`, WebView2/Chromium — árvore de acessibilidade
  bem mais profunda e menos nomeada que um app Win32 clássico): o botão
  "Pessoas" da barra de controles da call (`AutomationId='roster-button'`)
  abre um painel com um `TreeControl` chamado "Participantes"; cada
  `TreeItemControl` dentro dele representa um participante. O `.Name` do
  item mistura nome + papel/badge + status do mudo, mas SEM separador
  consistente: pra membro da organização vem "Nome, Papel, Mudo
  desativado" (3 campos, dá pra splitar por vírgula), mas pra convidado
  externo (entrou só pelo link, sem conta) vem "Nome Não verificado, Mudo
  desativado" (testado ao vivo com uma call real de 2 pessoas) — o nome e
  o badge "Não verificado" vêm colados sem vírgula entre eles, então
  splitar por vírgula pega "Nome Não verificado" inteiro como nome.
  Caminho mais confiável, também achado nessa call real: dentro do item,
  o `GroupControl` do avatar (`AutomationId` começando com
  "roster-avatar-img-") tem um `TextControl` filho só com o nome puro,
  sem badge nenhum — é o texto alternativo da foto de perfil.

- **WhatsApp Desktop**: ao contrário do que a spec assumia, o título da
  janela principal (`GetWindowText`) NÃO mostra o nome do contato durante
  uma ligação — testado ao vivo, fica travado em "Voice call" o tempo
  todo. O nome real está numa janela SEPARADA (criada só enquanto a call
  dura), num `TextControl` com `AutomationId='CallerTextBlock'` por
  participante (suporta grupo: um item por pessoa). A janela principal
  sempre se chama exatamente "WhatsApp" (testado, mesmo com Windows em
  PT-BR — o app roda em inglês); qualquer outra janela de topo do mesmo
  processo é a janela de call.

Fallback obrigatório: qualquer falha (layout mudou, elemento não achado,
app fechado, timeout) retorna lista vazia — o merge do transcript já trata
lista vazia como "sem nomes capturados", caindo em "Outros" como hoje.
Nunca propaga exceção pro chamador.

`watch_participants()` faz polling durante a call inteira, não só uma
janela fixa no início — testado ao vivo (call real, 2 min): uma pessoa
convidada pode demorar bem mais que alguns segundos pra entrar (achar o
link, o Fabio ajudar por telefone, etc.), e uma tentativa única cedo
demais simplesmente não vê ninguém. Os nomes vistos se ACUMULAM (nunca
somem da lista, mesmo que a pessoa saia antes do fim). Pro Teams, o
painel de participantes só é aberto UMA VEZ e fica aberto durante todo o
polling — abrir/fechar a cada tentativa, a cada poucos segundos, pela
call inteira, ficaria piscando na tela de quem está participando."""

import ctypes
import logging
import time
from ctypes import wintypes
from typing import Callable, List, Optional

import psutil
import uiautomation as auto

logger = logging.getLogger("voice_recorder")

# Curto de propósito: cada leitura roda dentro de um polling que se repete
# a cada poucos segundos — um app travado ou com layout quebrado não pode
# segurar esse laço por muito tempo a cada rodada.
_UIA_TIMEOUT_SECONDS = 2.0

_secur32 = ctypes.windll.secur32
_NAME_DISPLAY = 3  # EXTENDED_NAME_FORMAT.NameDisplay


def _get_local_display_name() -> Optional[str]:
    """Nome de exibição da conta corporativa/AzureAD logada — testado na
    prática: bate exatamente com o nome que o Teams mostra pro próprio
    Fabio no painel de participantes ("Fabio Gomes Freire"), sem precisar
    de configuração nova. Usado só pra excluir a si mesmo da lista."""
    try:
        size = wintypes.ULONG(0)
        _secur32.GetUserNameExW(_NAME_DISPLAY, None, ctypes.byref(size))
        if size.value == 0:
            return None
        buf = ctypes.create_unicode_buffer(size.value)
        if not _secur32.GetUserNameExW(_NAME_DISPLAY, buf, ctypes.byref(size)):
            return None
        return buf.value or None
    except OSError:
        return None


def _iter_top_level_windows(process_name_substr: str):
    root = auto.GetRootControl()
    for child in root.GetChildren():
        try:
            pid = child.ProcessId
            pname = psutil.Process(pid).name() if pid else ""
        except Exception:
            continue
        if process_name_substr.lower() in pname.lower():
            yield child


def _iter_descendants(control, depth: int = 0, max_depth: int = 15):
    if depth > max_depth:
        return
    try:
        children = control.GetChildren()
    except Exception:
        return
    for child in children:
        yield child
        yield from _iter_descendants(child, depth + 1, max_depth)


def _extract_teams_participant_name(item: auto.Control) -> Optional[str]:
    """Nome puro de um item do roster — ver docstring do módulo sobre por
    que não dá pra confiar só no `.Name` do item (mistura badge tipo "Não
    verificado" sem separador pra convidados externos)."""
    for descendant in _iter_descendants(item, max_depth=6):
        aid = descendant.AutomationId or ""
        if descendant.ControlTypeName == "GroupControl" and aid.startswith("roster-avatar-img-"):
            for child in descendant.GetChildren():
                if child.ControlTypeName == "TextControl" and child.Name:
                    return child.Name.strip()

    # Fallback se o layout do avatar mudar: melhor um nome com lixo
    # (badge colado) do que nenhum nome.
    label = item.Name or ""
    name = label.split(",")[0].strip()
    return name or None


def _read_teams_roster(window: auto.Control) -> Optional[List[str]]:
    """Lê o painel de participantes SE ele já estiver aberto — não abre
    nem fecha nada (isso é responsabilidade de quem chama). Retorna
    `None` (distinto de lista vazia) quando o painel não está aberto, pra
    quem chama saber que precisa abrir antes de tentar de novo."""
    tree = window.TreeControl(Name="Participantes", searchDepth=30)
    if not tree.Exists(0.3):
        return None

    names: List[str] = []
    for item in tree.GetChildren():
        if item.ControlTypeName != "TreeItemControl":
            continue  # ex: o GroupControl com o cabeçalho "Nesta reunião, N total"
        name = _extract_teams_participant_name(item)
        if name:
            names.append(name)

    own_name = _get_local_display_name()
    if own_name:
        names = [n for n in names if n.strip().lower() != own_name.strip().lower()]

    return names


def _capture_teams() -> List[str]:
    """Leitura avulsa (abre o painel se precisar, lê, fecha de novo) —
    usada só fora do fluxo real de gravação (ex: testes manuais). O fluxo
    real (`main.py`) usa `watch_participants`/`_watch_teams`, que mantém
    o painel aberto durante toda a call em vez de abrir/fechar a cada
    leitura."""
    window = next(_iter_top_level_windows("ms-teams.exe"), None)
    if window is None:
        return []

    roster_btn = window.ButtonControl(AutomationId="roster-button", searchDepth=30)
    if not roster_btn.Exists(_UIA_TIMEOUT_SECONDS):
        return []  # sem call ativa com esses controles, ou layout mudou

    names = _read_teams_roster(window)
    opened_here = names is None
    try:
        if opened_here:
            roster_btn.GetInvokePattern().Invoke()
            names = _read_teams_roster(window) or []
        return names or []
    finally:
        if opened_here:
            roster_btn.GetInvokePattern().Invoke()  # fecha de novo, deixa como achou


def _capture_whatsapp() -> List[str]:
    call_window = None
    for window in _iter_top_level_windows("whatsapp"):
        title = (window.Name or "").strip().lower()
        if title == "whatsapp":
            continue  # janela principal, não a de call
        call_window = window
        break

    if call_window is None:
        return []  # sem call ativa (só a janela principal aberta)

    names: List[str] = []
    for descendant in _iter_descendants(call_window):
        if descendant.AutomationId == "CallerTextBlock" and descendant.Name:
            name = descendant.Name.strip()
            if name and name not in names:
                names.append(name)

    return names


_EXTRACTORS = {
    "teams": _capture_teams,
    "whatsapp": _capture_whatsapp,
}


def capture_participants(source_app: str) -> List[str]:
    """Despacha pro extrator certo conforme `source_app` (mesmo
    identificador de janela salvo em `recordings.source_app`, ex:
    "MSTeams_8wekyb3d8bbwe" ou "5319275A.WhatsAppDesktop_cv1g1gvanyjgm").

    App não suportado, ou qualquer falha durante a captura, retorna lista
    vazia — nunca propaga exceção. Quem chama (main.py) não deve travar
    gravação nem transcrição por causa disso."""
    label = (source_app or "").lower()
    for keyword, extractor in _EXTRACTORS.items():
        if keyword not in label:
            continue
        try:
            return extractor()
        except Exception:
            logger.exception("Falha ao capturar participantes (%s).", source_app)
            return []
    return []


def _watch_generic(
    extractor: Callable[[], List[str]],
    should_continue: Callable[[], bool],
    on_update: Callable[[List[str]], None],
    interval_seconds: float,
) -> None:
    collected: List[str] = []
    while should_continue():
        try:
            found = extractor()
            logger.debug("Polling de participantes: achou %r nesta rodada.", found)
            grew = False
            for name in found:
                if name not in collected:
                    collected.append(name)
                    grew = True
            if grew:
                on_update(list(collected))
        except Exception:
            logger.exception("Falha durante polling de participantes.")
        if should_continue():
            time.sleep(interval_seconds)


def _watch_teams(
    should_continue: Callable[[], bool],
    on_update: Callable[[List[str]], None],
    interval_seconds: float,
) -> None:
    """Só abre o painel de participantes uma vez, na primeira leitura —
    ver docstring do módulo sobre por que não pode abrir/fechar a cada
    rodada de polling. Chama `on_update` a cada vez que a lista cresce —
    não dá pra esperar o polling inteiro acabar pra reportar algo: a call
    pode terminar bem depois que alguém já apareceu no roster, e quem
    chama precisa do resultado disponível antes disso (ver main.py)."""
    collected: List[str] = []
    roster_btn = None
    opened_here = False

    try:
        while should_continue():
            try:
                window = next(_iter_top_level_windows("ms-teams.exe"), None)
                if window is not None:
                    roster_btn = window.ButtonControl(AutomationId="roster-button", searchDepth=30)
                    if roster_btn.Exists(_UIA_TIMEOUT_SECONDS):
                        names = _read_teams_roster(window)
                        if names is None and not opened_here:
                            roster_btn.GetInvokePattern().Invoke()
                            opened_here = True
                            names = _read_teams_roster(window)
                        logger.debug("Polling de participantes (Teams): achou %r nesta rodada.", names)
                        grew = False
                        for name in names or []:
                            if name not in collected:
                                collected.append(name)
                                grew = True
                        if grew:
                            on_update(list(collected))
            except Exception:
                logger.exception("Falha durante polling de participantes (Teams).")
            if should_continue():
                time.sleep(interval_seconds)
    finally:
        if opened_here and roster_btn is not None:
            try:
                roster_btn.GetInvokePattern().Invoke()  # fecha de novo, deixa como achou
            except Exception:
                pass


_WATCHERS: dict = {
    "teams": _watch_teams,
    "whatsapp": lambda should_continue, on_update, interval: _watch_generic(
        _capture_whatsapp, should_continue, on_update, interval
    ),
}


def watch_participants(
    source_app: str,
    should_continue: Callable[[], bool],
    on_update: Callable[[List[str]], None],
    interval_seconds: float = 8.0,
) -> None:
    """Monitora os participantes durante toda a call (`should_continue()`
    controla até quando), em vez de uma tentativa única — ver docstring do
    módulo. Acumula todo mundo visto ao longo do polling (nunca remove
    alguém que saiu antes do fim) e chama `on_update(nomes_ate_agora)`
    cada vez que a lista cresce, pra quem chama poder persistir o
    resultado mais recente a qualquer momento, sem esperar a call acabar.
    App não suportado, ou qualquer falha, simplesmente nunca chama
    `on_update` (nunca propaga exceção).

    Chama sempre numa thread NOVA (é o próprio propósito desta função —
    ver docstring do módulo): a `uiautomation` exige inicializar COM
    explicitamente em cada thread que a usa
    (`InitializeUIAutomationInCurrentThread`), senão as chamadas não
    lançam erro nenhum, só devolvem resultados incompletos/vazios em
    silêncio — testado na prática: uma call real de 75s rodando dentro do
    app (thread nova, sem essa inicialização) não achou ninguém, embora a
    mesmíssima chamada funcionasse perfeitamente rodando na thread
    principal de um script avulso."""
    label = (source_app or "").lower()
    watcher = next((w for kw, w in _WATCHERS.items() if kw in label), None)
    if watcher is None:
        return

    auto.InitializeUIAutomationInCurrentThread()
    try:
        watcher(should_continue, on_update, interval_seconds)
    finally:
        auto.UninitializeUIAutomationInCurrentThread()
