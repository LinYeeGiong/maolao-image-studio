import asyncio
from io import BytesIO

from fastapi import UploadFile
from starlette.datastructures import Headers

from app import main
from app.api.routes import conversations, images
from app.core.database import connect, init_database, now_iso
from app.core.image_storage import StoredImage
from app.core.settings import settings


def seed_conversation(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    init_database()
    with connect() as connection:
        connection.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?)",
            ("conversation-1", "新对话", now_iso(), now_iso()),
        )


def cos_stored() -> StoredImage:
    return StoredImage(
        stored_name="turn-1-image-1.png",
        storage_backend="cos",
        storage_status="ready",
        object_key="maolao/conversation-1/turn-1/image-1/original.png",
        preview_key="maolao/conversation-1/turn-1/image-1/preview.webp",
        thumbnail_key="maolao/conversation-1/turn-1/image-1/thumbnail.webp",
    )


def test_reference_upload_persists_cos_storage_metadata(tmp_path, monkeypatch) -> None:
    seed_conversation(tmp_path, monkeypatch)
    calls: list[dict[str, object]] = []

    def fake_store_image(**kwargs):
        calls.append(kwargs)
        return cos_stored()

    monkeypatch.setattr(conversations, "store_image", fake_store_image, raising=False)
    monkeypatch.setattr(conversations, "start_turn", lambda _: None)
    upload = UploadFile(
        BytesIO(b"\x89PNG\r\n\x1a\ncontent"),
        filename="reference.png",
        headers=Headers({"content-type": "image/png"}),
    )

    asyncio.run(
        conversations.create_turn(
            conversation_id="conversation-1",
            prompt="use this",
            size="2880x2880",
            n=1,
            source_image_id=None,
            image=None,
            images=[upload],
        )
    )

    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM images WHERE kind = 'reference'"
        ).fetchone()
    assert len(calls) == 1
    assert calls[0]["conversation_id"] == "conversation-1"
    assert row["storage_backend"] == "cos"
    assert row["object_key"].endswith("/original.png")


def test_text_only_turn_does_not_inherit_latest_generated_image(tmp_path, monkeypatch) -> None:
    seed_conversation(tmp_path, monkeypatch)
    timestamp = now_iso()
    with connect() as connection:
        connection.execute(
            """INSERT INTO turns
               (id, conversation_id, prompt, effective_prompt, size, n, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("turn-previous", "conversation-1", "old", "old", "2880x2880", 1,
             "succeeded", timestamp),
        )
        connection.execute(
            """INSERT INTO images
               (id, turn_id, kind, position, file_name, stored_name, mime_type, created_at)
               VALUES (?, ?, 'generated', 0, ?, ?, 'image/png', ?)""",
            ("image-previous", "turn-previous", "previous.png", "previous.png", timestamp),
        )
    monkeypatch.setattr(conversations, "start_turn", lambda _: None)

    created = asyncio.run(
        conversations.create_turn(
            conversation_id="conversation-1",
            prompt="new text-only request",
            size="2160x3840",
            n=1,
            source_image_id=None,
            image=None,
            images=[],
        )
    )

    assert created["source_image_id"] is None
    assert created["quality"] == "low"
    with connect() as connection:
        row = connection.execute(
            "SELECT source_image_id, effective_prompt FROM turns WHERE id = ?",
            (created["id"],),
        ).fetchone()
    assert row["source_image_id"] is None
    assert row["effective_prompt"] == "new text-only request"


def test_generated_result_persists_cos_storage_metadata(tmp_path, monkeypatch) -> None:
    seed_conversation(tmp_path, monkeypatch)
    with connect() as connection:
        connection.execute(
            """INSERT INTO turns
               (id, conversation_id, prompt, effective_prompt, size, n, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("turn-1", "conversation-1", "p", "p", "2880x2880", 1, "processing", now_iso()),
        )
    monkeypatch.setattr(images, "store_image", lambda **_: cos_stored(), raising=False)

    images._save_generated_image(
        turn_id="turn-1",
        position=0,
        content=b"\x89PNG\r\n\x1a\ncontent",
        content_type="image/png",
    )

    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM images WHERE kind = 'generated'"
        ).fetchone()
    assert row["storage_backend"] == "cos"
    assert row["preview_key"].endswith("/preview.webp")


def test_cos_reference_is_loaded_through_storage_service(tmp_path, monkeypatch) -> None:
    seed_conversation(tmp_path, monkeypatch)
    with connect() as connection:
        connection.execute(
            """INSERT INTO turns
               (id, conversation_id, prompt, effective_prompt, size, n, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("turn-1", "conversation-1", "p", "p", "2880x2880", 1, "queued", now_iso()),
        )
        connection.execute(
            """INSERT INTO images
               (id, turn_id, kind, position, file_name, stored_name, mime_type,
                storage_backend, storage_status, object_key, preview_key,
                thumbnail_key, created_at)
               VALUES (?, ?, 'reference', 0, ?, ?, 'image/png', 'cos', 'ready', ?, ?, ?, ?)""",
            (
                "image-1", "turn-1", "reference.png", "unused.png",
                "prefix/original.png", "prefix/preview.webp",
                "prefix/thumbnail.webp", now_iso(),
            ),
        )
    monkeypatch.setattr(images, "read_original", lambda row: b"cos-original", raising=False)

    references = images._references_for_turn(
        {"id": "turn-1", "source_image_id": None}
    )

    assert references[0][0] == "reference.png"
    assert references[0][1].read() == b"cos-original"


def test_storage_maintenance_processes_uploads_and_deletions(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        main, "retry_pending_uploads_once", lambda: calls.append("upload")
    )
    monkeypatch.setattr(
        main, "retry_pending_deletions_once", lambda: calls.append("delete")
    )

    asyncio.run(main.run_storage_maintenance_once())

    assert sorted(calls) == ["delete", "upload"]
