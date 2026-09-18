---
name: voice-recorder
description: PRD/contexto do projeto pessoal "voice-recorder" (nomes anteriores de trabalho: meeting-scribe-local, voice-recorder-local) — app local (Windows) que detecta automaticamente quando o Fabio entra numa call (Teams, Meet, Discord, WhatsApp, Zoom etc, via uso do microfone) e grava por padrão, gravando mic e loopback em trilhas separadas pra identificar quem falou (Fabio vs Outros); além de um modo manual pra gravar conteúdo que está assistindo/ouvindo (aulas, vídeos); transcreve via API da OpenAI e disponibiliza numa UI web local sempre ativa em segundo plano.
---

# Voice Recorder — PRD

## Contexto

Origem: conversa disparada pelo Read.ai (ferramenta paga de transcrição de reuniões, R$109,90/mês no plano Pro) — Fabio quer o mesmo resultado (transcrição de reuniões como registro/status do que foi conversado) sem pagar mensalidade, usando a própria estrutura (VPS automatiza.tec.br, n8n, chaves de API já em uso no IA Jurídico) e um app local no notebook.

Uso: 100% pessoal (Fabio), sem intenção de virar produto/serviço pra clientes nem SaaS.

Decisão de escopo (18/09): em vez de gravação manual + pipeline no n8n (primeira ideia discutida), Fabio decidiu por uma solução totalmente local que resolve o problema real — esquecer de apertar "gravar". O app roda em segundo plano no notebook e detecta automaticamente quando uma call começa, pelo uso do microfone (funciona independente do app — Teams, Meet no navegador, Zoom, Discord, WhatsApp Desktop etc.).

Ampliação de escopo (18/09): além de calls, Fabio quer usar o mesmo app pra gravar **conteúdo que está assistindo/ouvindo** (ex: aulas de cursos), transcrever e depois compartilhar os trechos relevantes com o Claude no Segundo Cérebro.

Nome do projeto: passou por `meeting-scribe-local` → `voice-recorder-local` → **`voice-recorder`** (nome final, mais simples).

Fluxo de trabalho: PRD e decisões de produto no Segundo Cérebro (Cowork); desenvolvimento no Antigravity — mesma estratégia do IA Jurídico.

Repositório: `C:\DEV\voice-recorder`.

## Decisões de arquitetura (18/09)

### Modo 1 — Call (automático)

- **Gatilho de detecção**: monitorar o uso do microfone no Windows (o registro guarda, por app, se o microfone está em uso agora — `HKEY_CURRENT_USER\...\CapabilityAccessManager\ConsentStore\microphone`) — sinal confiável e independente de qual app está em uso. Quando o mic fica ativo, o app entende que uma call começou.
- **Gravação por padrão, com opt-out — não opt-in**: ao detectar o mic ativo, o app **já começa a gravar direto**, sem esperar confirmação nenhuma. Ao mesmo tempo sobe uma notificação do Windows avisando que está gravando (com o app de origem identificado) e um botão **"Não gravar"**. Esse botão fica disponível não só na notificação inicial, mas o tempo todo durante a call, também pelo ícone da bandeja do sistema ("Descartar gravação atual") — porque o Fabio pode estar compartilhando tela, ou simplesmente não ver o popup a tempo, e nesses casos é pior perder a gravação do que gravar algo que depois será descartado. Só quando o Fabio clica em "Não gravar" (a qualquer momento) é que a gravação em andamento é interrompida e o áudio descartado. **Regra geral: por definição o sistema grava; é o Fabio quem precisa avisar quando não é pra gravar — nunca o contrário.**
- **Sem exceção por app**: descartada a ideia de lista configurável tipo "nunca perguntar" pra Discord/WhatsApp ou "sempre gravar" pra Teams — o Fabio pode ter call de trabalho tanto no WhatsApp/Discord quanto call pessoal no Teams, então classificar por app não reflete o uso real. O único controle é o botão "Não gravar" a qualquer momento da call.
- **Captura de áudio em trilhas separadas**: em vez de mesclar loopback (outros participantes) e microfone (Fabio) num único áudio, gravar as **duas trilhas separadamente**. Biblioteca `soundcard` (Python) suporta loopback nativamente no Windows via WASAPI, sem driver extra tipo Stereo Mix.
- **Identificação de quem falou — "Fabio" vs "Outros"**: como as trilhas já são gravadas separadas por natureza (mic = só o Fabio, loopback = só os outros participantes), a identificação não depende de reconhecimento de voz nem do modelo `diarize` — cada trilha é transcrita separadamente (duas chamadas à API), e o resultado final é montado juntando as duas por timestamp, num único `.txt` com o formato:
  ```
  [00:03] Fabio: ...
  [00:07] Outros: ...
  ```
  Isso cobre 100% do caso de uso do MVP com precisão garantida por construção, sem custo extra de um modelo especializado. Separar **entre os próprios outros participantes** (ex: numa call de grupo, saber se foi o João ou a Maria que falou) é resolvido pelo modelo `gpt-4o-transcribe-diarize` aplicado só na trilha de loopback — fica como melhoria futura, não necessário pro MVP.
