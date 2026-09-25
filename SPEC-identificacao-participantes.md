# SPEC — Identificar participantes por nome (sem diarização) (25/09/2026)

> ⚠️ **Sequenciamento:** toca em `main.py`, `db.py` e no worker de transcrição —
> mesmos arquivos das specs `SPEC-auto-mute-outros-apps.md` e
> `SPEC-transcricao-sob-demanda.md`. Implementar depois que essas duas estiverem
> commitadas, para não conflitar.

## Contexto

Hoje toda fala que não é do Fabio cai como "Outros" — a separação é por trilha
(mic = Fabio, loopback = todo o resto), não por voz. Numa call 1:1 (a maioria das
do WhatsApp e boa parte das do Teams), "Outros" é sempre a mesma pessoa, mas o
transcript não diz quem é.

**Separar múltiplas pessoas dentro de "Outros" por voz (diarização) está fora de
escopo aqui.** Requer `gpt-4o-transcribe-diarize`, que o Fabio liberou no dashboard
da OpenAI mas que retorna 403 de forma consistente (4/4 tentativas) — problema
documentado e reportado por outros desenvolvedores (checkbox de acesso no projeto
não reflete acesso real, aparentemente por um controle adicional de conta/tier que
o toggle não cobre). Não é bug nosso, não adianta tentar de novo por enquanto. Fica
pendente até esse acesso realmente funcionar; revisitar depois.

## Objetivo desta spec

Sem diarização, dá pra resolver bem o caso mais comum (call 1:1) e melhorar
parcialmente o caso de grupo:

- **Call 1:1** (contagem de participantes = 1 além do Fabio): trocar "Outros" pelo
  nome real da pessoa no `.txt` inteiro. Sem ambiguidade — não tem outra voz pra
  confundir.
- **Call em grupo** (2+ participantes além do Fabio): não dá pra saber com certeza
  quem falou cada linha sem diarização. O que dá pra fazer: listar os nomes reais
  dos participantes no topo do transcript (você sabe quem estava na call, mesmo
  que as falas continuem agrupadas em "Outros"), e opcionalmente tentar uma
  atribuição heurística por contexto textual (ver Mecanismo B) — sempre deixando
  claro que é um palpite, não uma certeza.

## Mecanismo A — Capturar nomes reais via UI Automation

Ler a lista de participantes diretamente da árvore de acessibilidade do Windows
(a mesma interface que leitores de tela usam) — **100% local, sem Graph API, sem
aprovação de administrador do Office**.

**Teams:** o cliente novo (`ms-teams.exe`) é baseado em WebView2/Chromium. A árvore
de acessibilidade dele tem estrutura diferente de um app Win32 clássico — precisa
de um protótipo exploratório (igual fizemos com o process loopback) pra achar onde
o painel de participantes fica exposto, antes de escrever código de produção. Usar
`uiautomation` (mesmo padrão ctypes/COM já validado no projeto) ou `pywinauto` com
backend UIA.

**WhatsApp Desktop:** mais simples — o título da janela durante uma call
normalmente mostra o nome do contato ou do grupo. Ler via `GetWindowText`
(muito mais leve que percorrer árvore de acessibilidade). Hoje o `source_app`
salvo no banco (`db.py`) é um identificador técnico da janela (ex:
`5319275A.WhatsAppDesktop_cv1g1gvanyjgm`), não o nome amigável — precisa capturar
o texto do título separadamente.

**Quando rodar:** durante a call, disparado em `on_call_start()` (`main.py:106`),
já que o painel de participantes só existe com a call ativa. Guardar o resultado
(lista de nomes) e persistir ao final da gravação.

**Fallback obrigatório:** se a captura falhar (layout mudou, elemento não
encontrado, app fechou antes), degradar para o comportamento de hoje ("Outros")
sem quebrar a gravação nem a transcrição. Nunca bloquear o pipeline por causa
disso.

## Mecanismo B — Atribuição por contexto (best-effort, só grupo)

Só entra em jogo quando UI Automation capturou 2+ nomes (grupo) e o Fabio pedir
explicitamente (botão na UI, não automático — mesma filosofia de custo sob
demanda da `SPEC-transcricao-sob-demanda.md`, já que aqui entraria uma chamada de
LLM).

Uma única chamada ao `gpt-4o-mini` (já liberado, texto puro, sem áudio) recebendo
o transcript mesclado + a lista de nomes capturados, pedindo pra relabelar linhas
`Outros:` com o nome mais provável quando o contexto deixar claro (ex: alguém é
chamado pelo nome logo antes de falar), e manter `Outros:` quando não der pra
saber. Resultado é heurístico — deixar isso explícito na UI (ex: nomes sugeridos
em itálico ou com um aviso "atribuição automática, pode errar").

## Mudanças de dados

- `db.py`: nova coluna `participants TEXT` (JSON: lista de nomes capturados via UI
  Automation; lista vazia se falhou ou não achou ninguém). Mesma mecânica de
  migração já estabelecida (`_migrate()` com `PRAGMA table_info` + `ALTER TABLE`).
- Novo módulo `src/voice_recorder/watcher/participants.py`: função
  `capture_participants(source_app: str) -> list[str]`, despachando para o
  extrator certo (Teams vs WhatsApp) conforme o app detectado. Apps não
  suportados retornam lista vazia (comportamento atual, sem regressão).
- Merge do transcript (`worker.py::_merge_call_segments`): se `len(participants) == 1`,
  substitui `"Outros"` pelo nome real na hora de montar o `.txt`. Se `len >= 2`,
  adiciona um cabeçalho `Participantes: Nome1, Nome2, Nome3` antes do transcript
  e mantém `"Outros"` no corpo (a menos que o Mecanismo B tenha rodado).

## Fora de escopo

- Diarização / separar vozes dentro de "Outros" — bloqueado no acesso à API,
  revisitar quando resolver com a OpenAI.
- `known_speaker_references` (matching por amostra de voz) — mesma dependência do
  modelo bloqueado.
- Extração de participantes para apps além de Teams e WhatsApp — adicionar depois,
  sob demanda, seguindo o mesmo padrão de despacho por `source_app`.

## Teste de aceite

1. Call 1:1 no WhatsApp com um contato de teste: `.txt` final mostra o nome real
   no lugar de "Outros" em todas as linhas.
2. Call de teste 1:1 no Teams: mesmo resultado.
3. Call em grupo no Teams (3+ pessoas): cabeçalho lista os nomes reais; corpo
   mantém "Outros" até o Fabio clicar em "Identificar participantes" (Mecanismo B).
4. Simular falha da UI Automation (ex: fechar a janela do Teams assim que a call
   começa): gravação e transcrição completam normalmente, caindo em "Outros" como
   hoje — sem crash, sem trava.
