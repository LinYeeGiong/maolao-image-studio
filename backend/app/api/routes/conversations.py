from __future__ import annotations

import asyncio
import inspect
import mimetypes
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.api.routes.images import ImageQuality, start_turn, validate_size
from app.core.database import connect, media_dir, now_iso
from app.core.image_storage import (
    StoredImage,
    discard_stored_image,
    read_original,
    retry_pending_deletions_once,
    store_image,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])
MAX_REFERENCE_IMAGES = 16
Provider = Literal["maolao", "relayrouter", "openai"]
RouteMode = Literal["auto", "manual"]


class ConversationCreate(BaseModel):
    title: str = Field(default="新对话", max_length=100)


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=100)


def validate_reference_count(count: int) -> None:
    if count > MAX_REFERENCE_IMAGES:
        raise HTTPException(status_code=422, detail=f"参考图最多支持 {MAX_REFERENCE_IMAGES} 张")


def _image_payload(row: dict[str, Any]) -> dict[str, Any]:
    base = f"/api/v1/images/{row['id']}"
    return {
        "id": row["id"], "kind": row["kind"], "position": row["position"],
        "file_name": row["file_name"], "mime_type": row["mime_type"],
        "url": f"{base}/preview",
        "thumbnail_url": f"{base}/thumbnail",
        "preview_url": f"{base}/preview",
        "download_url": f"{base}/download",
    }


def _turn_payload(connection: Any, row: Any) -> dict[str, Any]:
    turn = dict(row)
    images = connection.execute(
        "SELECT * FROM images WHERE turn_id = ? ORDER BY kind DESC, position ASC", (turn["id"],)
    ).fetchall()
    turn["images"] = [_image_payload(dict(image)) for image in images]
    attempts = connection.execute(
        """SELECT provider, position, status, error_kind, error_message,
                  submitted_at, completed_at
           FROM provider_attempts WHERE turn_id = ? ORDER BY position""",
        (turn["id"],),
    ).fetchall()
    turn["provider_attempts"] = [dict(attempt) for attempt in attempts]
    turn.pop("effective_prompt", None)
    return turn


def _conversation_payload(connection: Any, row: Any, include_turns: bool = False) -> dict[str, Any]:
    conversation = dict(row)
    turns = connection.execute(
        "SELECT * FROM turns WHERE conversation_id = ? ORDER BY created_at ASC", (conversation["id"],)
    ).fetchall()
    conversation["turn_count"] = len(turns)
    conversation["last_status"] = turns[-1]["status"] if turns else None
    if include_turns:
        conversation["turns"] = [_turn_payload(connection, turn) for turn in turns]
    return conversation


def _get_conversation(connection: Any, conversation_id: str) -> Any:
    row = connection.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="对话不存在")
    return row


@router.get("")
def list_conversations() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
        return [_conversation_payload(connection, row) for row in rows]


@router.post("", status_code=201)
def create_conversation(payload: ConversationCreate) -> dict[str, Any]:
    conversation_id = str(uuid4())
    timestamp = now_iso()
    title = payload.title.strip() or "新对话"
    with connect() as connection:
        connection.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (conversation_id, title, timestamp, timestamp),
        )
        row = connection.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return _conversation_payload(connection, row, include_turns=True)


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str) -> dict[str, Any]:
    with connect() as connection:
        return _conversation_payload(connection, _get_conversation(connection, conversation_id), include_turns=True)


