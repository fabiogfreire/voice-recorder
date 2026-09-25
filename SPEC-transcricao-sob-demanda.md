# SPEC — Transcrição sob demanda (25/09/2026)

> ⚠️ **Sequenciamento:** esta spec mexe em `main.py`, `worker.py`, `app.py`,
> `db.py` e `index.html` — os mesmos arquivos da `SPEC.md` (correção do 403).
> Só começar depois que aquele trabalho estiver commitado, para não conflitar.

## Contexto

O banco tem **35 gravações e apenas 3 transcrições** em disco. Cerca de 90% do que
é gravado nunca vira texto, mas hoje tudo é transcrito automaticamente ao fim da
gravação (`main.py:125` para call, `main.py:205` para conteúdo). O resultado é pagar
por padrão pelo que é exceção.

Medição real na call de 25/09 (15,6 min): a trilha `Altofalantes` está muda
(RMS 0) e é descartada pelo filtro de silêncio; sobram mic + `SAMSUNG` = ~31 min de
áudio → **US$ 0,19**. Barato por call, mas invisível e recorrente.

## Objetivo

Gravar sempre, transcrever só quando o Fabio decidir — e dar informação suficiente
para essa decisão ser tomada em segundos, sem abrir o áudio.

## Decisões tomadas pelo Fabio

1. **Prévia de 1 minuto**, contada **do início** da gravação (~US$ 0,012).
2. **Sem player de áudio.** A prévia em texto responde "é essa gravação?" mais
   rápido do que ouvir — ler 1 min de texto leva segundos. O player só se
   justificaria para julgar *qualidade* de áudio (volume, eco, mic errado); se essa
   necessidade aparecer na prática, vira outra spec.

---

## E1 — Parar de transcrever automaticamente

Remover as chamadas de `enqueue_transcription()` em `main.py:125` (fim de call) e
`main.py:205` (fim do modo conteúdo). A gravação termina em `status="recorded"` e
fica aguardando decisão.

O botão "Transcrever" já existe em `index.html` para o status `recorded`, então
passa a ser a única porta de entrada — não é preciso criar nada novo aqui.

**Efeito colateral positivo:** hoje, sem chave configurada, toda gravação vira
`error` sozinha (`worker.py:99-106`). Com o disparo manual, isso só acontece quando
o Fabio realmente pedir a transcrição.

---

## E2 — Mostrar duração e custo antes de decidir

Sem isso, "escolher o que transcrever" vira chute. A lista precisa responder
*quanto tempo tem* e *quanto vai custar*.

**Persistir os dados ao finalizar a gravação** (não calcular a cada page load —
seriam 35 WAVs lidos a cada refresh, e a UI já recarrega sozinha a cada 3s):

- `db.py`: duas colunas novas, `duration_seconds REAL` e `billable_tracks INTEGER`.
  Usar o mesmo `_migrate()` já criado na SPEC.md (`PRAGMA table_info` +
  `ALTER TABLE`), estendendo-o em vez de criar outro mecanismo.
- `main.py`: ao fechar a gravação (nos dois modos), calcular a duração pelo header
  do WAV (`wave.getnframes() / getframerate()`, sem ler o áudio) e contar quantas
  trilhas passam no `has_audio_signal()`. Gravar os dois valores.

**Estimativa de custo** (`openai_client.py`): constante nomeada
`_USD_PER_MINUTE = 0.006`, com comentário de que vale para `whisper-1` e
`gpt-4o-transcribe`. Função `estimate_cost_usd(duration_seconds, track_count)`.

**UI** (`index.html`): exibir duração (`15,6 min`) e custo estimado (`~US$ 0,19`)
nas linhas com status `recorded`. Custo some quando já está transcrito.

> ⚠️ `has_audio_signal()` hoje carrega o WAV inteiro em `float64`
> (`openai_client.py:45-52`) — num arquivo de 793 MB são ~3,2 GB de RAM. Se o P2 da
> SPEC.md (RMS em blocos) não tiver sido feito, **fazer antes deste item**, senão o
> fim de cada gravação passa a disparar esse pico de memória.

---

## E3 — Prévia de 1 minuto

**Recorte** (`openai_client.py`): função `_extract_clip(path, start_seconds,
duration_seconds) -> Path`, escrevendo um WAV temporário. Mesma mecânica do
`_split_into_chunks()` já existente (`setpos()` + `readframes()`), então vale
reaproveitar o padrão de `tmp_dir` + limpeza no `finally`.

**Transcrição** (`worker.py`): `transcribe_preview(recording_id)` que segue o mesmo
caminho da transcrição completa — trilhas com sinal, `whisper-1` com timestamps no
Modo 1 (mantendo `[MM:SS] Fabio:` / `[MM:SS] Outros:`) e modelo plain no Modo 2 —
só que sobre o clipe de 60s.

**Armazenamento** (`db.py`): coluna `preview_text TEXT`. É texto curto (~1-2 KB),
não justifica arquivo separado.

**Regra importante:** a prévia **não altera o `status`**. A gravação continua
`recorded` e permanece elegível para transcrição completa. Erros na prévia vão para
`error_message` sem derrubar o status.

**UI** (`index.html`, `app.py`): botão "Prévia" e rota
`POST /recordings/{id}/preview`. O texto aparece embaixo da linha (um `<details>`
resolve). Quando já existe prévia, o botão vira "Ver prévia" e não chama a API de
novo.

---

## E4 — Transcrever tudo

Fluxo que já existe, sem mudança. A prévia **não** é reaproveitada na transcrição
completa: economizaria US$ 0,012 e custaria lógica de offset e remendo de texto —
não compensa.

---

## Fora de escopo

- Player de áudio na UI (decisão 2 acima).
- Reaproveitar a prévia dentro da transcrição completa.
- Tornar o disparo automático reconfigurável — se um dia fizer falta, é um `if`
  lendo uma flag em `config.py`.

## Teste de aceite

1. Fazer uma call curta (ou usar a gravação 35). Ao terminar, ela aparece como
   `recorded`, **sem** transcrição disparada e sem chamada à API.
2. A linha mostra duração e custo estimado coerentes com o áudio.
3. Clicar em "Prévia" gera ~1 min de texto em segundos, por ~US$ 0,012, e o status
   continua `recorded`.
4. Clicar em "Transcrever" gera o `.txt` completo e o status vira `transcribed`.
5. Recarregar a página: prévia e duração seguem lá, sem nova chamada à API.
