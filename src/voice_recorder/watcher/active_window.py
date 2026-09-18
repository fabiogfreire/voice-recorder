"""Título da janela/aba ativa no momento — usado como origem no Modo 2
(conteúdo/aula), via ctypes puro (sem depender de pywin32)."""

import ctypes
from typing import Optional


def get_active_window_title() -> Optional[str]:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return None
    buffer = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value or None
