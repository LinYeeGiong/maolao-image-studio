from io import BytesIO

from PIL import Image

from app.core import image_storage
from app.core.database import connect, init_database, media_dir, now_iso
from app.core.image_storage import (
    build_variants,
    retry_pending_uploads_once,
    store_image,
)
from app.core.settings import settings


class FakeCosClient:
    def __init__(self, *, fail_upload: bool = False) -> None:
        self.fail_upload = fail_upload
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Key: str, Body: bytes, **_: object) -> None:
        if self.fail_upload:
            raise RuntimeError("temporary COS outage with secret-looking data")
        self.objects[Key] = Body

    def delete_object(self, *, Key: str, **_: object) -> None:
        self.objects.pop(Key, None)


def png_bytes(width: int = 1800, height: int = 900) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "#7848aa").save(output, format="PNG")
    return output.getvalue()


def configure_cos(monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr(settings, "COS_ENABLED", enabled)
    monkeypatch.setattr(settings, "COS_SECRET_ID", "test-secret-id")
    monkeypatch.setattr(settings, "COS_SECRET_KEY", "test-secret-key")
    monkeypatch.setattr(settings, "COS_BUCKET", "bucket-123")
    monkeypatch.setattr(settings, "COS_REGION", "ap-guangzhou")
    monkeypatch.setattr(settings, "COS_OBJECT_PREFIX", "maolao")


def test_client_uses_configured_api_endpoint(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_config(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    configure_cos(monkeypatch, True)
    monkeypatch.setattr(image_storage, "CosConfig", fake_config)
    monkeypatch.setattr(image_storage, "CosS3Client", lambda config: config)

    monkeypatch.setattr(settings, "COS_API_ENDPOINT", "cos-internal.ap-guangzhou.myqcloud.com")
    image_storage._client()
    assert captured["Endpoint"] == "cos-internal.ap-guangzhou.myqcloud.com"

    # Browsers cannot resolve a VPC-internal host, so signing must ignore it.
    image_storage._public_client()
    assert captured["Endpoint"] is None

    monkeypatch.setattr(settings, "COS_API_ENDPOINT", "")
    image_storage._client()
    assert captured["Endpoint"] is None


def test_defer_upload_skips_cos_and_marks_pending(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    configure_cos(monkeypatch, True)
    client = FakeCosClient()

    stored = store_image(
        conversation_id="conversation-1",
        turn_id="turn-1",
        image_id="image-1",
        extension=".png",
        mime_type="image/png",
        content=png_bytes(800, 400),
        client=client,
        defer_upload=True,
    )

    assert stored.storage_status == "pending_upload"
    assert stored.storage_backend == "local"
    assert client.objects == {}
    assert stored.object_key is not None
    assert (media_dir() / stored.stored_name).is_file()


def test_builds_bounded_webp_preview_and_thumbnail() -> None:
    preview, thumbnail = build_variants(png_bytes())

    with Image.open(BytesIO(preview)) as preview_image:
        assert preview_image.format == "WEBP"
        assert max(preview_image.size) == 1440
    with Image.open(BytesIO(thumbnail)) as thumbnail_image:
        assert thumbnail_image.format == "WEBP"
        assert max(thumbnail_image.size) == 480


def test_disabled_cos_stores_ready_local_variants(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    configure_cos(monkeypatch, False)
    content = png_bytes(800, 400)

    stored = store_image(
        conversation_id="conversation-1",
        turn_id="turn-1",
        image_id="image-1",
        extension=".png",
        mime_type="image/png",
        content=content,
    )

    assert stored.storage_backend == "local"
    assert stored.storage_status == "ready"
    assert (media_dir() / stored.stored_name).read_bytes() == content
    assert (media_dir() / stored.preview_key).exists()
    assert (media_dir() / stored.thumbnail_key).exists()


def test_cos_upload_uses_scoped_keys_and_removes_local_copy(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    configure_cos(monkeypatch, True)
    client = FakeCosClient()

    stored = store_image(
        conversation_id="conversation-1",
        turn_id="turn-1",
        image_id="image-1",
        extension=".png",
        mime_type="image/png",
        content=png_bytes(800, 400),
        client=client,
    )

    assert stored.storage_backend == "cos"
    assert stored.storage_status == "ready"
    assert set(client.objects) == {
        "maolao/conversation-1/turn-1/image-1/original.png",
        "maolao/conversation-1/turn-1/image-1/preview.webp",
        "maolao/conversation-1/turn-1/image-1/thumbnail.webp",
    }
    assert not (media_dir() / stored.stored_name).exists()


def test_failed_upload_falls_back_locally_and_retry_switches_database_row(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    configure_cos(monkeypatch, True)
    init_database()
    failed = store_image(
        conversation_id="conversation-1",
        turn_id="turn-1",
        image_id="image-1",
        extension=".png",
        mime_type="image/png",
        content=png_bytes(800, 400),
        client=FakeCosClient(fail_upload=True),
    )
    with connect() as connection:
        connection.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?)",
            ("conversation-1", "Test", now_iso(), now_iso()),
        )
        connection.execute(
            """INSERT INTO turns
               (id, conversation_id, prompt, effective_prompt, size, n, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("turn-1", "conversation-1", "p", "p", "2880x2880", 1, "succeeded", now_iso()),
        )
        connection.execute(
            """INSERT INTO images
               (id, turn_id, kind, position, file_name, stored_name, mime_type,
                storage_backend, storage_status, object_key, preview_key,
                thumbnail_key, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "image-1", "turn-1", "generated", 0, "image.png",
                failed.stored_name, "image/png", failed.storage_backend,
                failed.storage_status, failed.object_key, failed.preview_key,
                failed.thumbnail_key, now_iso(),
            ),
        )

    assert failed.storage_backend == "local"
    assert failed.storage_status == "pending_upload"
    working_client = FakeCosClient()

    assert retry_pending_uploads_once(client=working_client) == 1

    with connect() as connection:
        row = connection.execute(
            "SELECT storage_backend, storage_status FROM images WHERE id = 'image-1'"
        ).fetchone()
    assert tuple(row) == ("cos", "ready")
    assert not (media_dir() / failed.stored_name).exists()
