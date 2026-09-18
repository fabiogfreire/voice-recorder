"""Caminhos usados pelo app, sempre em %LOCALAPPDATA%\\voice-recorder — assim
funcionam igual em modo dev e depois de empacotado como .exe, sem depender
de onde o executável está instalado."""

import os
from pathlib import Path


def get_app_data_dir() -> Path:
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
