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

## Rodar

```powershell
python -m voice_recorder.main
```

Sobe o watcher de microfone, a UI web em `http://localhost:8000` e o ícone
na bandeja do sistema, tudo no mesmo processo.

## Status

Modo 1 (call) funcionando ponta a ponta: detecção via registro do
microfone, gravação de mic e loopback em trilhas WAV separadas
(`soundcard`), salvamento em `%LOCALAPPDATA%\voice-recorder\recordings`,
metadados no SQLite, descarte manual pela bandeja ("Descartar gravação
atual"), config/chave de API e UI básica com tela de Configurações.
Testado com fala real no mic e um tom de teste no loopback — ambos
capturados sem cortes.

Nota: o mic normalmente começa a gravar ~0,3 a 1s depois do loopback
(latência de abertura do dispositivo WASAPI) — irrelevante pra calls
longas, mas vale revisitar se a mesclagem por timestamp da transcrição
exigir alinhamento mais fino.

Ainda faltam (próximos passos): notificação com botão "Não gravar"
(`windows-toasts`), modo manual de conteúdo/aula (Modo 2), worker de
transcrição (API OpenAI, mesclagem Fabio/Outros por timestamp) e
empacotamento em `.exe` (PyInstaller).
