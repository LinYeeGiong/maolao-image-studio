from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

from PIL import Image, ImageOps
from qcloud_cos import CosConfig, CosS3Client

from app.core.database import connect, media_dir, now_iso
from app.core.settings import settings

ImageVariant = Literal["original", "preview", "thumbnail"]
WEBP_QUALITY = 84
PREVIEW_MAX_EDGE = 1440
THUMBNAIL_MAX_EDGE = 480
# Once an object is on COS, its smaller renditions come from Data Processing
# (数据万象) on the fly, so we never generate or store them there.
_COS_VARIANT_PROCESSING = {
    "preview": f"imageMogr2/thumbnail/{PREVIEW_MAX_EDGE}x{PREVIEW_MAX_EDGE}",
    "thumbnail": f"imageMogr2/thumbnail/{THUMBNAIL_MAX_EDGE}x{THUMBNAIL_MAX_EDGE}",
}
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"

# Every signature embeds a fresh timestamp, so re-signing on each poll would
# hand the browser a new URL every 2.5s and defeat its cache entirely. Reuse
# one signature per object until it nears expiry.
_SIGNED_URL_CACHE: OrderedDict[tuple[str, str | None], tuple[float, str]] = OrderedDict()
_SIGNED_URL_CACHE_LIMIT = 2048
_SIGNED_URL_REFRESH_MARGIN_SECONDS = 300


@dataclass(frozen=True)
class StoredImage:
    stored_name: str
    storage_backend: str
    storage_status: str
    object_key: str | None
    preview_key: str | None
    thumbnail_key: str | None


def cos_is_configured() -> bool:
    return bool(
        settings.COS_ENABLED
        and settings.COS_SECRET_ID
        and settings.COS_SECRET_KEY
        and settings.COS_BUCKET
        and settings.COS_REGION
    )


def _build_client(endpoint: str | None) -> CosS3Client:
    config = CosConfig(
        Region=settings.COS_REGION,
        SecretId=settings.COS_SECRET_ID,
        SecretKey=settings.COS_SECRET_KEY,
        Scheme="https",
        Endpoint=endpoint or None,
        Timeout=settings.COS_TIMEOUT_SECONDS,
    )
    return CosS3Client(config)


def _client() -> CosS3Client:
    """Client for server-side data transfer, which may take a private route."""
    return _build_client(settings.COS_API_ENDPOINT)


def _public_client() -> CosS3Client:
    """Client for signing URLs handed to browsers.

    These must always name the public endpoint: a browser cannot resolve a
    VPC-internal host, so signing with COS_API_ENDPOINT would hand out URLs
    that only the server itself can reach.
    """
    return _build_client(None)


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "-", value).strip(".-")
    if not cleaned:
        raise ValueError("Storage path segment is empty")
    return cleaned


def _object_keys(
    conversation_id: str,
    turn_id: str,
    image_id: str,
    extension: str,
) -> tuple[str, str, str]:
    prefix_parts = [
        _safe_segment(part)
        for part in settings.COS_OBJECT_PREFIX.strip("/").split("/")
        if part
    ]
    base = "/".join(
        [
            *prefix_parts,
            _safe_segment(conversation_id),
            _safe_segment(turn_id),
            _safe_segment(image_id),
        ]
    )
    return (
        f"{base}/original{extension}",
        f"{base}/preview.webp",
        f"{base}/thumbnail.webp",
    )


def _local_variant_names(stored_name: str) -> tuple[str, str]:
    stem = Path(stored_name).stem
    return f"{stem}-preview.webp", f"{stem}-thumbnail.webp"


def local_variant_path(row: dict[str, Any], variant: ImageVariant) -> Path:
    if variant == "original":
        name = row["stored_name"]
    elif row.get("storage_status") == "pending_upload":
        preview_name, thumbnail_name = _local_variant_names(row["stored_name"])
        name = preview_name if variant == "preview" else thumbnail_name
    else:
        key = "preview_key" if variant == "preview" else "thumbnail_key"
        name = row.get(key) or row["stored_name"]
    return media_dir() / name


def _webp_variant(image: Image.Image, max_edge: int) -> bytes:
    variant = image.copy()
    variant.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    bands = variant.getbands()
    target_mode = "RGBA" if "A" in bands else "RGB"
    if variant.mode != target_mode:
        variant = variant.convert(target_mode)
    output = BytesIO()
    variant.save(output, format="WEBP", quality=WEBP_QUALITY, method=6)
    return output.getvalue()