@router.patch("/{conversation_id}")
def update_conversation(conversation_id: str, payload: ConversationUpdate) -> dict[str, Any]:
    with connect() as connection:
        _get_conversation(connection, conversation_id)
        connection.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (payload.title.strip(), now_iso(), conversation_id),
        )
        return _conversation_payload(
            connection,
            connection.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone(),
            include_turns=True,
        )


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str) -> None:
    with connect() as connection:
        _get_conversation(connection, conversation_id)
        files = connection.execute(
            """SELECT stored_name, storage_backend, storage_status,
                      object_key, preview_key, thumbnail_key
               FROM images WHERE turn_id IN
               (SELECT id FROM turns WHERE conversation_id = ?)""", (conversation_id,)
        ).fetchall()
        for item in files:
            if item["storage_backend"] == "cos" or item["storage_status"] == "pending_upload":
                for key in (item["object_key"], item["preview_key"], item["thumbnail_key"]):
                    if key:
                        connection.execute(
                            """INSERT OR IGNORE INTO pending_storage_deletions
                               (id, object_key, created_at) VALUES (?, ?, ?)""",
                            (str(uuid4()), key, now_iso()),
                        )
        connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    for item in files:
        stored_name = item["stored_name"]
        stem = Path(stored_name).stem
        for name in (stored_name, f"{stem}-preview.webp", f"{stem}-thumbnail.webp"):
            (media_dir() / name).unlink(missing_ok=True)
    retry_pending_deletions_once()


