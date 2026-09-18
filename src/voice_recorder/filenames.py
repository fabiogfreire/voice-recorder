"""Sanitização de texto pra uso em nomes de arquivo do Windows."""

import re

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def sanitize_for_filename(text: str, max_length: int = 60) -> str:
    return _INVALID_FILENAME_CHARS.sub("_", text).strip()[:max_length] or "Desconhecido"