- **Identificação do app de origem**: no momento em que o mic ativa, o app varre processos/janelas ativas (`Teams.exe`, título contendo "Meet", "Zoom Meeting", `Discord.exe`, `WhatsApp.exe` etc.) e usa isso pra compor a notificação e registrar a origem no nome do arquivo. Se não reconhecer, marca "Desconhecido" — mas grava normalmente (identificação de origem é só metadado, nunca condição pra gravar ou não).

### Modo 2 — Conteúdo/Aula (manual)

- **Gatilho**: manual, não por microfone — disparado pelo Fabio via atalho de teclado global ou pelo ícone da bandeja ("Gravar isso"), já que aqui não é uma call com outra pessoa. Ele mesmo inicia e encerra.
- **Captura de áudio**: só loopback do sistema (o que está tocando — vídeo/áudio da aula), sem microfone, numa trilha só. Não precisa de tag "Fabio"/"Outros" (não é conversa) nem de botão "Não gravar"/notificação de consentimento, já que quem iniciou foi o próprio Fabio de propósito.
- **Origem**: registrada com o título da janela/aba ativa no momento (ex: nome do curso/site) quando possível, ou um rótulo genérico "Conteúdo" se não identificar.
- **Armazenamento**: fica na mesma pasta padrão das gravações de call — Fabio move manualmente pra pasta do Segundo Cérebro as transcrições que achar relevante compartilhar depois.

### Comum aos dois modos

- **Transcrição**: via **API da OpenAI direto** (conta própria em platform.openai.com, com crédito ativo) — sem passar por OpenRouter, já que pra uma chamada isolada como transcrição não há ganho em agregador, só margem extra no preço. Modelos disponíveis na API: `gpt-transcribe` (uso geral, ~US$0,0045/min), `gpt-4o-mini-transcribe` (mais barato, cobrado por token — diferença irrelevante no volume pessoal), `gpt-4o-transcribe-diarize` (identifica falantes distintos dentro de uma trilha — melhoria futura), `whisper-1` (legado). Chave de API criada em platform.openai.com → API Keys, nome `voice-recorder`.
  - **Correção de arquitetura (18/09, testado com chave real)**: `gpt-transcribe` **não aceita** `response_format=verbose_json` (erro 400 "not compatible") — só devolve o texto inteiro num bloco só, sem timestamp por segmento. Isso inviabiliza a mesclagem cronológica Fabio/Outros do Modo 1, que depende de saber o instante de cada frase dentro da trilha. `whisper-1` foi testado e suporta `verbose_json` com segmentos normalmente. **Modelo final: `whisper-1` no Modo 1** (call, onde a mescla por timestamp é essencial) e **`gpt-transcribe` no Modo 2** (conteúdo/aula, trilha única, sem necessidade de timestamp — mais novo/barato onde não há perda).
- **Armazenamento**: transcrição salva em `.txt` por gravação (formato de nome sugerido: `AAAA-MM-DD_HHhMM_<origem>.txt`), guardando também o áudio original (ou descartando após transcrever — decisão pendente) numa pasta local dedicada.
- **UI e execução**: interface web local (FastAPI + frontend simples), rodando **sempre em segundo plano** dentro do mesmo processo do watcher — nunca precisa ser "iniciada" manualmente. Só abre o navegador em `localhost` quando quiser consultar. Empacotar tudo (watcher + servidor) num único `.exe` via PyInstaller, com atalho na pasta de Inicialização do Windows (ou Agendador de Tarefas) pra subir sozinho no boot, e um ícone na bandeja do sistema (`pystray`) com menu simples (Abrir UI / Gravar isso [modo aula] / Descartar gravação atual / Pausar detecção / Sair).
- **Resumo automático**: tratado como **próximo incremento natural** assim que a captura+transcrição estiver validada no dia a dia — é só mais uma chamada à mesma API da OpenAI usando o `.txt` já gerado, não um módulo novo. Continua fora do MVP inicial, mas não deve ficar esquecido depois.
- **Escopo do MVP**: gravação automática de call (opt-out, trilhas separadas Fabio/Outros) + modo manual de conteúdo/aula + detecção de origem + botão "Não gravar" (só no modo call) + transcrição (`whisper-1` no call, mesclada por timestamp; `gpt-transcribe` no conteúdo) + UI de consulta.