def _source_context(connection: Any, conversation_id: str, source_image_id: str | None) -> tuple[str | None, str | None]:
    if not source_image_id:
        return None, None
    row = connection.execute(
        """SELECT images.id, turns.effective_prompt FROM images
           JOIN turns ON turns.id = images.turn_id
           WHERE images.id = ? AND images.kind = 'generated' AND turns.conversation_id = ?""",
        (source_image_id, conversation_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=422, detail="选中的参考图不属于当前对话")
    return row["id"], row["effective_prompt"]


@router.post("/{conversation_id}/turns", status_code=202)
async def create_turn(
    conversation_id: str,
    prompt: str = Form(..., min_length=1, max_length=4000),
    size: str = Form(default="2880x2880"),
    quality: ImageQuality = Form(default="low"),
    n: int = Form(default=1, ge=1, le=10),
    route_mode: RouteMode = Form(default="auto"),
    selected_provider: Provider | None = Form(default=None),
    retry_of_turn_id: str | None = Form(default=None),
    source_image_id: str | None = Form(default=None),
    image: UploadFile | None = File(default=None),
    images: list[UploadFile] | None = File(default=None),
) -> dict[str, Any]:
    # Direct unit-test calls do not resolve FastAPI Form defaults.
    route_mode = route_mode if isinstance(route_mode, str) else "auto"
    quality = quality if isinstance(quality, str) else "low"
    selected_provider = selected_provider if isinstance(selected_provider, str) else None
    retry_of_turn_id = retry_of_turn_id if isinstance(retry_of_turn_id, str) else None
    if route_mode == "manual" and selected_provider is None:
        raise HTTPException(status_code=422, detail="请选择手动生成线路")
    if route_mode == "auto" and selected_provider is not None:
        raise HTTPException(status_code=422, detail="自动模式不能指定单条线路")
    if quality not in {"low", "high"}:
        raise HTTPException(status_code=422, detail="图片质量只支持 low 或 high")
    selected_size = validate_size(size)
    prompt = prompt.strip()
    turn_id = str(uuid4())
    timestamp = now_iso()
    uploads = ([image] if image is not None else []) + (images or [])
    validate_reference_count(len(uploads))
    pending_references: list[tuple[str, str, str, bytes]] = []
    stored_references: list[tuple[str, str, str, StoredImage]] = []

    for upload in uploads:
        if not (upload.content_type or "").startswith("image/"):
            raise HTTPException(status_code=422, detail="参考文件必须是图片")
        content = await upload.read()
        if not content:
            raise HTTPException(status_code=422, detail="参考图不能为空")
        mime_type = upload.content_type or "image/png"
        extension = Path(upload.filename or "").suffix or mimetypes.guess_extension(mime_type) or ".png"
        pending_references.append((upload.filename or f"reference{extension}", mime_type, extension.lower(), content))

    if retry_of_turn_id and not pending_references:
        with connect() as connection:
            retry_turn = connection.execute(
                "SELECT source_image_id FROM turns WHERE id = ? AND conversation_id = ?",
                (retry_of_turn_id, conversation_id),
            ).fetchone()
            if retry_turn is None:
                raise HTTPException(status_code=422, detail="重试任务不属于当前对话")
            rows = []
            if retry_turn["source_image_id"]:
                source = connection.execute(
                    "SELECT * FROM images WHERE id = ?", (retry_turn["source_image_id"],)
                ).fetchone()
                if source is not None:
                    rows.append(source)
            rows.extend(connection.execute(
                "SELECT * FROM images WHERE turn_id = ? AND kind = 'reference' ORDER BY position",
                (retry_of_turn_id,),
            ).fetchall())
        for row in rows:
            image_row = dict(row)
            pending_references.append((
                image_row["file_name"], image_row["mime_type"],
                Path(image_row["stored_name"]).suffix or ".png", read_original(image_row),
            ))
        validate_reference_count(len(pending_references))

    for file_name, mime_type, extension, content in pending_references:
        image_id = str(uuid4())
        try:
            stored = await asyncio.to_thread(
                store_image,
                conversation_id=conversation_id,
                turn_id=turn_id,
                image_id=image_id,
                extension=extension,
                mime_type=mime_type,
                content=content,
            )
        except Exception:
            for stored_reference in stored_references:
                discard_stored_image(stored_reference[3])
            raise
        stored_references.append((image_id, file_name, mime_type, stored))

    try:
        with connect() as connection:
            conversation = _get_conversation(connection, conversation_id)
            source_id: str | None = None
            base_prompt: str | None = None
            if source_image_id or not stored_references:
                source_id, base_prompt = _source_context(connection, conversation_id, source_image_id)
            if source_id and stored_references:
                validate_reference_count(len(stored_references) + 1)
            if retry_of_turn_id:
                retry_source = connection.execute(
                    "SELECT id FROM turns WHERE id = ? AND conversation_id = ?",
                    (retry_of_turn_id, conversation_id),
                ).fetchone()
                if retry_source is None:
                    raise HTTPException(status_code=422, detail="重试任务不属于当前对话")
            effective_prompt = prompt if not base_prompt else f"{base_prompt}\n\n请在参考图基础上进行以下调整：{prompt}"
            connection.execute(
                """INSERT INTO turns
                   (id, conversation_id, prompt, effective_prompt, size, quality, n, status,
                    route_mode, selected_provider, retry_of_turn_id, source_image_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)""",
                (turn_id, conversation_id, prompt, effective_prompt, selected_size, quality, n,
                 route_mode, selected_provider, retry_of_turn_id, source_id, timestamp),
            )
            for position, (image_id, file_name, mime_type, stored) in enumerate(stored_references):
                connection.execute(
                    """INSERT INTO images
                       (id, turn_id, kind, position, file_name, stored_name, mime_type,
                        storage_backend, storage_status, object_key, preview_key,
                        thumbnail_key, created_at)
                       VALUES (?, ?, 'reference', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        image_id, turn_id, position, file_name, stored.stored_name,
                        mime_type, stored.storage_backend, stored.storage_status,
                        stored.object_key, stored.preview_key, stored.thumbnail_key,
                        timestamp,
                    ),
                )
            title = conversation["title"]
            turn_count = connection.execute("SELECT COUNT(*) FROM turns WHERE conversation_id = ?", (conversation_id,)).fetchone()[0]
            if turn_count == 1 and title == "新对话":
                title = prompt[:32] + ("…" if len(prompt) > 32 else "")
            connection.execute("UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?", (title, timestamp, conversation_id))
            row = connection.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
            result = _turn_payload(connection, row)
    except Exception:
        for stored_reference in stored_references:
            discard_stored_image(stored_reference[3])
        raise
    queued = start_turn(turn_id)
    if inspect.isawaitable(queued):
        await queued
    return result
