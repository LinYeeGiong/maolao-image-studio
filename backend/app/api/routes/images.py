from __future__ import annotations

import asyncio
import json
import mimetypes
from dataclasses import dataclass
from io import BytesIO
from time import monotonic
from typing import Any, Literal
from urllib.parse import quote, urljoin, urlparse
from uuid import uuid4

import httpx
from fastapi import HTTPException

from app.core.database import connect, now_iso, row_dict
from app.core.image_storage import discard_stored_image, read_original, store_image
from app.core.settings import settings

ImageSize = Literal["2880x2880", "3840x2160", "2160x3840"]
ALLOWED_SIZES = {"2880x2880", "3840x2160", "2160x3840"}
MAX_GENERATED_IMAGE_BYTES = 50 * 1024 * 1024
MAX_ERROR_RESPONSE_BYTES = 64 * 1024
TRUSTED_IMAGE_HOST_SUFFIXES = (
    ".amazonaws.com",
    ".blob.core.windows.net",
    ".openaiusercontent.com",
)
ALLOWED_GENERATED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


@dataclass(frozen=True)
class MaolaoRequest:
    action: str
    json: dict[str, Any] | None
    data: dict[str, str] | None
    files: list[tuple[str, tuple[str, BytesIO, str]]] | None


@dataclass(frozen=True)
class ImageDownloadRequest:
    url: str
    headers: dict[str, str] | None


def build_maolao_request(
    *,
    prompt: str,
    size: ImageSize,
    n: int,
    reference_images: list[tuple[str, BytesIO, str]],
) -> MaolaoRequest:
    common = {
        "model": "gpt-image-2-4k",
        "prompt": prompt,
        "n": n,
        "quality": "high",
        "response_format": "b64_json",
        "size": size,
    }
    if not reference_images:
        return MaolaoRequest(action="generations", json=common, data=None, files=None)
    files = [("image", reference_image) for reference_image in reference_images]
    return MaolaoRequest(
        action="edits",
        json=None,
        data={key: str(value) for key, value in common.items()},
        files=files,
    )


def build_image_download_request(
    *,
    result_item: dict[str, Any],
    upstream_task_id: str,
    index: int,
    base_url: str,
    api_headers: dict[str, str],
) -> ImageDownloadRequest:
    result_url = result_item.get("url")
    if isinstance(result_url, str) and result_url.strip():
        result_url = result_url.strip()
        parsed = urlparse(result_url)
        if parsed.scheme or parsed.netloc:
            if parsed.scheme.lower() != "https" or not parsed.netloc:
                raise ValueError("Absolute image result URLs must use HTTPS")
            base = urlparse(base_url)
            if parsed.netloc.lower() == base.netloc.lower():
                return ImageDownloadRequest(url=result_url, headers=api_headers)
            hostname = (parsed.hostname or "").lower()
            if not any(
                hostname == suffix.removeprefix(".") or hostname.endswith(suffix)
                for suffix in TRUSTED_IMAGE_HOST_SUFFIXES
            ):
                raise ValueError("Image result URL does not use a trusted host")
            return ImageDownloadRequest(url=result_url, headers=None)
        return ImageDownloadRequest(
            url=urljoin(f"{base_url.rstrip('/')}/", result_url.lstrip("/")),
            headers=api_headers,
        )
    return ImageDownloadRequest(
        url=(
            f"{base_url.rstrip('/')}/v1/images/tasks/"
            f"{quote(str(upstream_task_id), safe='')}/content/{index}"
        ),
        headers=api_headers,
    )


def _headers() -> dict[str, str]:
    if not settings.MAOLAO_API_KEY:
        raise RuntimeError("服务端尚未配置 MAOLAO_API_KEY")
    return {"Authorization": f"Bearer {settings.MAOLAO_API_KEY}"}


def _upstream_error(response: httpx.Response) -> str:
    try:
        detail = response.json()
    except ValueError:
        return response.text or f"MaolaoAPI 请求失败 ({response.status_code})"
    if isinstance(detail, dict):
        error = detail.get("error") or detail.get("detail")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error:
            return str(error)
    return json.dumps(detail, ensure_ascii=False)


