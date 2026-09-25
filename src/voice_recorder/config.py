"""Resolução de configurações (hoje só a chave da OpenAI).

Prioridade de leitura: variável de ambiente (.env, útil em dev) > config.json
salvo pela tela de Configurações da UI (uso real, pós-empacotamento em .exe).
"""

import json
import os
from typing import Optional

from dotenv import load_dotenv

from .paths import get_config_path

load_dotenv()


def _read_config() -> dict:
    path = get_config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_config(data: dict) -> None:
    get_config_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_openai_api_key() -> Optional[str]:
    env_key = os.getenv("OPENAI_API_KEY")
    if env_key:
        return env_key
    return _read_config().get("openai_api_key") or None


def save_openai_api_key(key: str) -> None:
    data = _read_config()
    data["openai_api_key"] = key.strip()
    _write_config(data)


def get_transcription_model_timestamps() -> str:
    """Modelo do Modo 1 (call), precisa suportar `verbose_json` com
    timestamp por segmento. Só `whisper-1` faz isso hoje."""
    env_value = os.getenv("TRANSCRIPTION_MODEL_TIMESTAMPS")
    if env_value:
        return env_value
    return _read_config().get("transcription_model_timestamps") or "whisper-1"


def get_transcription_model_plain() -> str:
    """Modelo do Modo 2 (conteúdo), trilha única sem timestamp."""
    env_value = os.getenv("TRANSCRIPTION_MODEL_PLAIN")
    if env_value:
        return env_value
    return _read_config().get("transcription_model_plain") or "gpt-4o-transcribe"
