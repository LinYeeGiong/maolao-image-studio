from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from app.api.routes import conversations, media
from app.core.database import connect, init_database, media_dir, now_iso
from app.core.settings import settings


def insert_image(
    *,
    conversation_id: str,
    turn_id: str,
    image_id: str,
    storage_backend: str = "local",
    object_key: str | None = None,
    preview_key: str | None = None,
    thumbnail_key: str | None = None,
) -> None:
    with connect() as connection:
        connection.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?)",
            (conversation_id, "Test", now_iso(), now_iso()),
        )
        connection.execute(
            """INSERT INTO turns
               (id, conversation_id, prompt, effective_prompt, size, n, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (turn_id, conversation_id, "p", "p", "2880x2880", 1, "succeeded", now_iso()),
        )
        connection.execute(
            """INSERT INTO images
               (id, turn_id, kind, position, file_name, stored_name, mime_type,
                storage_backend, storage_status, object_key, preview_key,
                thumbnail_key, created_at)
               VALUES (?, ?, 'generated', 0, ?, ?, 'image/png', ?, 'ready', ?, ?, ?, ?)""",
            (
                image_id,
                turn_id,
                f"{image_id}.png",
                f"{image_id}.png",
                storage_backend,
                object_key,
                preview_key,
                thumbnail_key,
                now_iso(),
            ),
        )


def test_legacy_preview_route_falls_back_to_local_original(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    init_database()
    insert_image(
        conversation_id="conversation-1", turn_id="turn-1", image_id="image-1"
    )
    original = media_dir() / "image-1.png"
    original.write_bytes(b"legacy")

    response = media.preview_image("image-1")

    assert isinstance(response, FileResponse)
    assert Path(response.path) == original


def test_cos_preview_route_redirects_to_private_signed_url(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    init_database()
    insert_image(
        conversation_id="conversation-1",
        turn_id="turn-1",
        image_id="image-1",
        storage_backend="cos",
        object_key="maolao/conversation-1/turn-1/image-1/original.png",
        preview_key="maolao/conversation-1/turn-1/image-1/preview.webp",
        thumbnail_key="maolao/conversation-1/turn-1/image-1/thumbnail.webp",
    )
    monkeypatch.setattr(
        media,
        "signed_url",
        lambda row, variant, **kwargs: f"https://private.example/{variant}?signature=x",
    )

    response = media.preview_image("image-1")

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 302
    assert response.headers["location"].endswith("/preview?signature=x")


def test_missing_image_route_returns_404(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    init_database()

    with pytest.raises(HTTPException) as exc_info:
        media.original_image("missing")

    assert exc_info.value.status_code == 404


def test_image_payload_exposes_stable_variant_routes() -> None:
    payload = conversations._image_payload(
        {
            "id": "image-1",
            "kind": "generated",
            "position": 0,
            "file_name": "image.png",
            "mime_type": "image/png",
            "stored_name": "stored.png",
        }
    )

    assert payload["url"] == "/api/v1/images/image-1/preview"
    assert payload["thumbnail_url"] == "/api/v1/images/image-1/thumbnail"
    assert payload["preview_url"] == "/api/v1/images/image-1/preview"
    assert payload["download_url"] == "/api/v1/images/image-1/download"


def test_deleting_conversation_queues_only_its_cos_objects(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "COS_ENABLED", False)
    init_database()
    for number in (1, 2):
        prefix = f"maolao/conversation-{number}/turn-{number}/image-{number}"
        insert_image(
            conversation_id=f"conversation-{number}",
            turn_id=f"turn-{number}",
            image_id=f"image-{number}",
            storage_backend="cos",
            object_key=f"{prefix}/original.png",
            preview_key=f"{prefix}/preview.webp",
            thumbnail_key=f"{prefix}/thumbnail.webp",
        )

    conversations.delete_conversation("conversation-1")

    with connect() as connection:
        queued = {
            row["object_key"]
            for row in connection.execute(
                "SELECT object_key FROM pending_storage_deletions"
            ).fetchall()
        }
        remaining = connection.execute(
            "SELECT COUNT(*) FROM conversations WHERE id = 'conversation-2'"
        ).fetchone()[0]
    assert queued == {
        "maolao/conversation-1/turn-1/image-1/original.png",
        "maolao/conversation-1/turn-1/image-1/preview.webp",
        "maolao/conversation-1/turn-1/image-1/thumbnail.webp",
    }
    assert remaining == 1