async def download_generated_image(
    client: httpx.AsyncClient,
    request: ImageDownloadRequest,
    *,
    attempts: int = 3,
    retry_delay_seconds: float = 5,
    max_bytes: int = MAX_GENERATED_IMAGE_BYTES,
    max_error_bytes: int = MAX_ERROR_RESPONSE_BYTES,
) -> httpx.Response:
    if attempts < 1:
        raise ValueError("Download attempts must be at least 1")
    for attempt in range(attempts):
        async with client.stream(
            "GET",
            request.url,
            headers=request.headers,
            follow_redirects=False,
        ) as response:
            if response.is_success:
                media_type = (
                    response.headers.get("content-type", "").split(";")[0].lower()
                )
                if media_type not in ALLOWED_GENERATED_IMAGE_TYPES:
                    raise RuntimeError(
                        "Downloaded result has an unsupported image content type"
                    )
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > max_bytes:
                            raise RuntimeError(
                                "Downloaded image exceeds the size limit"
                            )
                    except ValueError:
                        pass
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise RuntimeError("Downloaded image exceeds the size limit")
                image_content = bytes(content)
                valid_signature = (
                    media_type == "image/png"
                    and image_content.startswith(b"\x89PNG\r\n\x1a\n")
                    or media_type == "image/jpeg"
                    and image_content.startswith(b"\xff\xd8\xff")
                    or media_type == "image/webp"
                    and len(image_content) >= 12
                    and image_content.startswith(b"RIFF")
                    and image_content[8:12] == b"WEBP"
                )
                if not valid_signature:
                    raise RuntimeError(
                        "Downloaded result is not a valid supported image"
                    )
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=image_content,
                    request=response.request,
                )
            error_content = bytearray()
            async for chunk in response.aiter_bytes():
                remaining = max_error_bytes - len(error_content)
                if remaining <= 0:
                    break
                error_content.extend(chunk[:remaining])
            error_response = httpx.Response(
                response.status_code,
                headers=response.headers,
                content=bytes(error_content),
                request=response.request,
            )
            retryable = (
                response.status_code
                in {
                    404,
                    409,
                    425,
                    429,
                }
                or 500 <= response.status_code <= 599
            )
            if not retryable or attempt + 1 == attempts:
                raise RuntimeError(_upstream_error(error_response))
        await asyncio.sleep(retry_delay_seconds)
    raise RuntimeError("Image download attempts exhausted")


def exception_message(exc: Exception) -> str:
    return str(exc).strip() or type(exc).__name__


def _references_for_turn(turn: dict[str, Any]) -> list[tuple[str, BytesIO, str]]:
    with connect() as connection:
        rows = []
        if turn.get("source_image_id"):
            rows += connection.execute(
                "SELECT * FROM images WHERE id = ?",
                (turn["source_image_id"],),
            ).fetchall()
        rows += connection.execute(
            """SELECT * FROM images
               WHERE turn_id = ? AND kind = 'reference'
               ORDER BY position ASC""",
            (turn["id"],),
        ).fetchall()
    return [
        (
            row["file_name"],
            BytesIO(read_original(dict(row))),
            row["mime_type"],
        )
        for row in rows
    ]


def _update_turn(turn_id: str, **values: Any) -> None:
    if not values:
        return
    assignments = ", ".join(f"{key} = ?" for key in values)
    with connect() as connection:
        connection.execute(
            f"UPDATE turns SET {assignments} WHERE id = ?", (*values.values(), turn_id)
        )  # noqa: S608


