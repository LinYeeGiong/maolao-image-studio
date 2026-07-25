from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any, Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.core.settings import settings

router = APIRouter(prefix="/images", tags=["images"])

ImageSize = Literal["2880x2880", "3840x2160", "2160x3840"]


@dataclass(frozen=True)
class MaolaoRequest:
    action: str
    json: dict[str, Any] | None
    data: dict[str, str] | None
    files: dict[str, tuple[str, BytesIO, str]] | None


def build_maolao_request(
    *,
    prompt: str,
    size: ImageSize,
    n: int,
    reference_image: tuple[str, BytesIO, str] | None,
) -> MaolaoRequest:
    common = {
        "model": "gpt-image-2-4k",
        "prompt": prompt,
        "n": n,
        "quality": "high",
        "response_format": "b64_json",
        "size": size,
    }
    if reference_image is None:
        return MaolaoRequest(
            action="generations", json=common, data=None, files=None
        )

    return MaolaoRequest(
        action="edits",
        json=None,
        data={key: str(value) for key, value in common.items()},
        files={"image": reference_image},
    )


def _headers() -> dict[str, str]:
    if not settings.MAOLAO_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="服务端尚未配置 MAOLAO_API_KEY",
        )
    return {"Authorization": f"Bearer {settings.MAOLAO_API_KEY}"}


def _raise_upstream_error(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        detail = response.json()
    except ValueError:
        detail = response.text or "MaolaoAPI 请求失败"
    raise HTTPException(status_code=response.status_code, detail=detail)


@router.post("/tasks", status_code=202)
async def create_image_task(
    prompt: str = Form(..., min_length=1),
    size: ImageSize = Form(default="2880x2880"),
    n: int = Form(default=1, ge=1, le=128),
    image: UploadFile | None = File(default=None),
) -> Any:
    reference_image = None
    if image is not None:
        reference_image = (
            image.filename or "reference.png",
            BytesIO(await image.read()),
            image.content_type or "application/octet-stream",
        )
    upstream = build_maolao_request(
        prompt=prompt.strip(), size=size, n=n, reference_image=reference_image
    )
    url = f"{settings.MAOLAO_BASE_URL}/v1/images/tasks"

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            url,
            params={"action": upstream.action},
            headers=_headers(),
            json=upstream.json,
            data=upstream.data,
            files=upstream.files,
        )
    _raise_upstream_error(response)
    return response.json()


@router.get("/tasks/{task_id}")
async def get_image_task(task_id: str) -> Any:
    url = f"{settings.MAOLAO_BASE_URL}/v1/images/tasks/{quote(task_id, safe='')}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=_headers())
    _raise_upstream_error(response)
    return response.json()


@router.get("/tasks/{task_id}/content/{index}")
async def get_image_content(task_id: str, index: int) -> Response:
    url = (
        f"{settings.MAOLAO_BASE_URL}/v1/images/tasks/"
        f"{quote(task_id, safe='')}/content/{index}"
    )
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.get(url, headers=_headers())
    _raise_upstream_error(response)
    return Response(
        content=response.content,
        media_type=response.headers.get("content-type", "image/png"),
    )
