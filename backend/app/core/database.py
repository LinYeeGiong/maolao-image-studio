from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.settings import settings


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def data_dir() -> Path:
    path = Path(settings.DATA_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def media_dir() -> Path:
    path = data_dir() / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return data_dir() / "maolao.db"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path(), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_database() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS turns (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                prompt TEXT NOT NULL,
                effective_prompt TEXT NOT NULL,
                size TEXT NOT NULL,
                quality TEXT NOT NULL DEFAULT 'low',
                n INTEGER NOT NULL,
                status TEXT NOT NULL,
                upstream_task_id TEXT,
                route_mode TEXT NOT NULL DEFAULT 'auto',
                selected_provider TEXT,
                retry_of_turn_id TEXT REFERENCES turns(id),
                source_image_id TEXT,
                error TEXT,
                elapsed_seconds REAL,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS images (
                id TEXT PRIMARY KEY,
                turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                position INTEGER NOT NULL,
                file_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                storage_backend TEXT NOT NULL DEFAULT 'local',
                storage_status TEXT NOT NULL DEFAULT 'ready',
                object_key TEXT,
                preview_key TEXT,
                thumbnail_key TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pending_storage_deletions (
                id TEXT PRIMARY KEY,
                object_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS provider_attempts (
                id TEXT PRIMARY KEY,
                turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                position INTEGER NOT NULL,
                status TEXT NOT NULL,
                error_kind TEXT,
                error_message TEXT,
                external_task_id TEXT,
                submitted_at TEXT,
                completed_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_turns_conversation
                ON turns(conversation_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_images_turn
                ON images(turn_id, position);
            CREATE INDEX IF NOT EXISTS idx_provider_attempts_turn
                ON provider_attempts(turn_id, position);
            """
        )
        image_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(images)").fetchall()
        }
        migrations = {
            "storage_backend": "TEXT NOT NULL DEFAULT 'local'",
            "storage_status": "TEXT NOT NULL DEFAULT 'ready'",
            "object_key": "TEXT",
            "preview_key": "TEXT",
            "thumbnail_key": "TEXT",
        }
        for column, declaration in migrations.items():
            if column not in image_columns:
                connection.execute(
                    f"ALTER TABLE images ADD COLUMN {column} {declaration}"
                )  # noqa: S608
        turn_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(turns)").fetchall()
        }
        turn_migrations = {
            "quality": "TEXT NOT NULL DEFAULT 'low'",
            "route_mode": "TEXT NOT NULL DEFAULT 'auto'",
            "selected_provider": "TEXT",
            "retry_of_turn_id": "TEXT",
        }
        for column, declaration in turn_migrations.items():
            if column not in turn_columns:
                connection.execute(
                    f"ALTER TABLE turns ADD COLUMN {column} {declaration}"
                )  # noqa: S608


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