def _load_turn(turn_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        return row_dict(
            connection.execute(
                "SELECT * FROM turns WHERE id = ?", (turn_id,)
            ).fetchone()
        )


def _save_generated_image(
    *, turn_id: str, position: int, content: bytes, content_type: str
) -> None:
    media_type = content_type.split(";")[0]
    extension = mimetypes.guess_extension(media_type) or ".png"
    if extension == ".jpe":
        extension = ".jpg"
    image_id = str(uuid4())
    with connect() as connection:
        turn = connection.execute(
            "SELECT conversation_id FROM turns WHERE id = ?", (turn_id,)
        ).fetchone()
    if turn is None:
        raise RuntimeError("生成任务不存在")
    stored = store_image(
        conversation_id=turn["conversation_id"],
        turn_id=turn_id,
        image_id=image_id,
        extension=extension,
        mime_type=media_type,
        content=content,
    )
    try:
        with connect() as connection:
            connection.execute(
                """INSERT INTO images
                   (id, turn_id, kind, position, file_name, stored_name, mime_type,
                    storage_backend, storage_status, object_key, preview_key,
                    thumbnail_key, created_at)
                   VALUES (?, ?, 'generated', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    image_id,
                    turn_id,
                    position,
                    f"maolao-{position + 1}{extension}",
                    stored.stored_name,
                    media_type,
                    stored.storage_backend,
                    stored.storage_status,
                    stored.object_key,
                    stored.preview_key,
                    stored.thumbnail_key,
                    now_iso(),
                ),
            )
    except Exception:
        discard_stored_image(stored)
        raise


async def process_turn(turn_id: str) -> None:
    started = monotonic()
    turn = _load_turn(turn_id)
    if turn is None or turn["status"] in {"succeeded", "failed"}:
        return
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            upstream_task_id = turn.get("upstream_task_id")
            if not upstream_task_id:
                request = build_maolao_request(
                    prompt=turn["effective_prompt"],
                    size=turn["size"],
                    n=turn["n"],
                    reference_images=_references_for_turn(turn),
                )
                response = await client.post(
                    f"{settings.MAOLAO_BASE_URL}/v1/images/tasks",
                    params={"action": request.action},
                    headers=_headers(),
                    json=request.json,
                    data=request.data,
                    files=request.files,
                )
                if not response.is_success:
                    raise RuntimeError(_upstream_error(response))
                payload = response.json()
                upstream_task_id = payload.get("task_id") or payload.get("id")
                if not upstream_task_id:
                    raise RuntimeError("MaolaoAPI 未返回 task_id")
                _update_turn(
                    turn_id,
                    upstream_task_id=str(upstream_task_id),
                    status=payload.get("status") or "queued",
                )
            while True:
                response = await client.get(
                    f"{settings.MAOLAO_BASE_URL}/v1/images/tasks/{quote(str(upstream_task_id), safe='')}",
                    headers=_headers(),
                    timeout=30,
                )
                if not response.is_success:
                    raise RuntimeError(_upstream_error(response))
                task_payload = response.json()
                status = task_payload.get("status", "processing")
                _update_turn(turn_id, status=status)
                if status in {"succeeded", "failed"}:
                    break
                await asyncio.sleep(settings.TASK_POLL_INTERVAL_SECONDS)
            if task_payload.get("status") == "failed":
                raise RuntimeError(str(task_payload.get("error") or "图片生成失败"))
            delivered = (task_payload.get("result") or {}).get("data") or []
            result_items = delivered or [{} for _ in range(int(turn["n"]))]
            for index, result_item in enumerate(result_items):
                download_request = build_image_download_request(
                    result_item=result_item if isinstance(result_item, dict) else {},
                    upstream_task_id=str(upstream_task_id),
                    index=index,
                    base_url=settings.MAOLAO_BASE_URL,
                    api_headers=_headers(),
                )
                response = await download_generated_image(client, download_request)
                await asyncio.to_thread(
                    _save_generated_image,
                    turn_id=turn_id,
                    position=index,
                    content=response.content,
                    content_type=response.headers.get("content-type", "image/png"),
                )
        _update_turn(
            turn_id,
            status="succeeded",
            elapsed_seconds=round(monotonic() - started, 3),
            completed_at=now_iso(),
        )
    except Exception as exc:
        _update_turn(
            turn_id,
            status="failed",
            error=exception_message(exc),
            elapsed_seconds=round(monotonic() - started, 3),
            completed_at=now_iso(),
        )
    finally:
        current = _load_turn(turn_id)
        if current:
            with connect() as connection:
                connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (now_iso(), current["conversation_id"]),
                )


def start_turn(turn_id: str) -> None:
    asyncio.create_task(process_turn(turn_id))


def resume_pending_turns() -> None:
    with connect() as connection:
        rows = connection.execute(
            "SELECT id FROM turns WHERE status IN ('queued', 'processing')"
        ).fetchall()
    for row in rows:
        start_turn(row["id"])


def validate_size(size: str) -> ImageSize:
    if size not in ALLOWED_SIZES:
        raise HTTPException(status_code=422, detail="不支持的图片尺寸")
    return size  # type: ignore[return-value]
