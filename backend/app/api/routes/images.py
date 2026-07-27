from __future__ import annotations

import asyncio
import json
import mimetypes
from dataclasses import dataclass
from io import BytesIO
from time import monotonic
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import HTTPException

from app.core.database import connect, media_dir, now_iso, row_dict
from app.core.settings import settings

ImageSize = Literal["2880x2880", "3840x2160", "2160x3840"]
ALLOWED_SIZES = {"2880x2880", "3840x2160", "2160x3840"}


@dataclass(frozen=True)
class MaolaoRequest:
    action: str
    json: dict[str, Any] | None
    data: dict[str, str] | None
    files: list[tuple[str, tuple[str, BytesIO, str]]] | None


def build_maolao_request(
    *, prompt: str, size: ImageSize, n: int,
    reference_images: list[tuple[str, BytesIO, str]],
) -> MaolaoRequest:
    common = {
        "model": "gpt-image-2-4k", "prompt": prompt, "n": n,
        "quality": "high", "response_format": "b64_json", "size": size,
    }
    if not reference_images:
        return MaolaoRequest(action="generations", json=common, data=None, files=None)
    files = [("image", reference_image) for reference_image in reference_images]
    return MaolaoRequest(action="edits", json=None, data={key: str(value) for key, value in common.items()}, files=files)


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


def _references_for_turn(turn: dict[str, Any]) -> list[tuple[str, BytesIO, str]]:
    with connect() as connection:
        rows = []
        if turn.get("source_image_id"):
            rows += connection.execute(
                "SELECT file_name, stored_name, mime_type FROM images WHERE id = ?",
                (turn["source_image_id"],),
            ).fetchall()
        rows += connection.execute(
            """SELECT file_name, stored_name, mime_type FROM images
               WHERE turn_id = ? AND kind = 'reference'
               ORDER BY position ASC""",
            (turn["id"],),
        ).fetchall()
    return [
        (row["file_name"], BytesIO((media_dir() / row["stored_name"]).read_bytes()), row["mime_type"])
        for row in rows
    ]


def _update_turn(turn_id: str, **values: Any) -> None:
    if not values:
        return
    assignments = ", ".join(f"{key} = ?" for key in values)
    with connect() as connection:
        connection.execute(f"UPDATE turns SET {assignments} WHERE id = ?", (*values.values(), turn_id))  # noqa: S608


def _load_turn(turn_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        return row_dict(connection.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone())


def _save_generated_image(*, turn_id: str, position: int, content: bytes, content_type: str) -> None:
    media_type = content_type.split(";")[0]
    extension = mimetypes.guess_extension(media_type) or ".png"
    if extension == ".jpe":
        extension = ".jpg"
    image_id = str(uuid4())
    stored_name = f"{turn_id}-{position}-{image_id}{extension}"
    (media_dir() / stored_name).write_bytes(content)
    with connect() as connection:
        connection.execute(
            "INSERT INTO images (id, turn_id, kind, position, file_name, stored_name, mime_type, created_at) VALUES (?, ?, 'generated', ?, ?, ?, ?, ?)",
            (image_id, turn_id, position, f"maolao-{position + 1}{extension}", stored_name, media_type, now_iso()),
        )


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
                    f"{settings.MAOLAO_BASE_URL}/v1/images/tasks", params={"action": request.action},
                    headers=_headers(), json=request.json, data=request.data, files=request.files,
                )
                if not response.is_success:
                    raise RuntimeError(_upstream_error(response))
                payload = response.json()
                upstream_task_id = payload.get("task_id") or payload.get("id")
                if not upstream_task_id:
                    raise RuntimeError("MaolaoAPI 未返回 task_id")
                _update_turn(turn_id, upstream_task_id=str(upstream_task_id), status=payload.get("status") or "queued")
            while True:
                response = await client.get(
                    f"{settings.MAOLAO_BASE_URL}/v1/images/tasks/{quote(str(upstream_task_id), safe='')}", headers=_headers(), timeout=30,
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
            for index in range(len(delivered) or int(turn["n"])):
                response = await client.get(
                    f"{settings.MAOLAO_BASE_URL}/v1/images/tasks/{quote(str(upstream_task_id), safe='')}/content/{index}", headers=_headers(),
                )
                if not response.is_success:
                    raise RuntimeError(_upstream_error(response))
                _save_generated_image(turn_id=turn_id, position=index, content=response.content, content_type=response.headers.get("content-type", "image/png"))
        _update_turn(turn_id, status="succeeded", elapsed_seconds=round(monotonic() - started, 3), completed_at=now_iso())
    except Exception as exc:
        _update_turn(turn_id, status="failed", error=str(exc), elapsed_seconds=round(monotonic() - started, 3), completed_at=now_iso())
    finally:
        current = _load_turn(turn_id)
        if current:
            with connect() as connection:
                connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now_iso(), current["conversation_id"]))


def start_turn(turn_id: str) -> None:
    asyncio.create_task(process_turn(turn_id))


def resume_pending_turns() -> None:
    with connect() as connection:
        rows = connection.execute("SELECT id FROM turns WHERE status IN ('queued', 'processing')").fetchall()
    for row in rows:
        start_turn(row["id"])


def validate_size(size: str) -> ImageSize:
    if size not in ALLOWED_SIZES:
        raise HTTPException(status_code=422, detail="不支持的图片尺寸")
    return size  # type: ignore[return-value]
