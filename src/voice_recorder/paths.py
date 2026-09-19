"""Caminhos usados pelo app. Por padrão em %LOCALAPPDATA%\\voice-recorder —
assim funcionam igual em modo dev e depois de empacotado como .exe, sem
depender de onde o executável está instalado.

Pra customizar (ex: usar uma pasta local do projeto em dev), defina a
variável de ambiente VOICE_RECORDER_HOME=/caminho/desejado"""

import os
from pathlib import Path


def get_app_data_dir() -> Path:
    # Se VOICE_RECORDER_HOME está definida, usar ela como base
    if custom_home := os.getenv("VOICE_RECORDER_HOME"):
        app_dir = Path(custom_home)
    else:
        base = os.getenv("LOCALAPPDATA") or str(Path.home())
        app_dir = Path(base) / "voice-recorder"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_recordings_dir() -> Path:
    recordings_dir = get_app_data_dir() / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)
    return recordings_dir


def get_db_path() -> Path:
    return get_app_data_dir() / "voice_recorder.db"


def get_config_path() -> Path:
    return get_app_data_dir() / "config.json"
