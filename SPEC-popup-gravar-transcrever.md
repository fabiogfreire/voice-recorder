# SPEC — Pop-up fixo "Gravar e transcrever?" (calls e áudio tocando)

Status: implementada em 25/11/2026. Validados ao vivo os critérios 2 (com o ditado do
Claude, antes do item 9), 6 e 10; os demais ainda sem teste manual.

## Pedido

Assim que o app detectar áudio (call **ou** áudio tocando), deve aparecer um pop-up
perguntando se é para gravar e transcrever, com os botões **Sim** e **Não**. O pop-up
**fica na tela até o Fabio responder**. É uma pergunta só: **Sim** grava e transcreve
automaticamente no fim; **Não** não guarda nada.

## Como é hoje

| Evento | Hoje | Onde |
|---|---|---|
| Call detectada (Modo 1) | Grava direto. A notificação traz só "Não gravar" (opt-out) e some em ~5s | `main.on_call_start` → `notifications/call_notifier.notify_recording_started` |
| Áudio tocando (Modo 2) | Notificação com "Gravar"/"Não", que some em ~5s | `main.on_playback_detected` → `notifications/playback_notifier.notify_playback_detected` |
| Fim da gravação | Status `recorded`, à espera de alguém clicar em "Transcrever" na UI (SPEC-transcricao-sob-demanda) | `main.on_call_end`, `main.on_content_toggle` |

## Decisões

1. **Pop-up fixo = toast com `scenario=ToastScenario.Reminder`** (windows-toasts 1.3.1, já
   instalado). Esse tipo de toast não expira sozinho e só sai da tela por um botão ou pelo
   X, sem janela própria nem dependência nova. Ele exige pelo menos um botão, e já tem dois.
   - Não usar `IncomingCall` (toca o som de ligação em loop) nem `Alarm`.
   - Com o "Não incomodar" do Windows ligado, o toast vai direto para a central de
     notificações. Isso é limitação do Windows e só fica registrado no log.

2. **Call: continua gravando desde o início** e o pop-up decide o destino. Esperar o
   "Sim" perderia o começo da conversa.
   - **Sim** → a gravação fica marcada para transcrever automaticamente. Ao fim da call,
     `enqueue_transcription(recording_id)`. Se o Sim vier **depois** que a call já acabou
     (o pop-up continua na tela), enfileira na hora.
   - **Não** → descarta. Se ainda estiver gravando, usa o mesmo caminho de
     `on_discard_current`. Se a call já acabou, apaga os WAVs daquela gravação e marca
     `status="discarded"`.
   - **Sem resposta** → comportamento de hoje: a gravação termina em `recorded` e espera a
     decisão na UI.

3. **Áudio tocando: só começa a gravar depois do Sim** (igual a hoje com "Gravar").
   - **Sim** → `on_content_toggle()` inicia a gravação de conteúdo já marcada para
     transcrever. Quando o Fabio parar pela bandeja, a transcrição é enfileirada.
   - **Não** → nada (só log).
   - Não empilhar pop-ups: enquanto houver um pop-up de áudio tocando sem resposta, não
     mostrar outro.

4. **As respostas valem sempre para o `recording_id` do pop-up, nunca para "a gravação
   atual".** Como o pop-up fica na tela, o Fabio pode clicar em "Não" num pop-up antigo
   quando já existe outra call gravando. Esse clique não pode descartar a call nova.
   Hoje o `on_discard` do toast chama `on_discard_current()`, que pega a atual: corrigir.

5. **Marca de "transcrever no fim" fica em memória**, num `set[int]` protegido por lock
   em `main.py`, sem migração de banco. Se o app reiniciar no meio da gravação,
   `mark_stale_recordings_as_error()` já trata esse caso.

6. **X / arrastar para fechar** (`on_dismissed` com `UserCanceled`) = sem resposta.
   Grava no log e mantém o padrão de cada modo: a call fica em `recorded`, e o áudio
   tocando não grava.

7. O motor de transcrição continua o que estiver configurado. Esta SPEC não muda motor,
   modelo nem `config.json`.

