import sqlite3

from app.core.database import database_path, init_database
from app.core.settings import settings


def test_upgrades_legacy_images_table_without_losing_rows(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    connection = sqlite3.connect(database_path())
    connection.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY, title TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE turns (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            prompt TEXT NOT NULL, effective_prompt TEXT NOT NULL,
            size TEXT NOT NULL, n INTEGER NOT NULL, status TEXT NOT NULL,
            upstream_task_id TEXT, source_image_id TEXT, error TEXT,
            elapsed_seconds REAL, created_at TEXT NOT NULL, completed_at TEXT
        );
        CREATE TABLE images (
            id TEXT PRIMARY KEY,
            turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, position INTEGER NOT NULL,
            file_name TEXT NOT NULL, stored_name TEXT NOT NULL,
            mime_type TEXT NOT NULL, created_at TEXT NOT NULL
        );
        INSERT INTO conversations VALUES ('conversation-1', 'Legacy', 'now', 'now');
        INSERT INTO turns (
            id, conversation_id, prompt, effective_prompt, size, n, status, created_at
        ) VALUES ('turn-1', 'conversation-1', 'p', 'p', '2880x2880', 1, 'succeeded', 'now');
        INSERT INTO images VALUES (
            'image-1', 'turn-1', 'generated', 0,
            'legacy.png', 'legacy.png', 'image/png', 'now'
        );
        """
    )
    connection.commit()
    connection.close()

    init_database()

    connection = sqlite3.connect(database_path())
    connection.row_factory = sqlite3.Row
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(images)")
    }
    row = connection.execute("SELECT * FROM images WHERE id = 'image-1'").fetchone()
    deletion_table = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name = 'pending_storage_deletions'"
    ).fetchone()
    turn_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(turns)")
    }
    attempts_table = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'provider_attempts'"
    ).fetchone()
    connection.close()

    assert {
        "storage_backend",
        "storage_status",
        "object_key",
        "preview_key",
        "thumbnail_key",
    } <= columns
    assert row is not None
    assert row["stored_name"] == "legacy.png"
    assert row["storage_backend"] == "local"
    assert row["storage_status"] == "ready"
    assert deletion_table is not None
    assert {"route_mode", "selected_provider", "retry_of_turn_id"} <= turn_columns
    assert attempts_table is not None