## Componentes técnicos (rascunho)

1. **Watcher + servidor** (processo único em background, inicia com o Windows via Startup/Agendador, empacotado como `.exe` com PyInstaller, ícone na bandeja via `pystray`): monitora uso do microfone (modo call), expõe atalho/menu pra iniciar gravação manual (modo conteúdo), captura áudio em trilhas separadas (mic + loopback no modo call; só loopback no modo conteúdo, via `soundcard`), salva arquivos + metadata (app/origem, timestamp, duração, modo) num SQLite local, e já sobe o servidor FastAPI da UI dentro do mesmo processo.
2. **Módulo de notificação/descarte** (só modo call): notificação interativa do Windows (`windows-toasts`/`winrt`) com botão "Não gravar", mais a mesma ação no menu da bandeja durante toda a call.
3. **Worker de transcrição**: pega gravações não descartadas, transcreve cada trilha separadamente via API da OpenAI (`gpt-transcribe`), e no modo call mescla as duas transcrições por timestamp num único `.txt` com tag "Fabio"/"Outros"; no modo conteúdo, gera o `.txt` direto (trilha única). Atualiza status no SQLite.
4. **UI web local** (FastAPI + HTML/JS simples, servida por esse mesmo processo sempre ativo): lista gravações/transcrições dos dois modos, permite ouvir/ler, e uma tela de Configurações (dispositivo de microfone/saída a monitorar).

## Pendências / decisões em aberto

- [x] Criar a chave de API na OpenAI (platform.openai.com → API Keys) dedicada a esse projeto — feito em 18/09/2026.
- [ ] Manter o áudio bruto salvo (ocupa espaço, mas permite reprocessar/ouvir de novo) ou descartar depois de transcrever (mais leve, mas perde o original) — vale considerar manter as duas trilhas separadas se guardar.
- [ ] Formato de organização das pastas de transcrição (por data? por app? por mês? separar call de conteúdo/aula?).
- [ ] Definir o atalho de teclado global pro modo manual (gravar conteúdo/aula) — por ora, só via ícone da bandeja ("Gravar isso").
- [ ] Avaliar `gpt-4o-transcribe-diarize` como upgrade futuro, aplicado só na trilha de loopback, pra separar entre si os participantes externos numa call de grupo.
- [ ] Empacotar em `.exe` (PyInstaller) + atalho de Inicialização do Windows — próximo passo.

## Status

- 18/09/2026: PRD inicial criado e evoluído. Decisões de arquitetura fechadas: gatilho por uso do microfone pra calls (gravação automática opt-out, com botão "Não gravar" sempre disponível), mic e loopback gravados em trilhas separadas e transcritos/mesclados por timestamp com tag "Fabio"/"Outros", modo manual adicional pra gravar conteúdo/aulas, sem lista de exceções por app, transcrição via API da OpenAI direto, UI web local rodando sempre em segundo plano (mesmo processo do watcher, empacotado como `.exe` + ícone na bandeja), resumo automático tratado como próximo incremento natural.
- 18/09/2026: Modo 1 (call) implementado e testado de ponta a ponta com áudio real — detecção via registro do microfone, captura mic+loopback (`soundcard`), notificação "Não gravar" (`windows_toasts`), descarte pela bandeja, e transcrição via API da OpenAI. Modo 2 (conteúdo/aula) implementado via bandeja ("Gravar isso"), testado com uma janela real do Chrome. Corrigido em teste real: `gpt-transcribe` não aceita timestamps por segmento — trocado por `whisper-1` no Modo 1 (mescla por timestamp) mantendo `gpt-transcribe` no Modo 2. Repositório versionado em git (local, remoto ainda pendente no automatizatec). Falta: empacotamento em `.exe`.
- Cópia de referência mantida também no Segundo Cérebro (Cowork), em `pessoal/voice-recorder.md`.
- Última atualização: 18/09/2026