def build_variants(content: bytes) -> tuple[bytes, bytes]:
    with Image.open(BytesIO(content)) as source:
        source.load()
        oriented = ImageOps.exif_transpose(source)
        return (
            _webp_variant(oriented, PREVIEW_MAX_EDGE),
            _webp_variant(oriented, THUMBNAIL_MAX_EDGE),
        )


def _put_object(client: Any, *, key: str, body: bytes, content_type: str) -> None:
    common = {
        "Bucket": settings.COS_BUCKET,
        "Key": key,
        "ContentType": content_type,
        "CacheControl": IMMUTABLE_CACHE_CONTROL,
    }
    if len(body) < settings.COS_MULTIPART_THRESHOLD_BYTES:
        client.put_object(Body=body, **common)
        return
    # Serial parts: this host's egress is the bottleneck, so parallelism buys
    # nothing, but per-part retries keep a stalled chunk from losing the
    # whole transfer.
    client.upload_file_from_buffer(
        Body=BytesIO(body), PartSize=1, MAXThread=1, **common
    )


def _upload_original(
    client: Any, *, object_key: str, original: bytes, mime_type: str
) -> None:
    """Push just the original; COS renders the smaller variants on request."""
    _put_object(client, key=object_key, body=original, content_type=mime_type)


def _remove_local_variants(stored_name: str) -> None:
    preview_name, thumbnail_name = _local_variant_names(stored_name)
    for name in (stored_name, preview_name, thumbnail_name):
        (media_dir() / name).unlink(missing_ok=True)


def store_image(
    *,
    conversation_id: str,
    turn_id: str,
    image_id: str,
    extension: str,
    mime_type: str,
    content: bytes,
    client: Any | None = None,
    defer_upload: bool = False,
) -> StoredImage:
    normalized_extension = extension.lower()
    if normalized_extension == ".jpeg":
        normalized_extension = ".jpg"
    if normalized_extension not in {".png", ".jpg", ".webp"}:
        raise ValueError("Unsupported image extension")
    preview, thumbnail = build_variants(content)
    stored_name = f"{_safe_segment(turn_id)}-{_safe_segment(image_id)}{normalized_extension}"
    preview_name, thumbnail_name = _local_variant_names(stored_name)
    (media_dir() / stored_name).write_bytes(content)
    (media_dir() / preview_name).write_bytes(preview)
    (media_dir() / thumbnail_name).write_bytes(thumbnail)

    if not cos_is_configured():
        return StoredImage(
            stored_name=stored_name,
            storage_backend="local",
            storage_status="ready",
            object_key=None,
            preview_key=preview_name,
            thumbnail_key=thumbnail_name,
        )

    object_key, preview_key, thumbnail_key = _object_keys(
        conversation_id, turn_id, image_id, normalized_extension
    )
    if defer_upload:
        # Skip the synchronous COS round-trip on this path so the caller
        # (a user-facing request) doesn't block on network I/O; the
        # storage maintenance loop uploads it shortly after via
        # retry_pending_uploads_once, same as a failed upload would.
        return StoredImage(
            stored_name=stored_name,
            storage_backend="local",
            storage_status="pending_upload",
            object_key=object_key,
            preview_key=preview_key,
            thumbnail_key=thumbnail_key,
        )
    try:
        _upload_original(
            client or _client(),
            object_key=object_key,
            original=content,
            mime_type=mime_type,
        )
    except Exception:
        return StoredImage(
            stored_name=stored_name,
            storage_backend="local",
            storage_status="pending_upload",
            object_key=object_key,
            preview_key=preview_key,
            thumbnail_key=thumbnail_key,
        )

    _remove_local_variants(stored_name)
    return StoredImage(
        stored_name=stored_name,
        storage_backend="cos",
        storage_status="ready",
        object_key=object_key,
        preview_key=preview_key,
        thumbnail_key=thumbnail_key,
    )


def _cos_body_bytes(response: dict[str, Any]) -> bytes:
    body = response["Body"]
    stream = body.get_raw_stream() if hasattr(body, "get_raw_stream") else body
    return stream.read()


def read_original(row: dict[str, Any], client: Any | None = None) -> bytes:
    if row.get("storage_backend") == "cos" and row.get("object_key"):
        try:
            response = (client or _client()).get_object(
                Bucket=settings.COS_BUCKET, Key=row["object_key"]
            )
            return _cos_body_bytes(response)
        except Exception as exc:
            raise RuntimeError("cos_read_error") from exc
    return local_variant_path(row, "original").read_bytes()


