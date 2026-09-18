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

Fundação criada: config/chave de API, banco SQLite local, detecção de call
via registro do microfone (Modo 1), UI básica com tela de Configurações.

Ainda faltam (próximos passos): captura de áudio mic+loopback (`soundcard`),
notificação com botão "Não gravar", modo manual de conteúdo/aula (Modo 2),
worker de transcrição (API OpenAI) e empacotamento em `.exe` (PyInstaller).
