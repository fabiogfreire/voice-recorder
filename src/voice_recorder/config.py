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
