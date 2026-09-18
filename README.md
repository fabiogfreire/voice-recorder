# Voice Recorder

Contexto completo e decisões de arquitetura em [PRD.md](PRD.md).

## Setup (dev)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e .
pip install -r requirements.txt
```

Chave da OpenAI: configure depois de rodar o app, na tela **Configurações**
(`http://localhost:8000/settings`) — ou, em modo dev, copie `.env.example`
para `.env` e preencha `OPENAI_API_KEY`.

## Rodar (dev)

```powershell
python -m voice_recorder.main
```

Sobe o watcher de microfone, a UI web em `http://localhost:8000` e o ícone
na bandeja do sistema, tudo no mesmo processo.

## Gerar o executável

```powershell
pip install pyinstaller
pyinstaller --name voice-recorder --onefile --paths src `
  --add-data "src/voice_recorder/web/templates;voice_recorder/web/templates" `
  --add-data "src/voice_recorder/web/static;voice_recorder/web/static" `
  run.py
```

Gera `dist\voice-recorder.exe`. Copie pra um local estável (ex:
`%LOCALAPPDATA%\Programs\voice-recorder\`) e crie um atalho apontando pra
lá na pasta de Inicialização do Windows
(`shell:startup`, ou `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`)
pra ele subir sozinho no boot.

## Status

**MVP completo e validado com uso real (18/09/2026):**

- **Modo 1 (call)**: detecção automática via registro do microfone,
  gravação de mic + loopback (de todos os dispositivos de saída
  simultaneamente) em trilhas separadas, notificação "Não gravar" +
  descarte pela bandeja, transcrição via API OpenAI (`whisper-1`) mesclada
  por timestamp em tags Fabio/Outros. Testado com ligações reais de
  WhatsApp — com fone, a separação Fabio/Outros fica perfeita; sem fone, o
  mic capta as duas vozes por vazamento acústico da caixa de som
  (limitação física, não de software).
- **Modo 2 (conteúdo/aula)**: manual via bandeja ("Gravar isso") ou
  automático por notificação opt-in quando detecta áudio tocando sem
  gravação em andamento. Transcrição via `gpt-transcribe`. Testado com
  vídeo real.
- **Robustez**: áudio gravado em 16kHz mono (evita estourar o limite de
  25MB da API em calls/aulas longas); trilhas maiores que isso são
  divididas em pedaços e remontadas por timestamp; trilhas sem sinal real
  são puladas (evita alucinação do Whisper em silêncio); processo do
  próprio app é excluído da detecção de microfone (bug real encontrado:
  sem isso, o app nunca detectava o fim da própria gravação).
- **Empacotamento**: `.exe` via PyInstaller, instalado em
  `%LOCALAPPDATA%\Programs\voice-recorder\`, com atalho na pasta de
  Inicialização do Windows.

Pendências abertas (ver PRD.md): manter ou descartar áudio bruto após
transcrever; formato de organização das pastas; atalho de teclado global
pro Modo 2 (hoje só via bandeja/notificação); diarização de múltiplos
participantes externos (`gpt-4o-transcribe-diarize`); resumo automático.
