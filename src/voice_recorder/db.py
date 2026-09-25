"""Armazenamento local (SQLite) de metadados das gravações dos dois modos."""

import sqlite3
from contextlib import contextmanager
from typing import Optional

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
    transcript_path TEXT,
    error_message TEXT,
    duration_seconds REAL,                     -- calculado 1x ao fechar a gravação (header do WAV, sem ler áudio)
    billable_tracks INTEGER,                   -- trilhas com sinal de verdade (has_audio_signal), pra estimar custo
    preview_text TEXT                          -- transcrição do primeiro minuto, sob demanda — não altera `status`
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
        _migrate(conn)


_MIGRATED_COLUMNS = {
    "error_message": "TEXT",
    "duration_seconds": "REAL",
    "billable_tracks": "INTEGER",
    "preview_text": "TEXT",
}


def _migrate(conn: sqlite3.Connection) -> None:
    """`CREATE TABLE IF NOT EXISTS` não altera uma tabela já existente —
    quem já tinha o banco antes de uma coluna nova existir precisa de um
    ALTER TABLE explícito. Toda coluna adicionada depois da criação
    original da tabela entra em `_MIGRATED_COLUMNS`, em vez de criar um
    novo mecanismo de migração a cada spec."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(recordings)")}
    for name, col_type in _MIGRATED_COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE recordings ADD COLUMN {name} {col_type}")


def mark_stale_recordings_as_error() -> None:
    """`recording`/`transcribing` só existem dentro de um processo vivo — se
    uma linha está nesse estado quando o app sobe, é porque o processo
    anterior foi encerrado (crash, fechado pelo Windows, etc.) no meio de
    uma gravação ou transcrição. Chamar só no startup, nunca durante a
    operação normal do app."""
    message = "App encerrado durante a gravação/transcrição anterior."
    with get_connection() as conn:
        conn.execute(
            "UPDATE recordings SET status = 'error', error_message = ? "
            "WHERE status IN ('recording', 'transcribing')",
            (message,),
        )


def list_recordings() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM recordings ORDER BY started_at DESC"
        ).fetchall()


def get_recording(recording_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM recordings WHERE id = ?", (recording_id,)
        ).fetchone()


def create_recording(mode: str, source_app: str, started_at: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO recordings (mode, source_app, started_at) VALUES (?, ?, ?)",
            (mode, source_app, started_at),
        )
        return cur.lastrowid


def delete_recording(recording_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))


def update_recording(recording_id: int, **fields) -> None:
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = [*fields.values(), recording_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE recordings SET {columns} WHERE id = ?", values)
