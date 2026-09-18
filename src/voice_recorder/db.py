"""Armazenamento local (SQLite) de metadados das gravações dos dois modos."""

import sqlite3
from contextlib import contextmanager

from .paths import get_db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,                        -- 'call' ou 'content'
    source_app TEXT,                           -- app/janela identificada na origem
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'recording',  -- recording | discarded | recorded | transcribing | transcribed | error
    mic_path TEXT,
    loopback_path TEXT,
    transcript_path TEXT
);
"""


@contextmanager
def get_connection():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(SCHEMA)


def list_recordings() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM recordings ORDER BY started_at DESC"
        ).fetchall()


def create_recording(mode: str, source_app: str, started_at: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO recordings (mode, source_app, started_at) VALUES (?, ?, ?)",
            (mode, source_app, started_at),
        )
        return cur.lastrowid


def update_recording(recording_id: int, **fields) -> None:
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = [*fields.values(), recording_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE recordings SET {columns} WHERE id = ?", values)