def signed_url(
    row: dict[str, Any],
    variant: ImageVariant,
    *,
    download_name: str | None = None,
    client: Any | None = None,
) -> str:
    key = row.get("object_key")
    if not key:
        raise RuntimeError("cos_read_error")
    cache_key = (str(key), variant, download_name)
    now = time.monotonic()
    cached = _SIGNED_URL_CACHE.get(cache_key)
    if cached is not None and cached[0] > now:
        _SIGNED_URL_CACHE.move_to_end(cache_key)
        return cached[1]
    params: dict[str, str] = {}
    processing = _COS_VARIANT_PROCESSING.get(variant)
    if processing:
        params[processing] = ""
    if download_name:
        encoded = quote(download_name, safe="")
        params["response-content-disposition"] = (
            f"attachment; filename*=UTF-8''{encoded}"
        )
    try:
        url = (client or _public_client()).get_presigned_url(
            Method="GET",
            Bucket=settings.COS_BUCKET,
            Key=key,
            Expired=settings.COS_SIGNED_URL_TTL,
            Params=params,
        )
    except Exception as exc:
        raise RuntimeError("cos_read_error") from exc
    expiry = now + max(settings.COS_SIGNED_URL_TTL - _SIGNED_URL_REFRESH_MARGIN_SECONDS, 60)
    _SIGNED_URL_CACHE[cache_key] = (expiry, str(url))
    _SIGNED_URL_CACHE.move_to_end(cache_key)
    while len(_SIGNED_URL_CACHE) > _SIGNED_URL_CACHE_LIMIT:
        _SIGNED_URL_CACHE.popitem(last=False)
    return str(url)


def public_variant_url(
    row: dict[str, Any],
    variant: ImageVariant,
    *,
    download_name: str | None = None,
) -> str | None:
    """A COS URL the browser can fetch directly, or None to serve it locally."""
    if row.get("storage_backend") != "cos" or row.get("storage_status") != "ready":
        return None
    try:
        return signed_url(row, variant, download_name=download_name)
    except RuntimeError:
        return None


def retry_pending_uploads_once(client: Any | None = None) -> int:
    if not cos_is_configured():
        return 0
    cos_client = client or _client()
    with connect() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM images WHERE storage_status = 'pending_upload'"
            ).fetchall()
        ]
    completed = 0
    for row in rows:
        preview_name, thumbnail_name = _local_variant_names(row["stored_name"])
        try:
            _upload_original(
                cos_client,
                object_key=row["object_key"],
                original=(media_dir() / row["stored_name"]).read_bytes(),
                mime_type=row["mime_type"],
            )
        except Exception:
            continue
        with connect() as connection:
            connection.execute(
                "UPDATE images SET storage_backend = 'cos', storage_status = 'ready' "
                "WHERE id = ? AND storage_status = 'pending_upload'",
                (row["id"],),
            )
        _remove_local_variants(row["stored_name"])
        completed += 1
    return completed


def queue_storage_deletions(object_keys: list[str]) -> None:
    with connect() as connection:
        for key in dict.fromkeys(object_keys):
            if key:
                connection.execute(
                    """INSERT OR IGNORE INTO pending_storage_deletions
                       (id, object_key, created_at) VALUES (?, ?, ?)""",
                    (str(uuid4()), key, now_iso()),
                )


def discard_stored_image(stored: StoredImage) -> None:
    if stored.object_key:
        queue_storage_deletions(
            [
                key
                for key in (
                    stored.object_key,
                    stored.preview_key,
                    stored.thumbnail_key,
                )
                if key
            ]
        )
    _remove_local_variants(stored.stored_name)


def retry_pending_deletions_once(client: Any | None = None) -> int:
    if not cos_is_configured():
        return 0
    cos_client = client or _client()
    with connect() as connection:
        rows = connection.execute(
            "SELECT id, object_key FROM pending_storage_deletions ORDER BY created_at"
        ).fetchall()
    completed = 0
    for row in rows:
        try:
            cos_client.delete_object(
                Bucket=settings.COS_BUCKET, Key=row["object_key"]
            )
        except Exception as exc:
            with connect() as connection:
                connection.execute(
                    """UPDATE pending_storage_deletions
                       SET attempts = attempts + 1, last_error = ? WHERE id = ?""",
                    (type(exc).__name__, row["id"]),
                )
            continue
        with connect() as connection:
            connection.execute(
                "DELETE FROM pending_storage_deletions WHERE id = ?", (row["id"],)
            )
        completed += 1
    return completed