8. **Microsoft Teams não tem pop-up** (decisão do Fabio em 25/11/2026, durante a
   implementação; vale sobre os itens 2 e 3). Call ou áudio tocando vindo do Teams
   **grava na hora e transcreve no fim**. Para descartar: bandeja ("Descartar gravação
   atual") ou a tela do app. Os demais apps seguem com o pop-up.
   - Origem da call: o app que está no microfone. Origem do áudio: sessões de áudio do
     Windows (processo do Teams ou filho dele). Teams no navegador não é reconhecido.
   - A gravação de áudio do Teams se encerra sozinha após **2 minutos sem som do Teams**.
     Parar ou descartar antes não a faz recomeçar enquanto esse silêncio não vier.
   - Se uma call do Teams começa com o áudio dele já sendo gravado, a call assume: a
     gravação de áudio é descartada se tiver menos de 60s (toque de chamada); senão é
     salva e transcrita.

9. **Claude é ignorado** (decisão do Fabio em 25/11/2026): o microfone usado pelo
   Claude (ditado) não conta como call, e áudio vindo só dele não gera pop-up. Claude
   aberto no navegador aparece como o próprio navegador.

## Textos

- Call: título **"Call detectada — {origem}"**, corpo **"Gravar e transcrever esta
  conversa?"**, botões **Sim** / **Não**.
- Áudio tocando: título **"Áudio detectado"**, corpo **"Gravar e transcrever o que está
  tocando?"**, botões **Sim** / **Não**.

## Implementação (arquivos)

- `notifications/call_notifier.py`: trocar `notify_recording_started(source_name, on_discard)`
  por `ask_record_and_transcribe_call(source_name, recording_id, on_yes, on_no)`, com
  Reminder, botões `yes`/`no` e `on_dismissed` logando. Manter o log incondicional do
  argumento recebido (caso #74 do descarte fantasma).
- `notifications/playback_notifier.py`: mesmo padrão (`on_yes`, `on_no`), com Reminder e uma
  flag de "pop-up pendente" liberada no Sim, no Não ou ao fechar.
- `main.py`:
  - `_auto_transcribe_ids: set[int]` + lock.
  - `on_call_yes(recording_id)`: se ainda está gravando, adiciona ao set; se já está
    `recorded`, chama `enqueue_transcription`.
  - `on_call_no(recording_id)`: se é a gravação atual, `on_discard_current()`; senão,
    descarta por id (apaga os arquivos do registro e marca `discarded`). Ignorar se já
    estiver transcrita ou descartada.
  - `on_call_end` / fim de `on_content_toggle`: depois do `update_recording(... "recorded")`,
    se o id estiver no set → remove e chama `enqueue_transcription(id)`.
  - `on_playback_detected`: o Sim chama `on_content_toggle()` e marca o id criado.
- A bandeja ("Descartar gravação atual", "Gravar isso") continua funcionando como hoje.

Atenção: a pasta de trabalho tem a migração do faster-whisper ainda sem commit, inclusive
em `main.py`. Implementar em cima do que estiver commitado na hora e não misturar as duas
coisas no mesmo commit.

## Critérios de aceite (teste manual)

1. Abrir uma call no WhatsApp (ou outro app que não o Teams) → o pop-up aparece e
   **continua na tela depois de 1 minuto**. O log mostra "Call detectada".
2. Sim durante a call → ao desligar, a gravação vai de `recorded` para `transcribing` e
   depois `transcribed`, sem clicar em nada na UI.
3. Não durante a call → a gravação para, fica `discarded` e os WAVs são apagados.
4. Call sem resposta, desligar e depois clicar Sim → transcrição enfileirada na hora.
5. Pop-up antigo sem resposta + nova call em andamento → Não no pop-up antigo **não** mexe
   na call nova.
6. Tocar um vídeo → pop-up fixo. Sim → começa a gravar conteúdo. Parar pela bandeja →
   transcreve sozinho. Não → nada gravado.
7. Vídeo continua tocando sem resposta → não aparece um segundo pop-up empilhado.
8. Fechar o pop-up no X → linha no log, e a gravação de call continua em `recorded`.
9. Call no Teams → nenhum pop-up; ao desligar, é transcrita sozinha.
10. Áudio tocando no Teams, fora de call → começa a gravar sem pop-up; após 2 minutos de
    silêncio para e transcreve.

## Identificador das notificações (achado na implementação)

Com o AUMID próprio adotado em 25/09, os pop-ups não apareciam na tela, sem erro. Os
notifiers voltaram ao AUMID padrão da `windows_toasts` (ver `notifications/__init__.py`).
