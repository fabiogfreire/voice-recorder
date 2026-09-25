# SPEC — Correção da falha de transcrição (25/09/2026)

## Contexto

Todas as gravações desde 23/09 ficaram com `status = error` e nenhuma transcrição foi
gerada. A UI mostra apenas "error", sem motivo — o diagnóstico só foi possível
reproduzindo a chamada da API à mão.

## Causa raiz (confirmada por teste real com a chave em uso)

A chave configurada é de projeto (`sk-proj-…`, projeto `proj_3YnG1DNv1ufHgb1bFZPP2fcg`)
e esse projeto tem acesso a **um único** modelo de transcrição. Os dois que o código
usa estão bloqueados:

| Modelo | Onde é usado | Resultado do teste |
|---|---|---|
| `whisper-1` | `MODEL_WITH_TIMESTAMPS`, Modo 1 (call) | ❌ 403 `does not have access to model` |
| `gpt-transcribe` | `MODEL_PLAIN`, Modo 2 (conteúdo) | ❌ 403 `does not have access to model` |
| `gpt-4o-transcribe` | — | ✅ funciona (`json`/`text`; recusa `verbose_json`) |
| `gpt-4o-mini-transcribe` | — | ❌ 403 |
| `gpt-4o-transcribe-diarize` | — | ❌ 403 |

`client.models.list()` devolve só 2 modelos no total para essa chave.

Como `transcribe_recording()` captura qualquer `Exception` e grava só
`status="error"` (`worker.py:89-91`), o 403 nunca chegou ao usuário.

## Decisões tomadas pelo Fabio

1. **Liberar `whisper-1` no dashboard da OpenAI** (platform.openai.com → projeto →
   Limits / Model permissions). Isso restaura o Modo 1 com timestamps reais, sem
   reescrever a mescla Fabio/Outros.
2. **Não mexer nos arquivos de áudio** — o Fabio faz a limpeza manualmente.
   Os arquivos `2026-09-25_*` devem ser preservados para reprocessamento.

> ⚠️ Pré-requisito: o passo 1 precisa estar feito antes de testar o P0.
> Verificar com o script de aceite no fim deste documento.

## Escopo

O objetivo não é só destravar a transcrição — é garantir que o próximo erro apareça
na tela em vez de virar um `error` mudo.

---

### P0 — Modelos configuráveis e erro visível

**1. Tornar os modelos configuráveis** (`config.py`, `openai_client.py`)

Hoje os modelos estão hardcoded em `openai_client.py:34-35`. Trocar de modelo exige
recompilar o `.exe`. Adicionar em `config.py`, no mesmo padrão de
`get_openai_api_key()` (env var > `config.json` > default):

- `get_transcription_model_timestamps()` → default `"whisper-1"`
- `get_transcription_model_plain()` → default `"gpt-4o-transcribe"`

Note o novo default do Modo 2: `gpt-transcribe` está inacessível e `gpt-4o-transcribe`
funciona hoje, então ele passa a ser o padrão.

Em `openai_client.py`, ler esses valores em vez das constantes. Manter a validação:
se o modelo de timestamps não for `whisper-1`, `verbose_json` vai falhar — ver item 2.

**2. Propagar a mensagem de erro até a UI**

- `db.py`: adicionar coluna `error_message TEXT` ao `SCHEMA`.
  ⚠️ `init_db()` usa `CREATE TABLE IF NOT EXISTS`, que **não** altera uma tabela
  existente. O banco atual já tem 35 registros, então é preciso uma migração:
  ler `PRAGMA table_info(recordings)` e rodar
  `ALTER TABLE recordings ADD COLUMN error_message TEXT` se a coluna não existir.
- `worker.py:89-91`: no `except`, gravar `error_message=str(exc)[:500]` junto com
  `status="error"`. Limpar o campo (`error_message=None`) ao iniciar uma nova
  tentativa, para não exibir erro antigo em gravação que deu certo.
- `enqueue_transcription()` (`worker.py:99-106`): a mensagem de "chave não
  configurada" também deve ir para `error_message`, não só para o log.
- `index.html`: quando `status == "error"` e houver `error_message`, mostrar o texto
  (um `<details>` ou `title=` no status já resolve — não precisa de coluna nova).

**Critério de aceite do P0:** clicar em "Transcrever" na gravação 35 gera o `.txt`
com linhas `[MM:SS] Fabio:` / `[MM:SS] Outros:`. Se falhar, o motivo aparece na tela.

---

### P1 — Parar de ficar cego

**3. Log em arquivo** (`main.py:28`)

`logging.basicConfig` escreve só no stderr, e o `.exe` roda sem console — todo log se
perde. Adicionar um `RotatingFileHandler` apontando para
`get_app_data_dir() / "voice-recorder.log"` (sugestão: 2 MB, 3 backups), mantendo
também o handler de console para o modo dev.

**4. Validar a chave ao salvar** (`web/app.py:90-94`, `settings.html`)

Hoje a chave é salva sem nenhuma verificação, e o problema só aparece horas depois,
no fim de uma call. Ao salvar em `/settings`, fazer uma chamada leve de validação
(`client.models.list()`) e mostrar na tela:

- chave inválida → erro explícito;
- chave válida mas sem acesso aos modelos configurados → avisar **quais** faltam e
  listar os de transcrição disponíveis naquele projeto.

Isso teria mostrado o 403 no dia em que a chave foi cadastrada.

---

### P2 — Robustez (fazer se sobrar tempo, não bloqueia o P0)

**5. Gravações travadas em `recording`**

A gravação 31 está com `status = recording` desde 23/09 com `loopback_path = NULL`,
enquanto o `.wav` em disco cresceu até 793 MB. Quando o app sobe, nenhuma gravação
pode legitimamente estar com status `recording` ou `transcribing` (esses estados só
existem dentro de um processo vivo). No `init_db()`/startup do `main.py`, marcar
essas linhas como `error` com `error_message` explicando que o app foi encerrado
durante a gravação.

**6. `has_audio_signal()` carrega o arquivo inteiro na memória**
(`openai_client.py:45-52`)

`np.frombuffer(...).astype(np.float64)` em um WAV de 793 MB pede ~3,2 GB de RAM —
um `MemoryError` aqui vira outro `error` mudo. Calcular o RMS lendo em blocos
(ex: 1 milhão de frames por vez, acumulando soma dos quadrados), ou amostrar
trechos do arquivo em vez de ler tudo.

---

## Fora de escopo

- Limpeza dos 3,1 GB de áudio antigo — o Fabio faz manualmente.
- Reescrever o Modo 1 para funcionar sem timestamps (só seria necessário se o
  `whisper-1` não fosse liberado).
- Diarização (`gpt-4o-transcribe-diarize`) — segue como melhoria futura.

## Teste de aceite

Rodar antes de começar, para confirmar que o `whisper-1` foi liberado:

```bash
cd /c/DEV/voice-recorder && .venv/Scripts/python.exe -c "
import sys; sys.path.insert(0,'src')
from voice_recorder.config import get_openai_api_key
from openai import OpenAI
c = OpenAI(api_key=get_openai_api_key())
print(sorted(m.id for m in c.models.list()))
"
```

`whisper-1` precisa aparecer na lista. Depois do fix, o teste real é clicar em
**Transcrever** na gravação 35 (call do Teams de 25/09, 15,5 min, 3 trilhas) e
conferir o `.txt` gerado.
