from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse

from app.core.database import connect
from app.core.image_storage import ImageVariant, local_variant_path, signed_url

router = APIRouter(prefix="/images", tags=["images"])


def _image_row(image_id: str) -> dict[str, Any]:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM images WHERE id = ?", (image_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    return dict(row)


def _deliver(
    image_id: str,
    variant: ImageVariant,
    *,
    download: bool = False,
) -> Response:
    row = _image_row(image_id)
    if row.get("storage_backend") == "cos" and row.get("storage_status") == "ready":
        url = signed_url(
            row,
            variant,
            download_name=row["file_name"] if download else None,
        )
        return RedirectResponse(url, status_code=302)

    path = local_variant_path(row, variant)
    if not path.is_file() and variant != "original":
        path = local_variant_path(row, "original")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图片文件不存在")
    media_type = row["mime_type"] if path.suffix.lower() != ".webp" else "image/webp"
    return FileResponse(
        path,
        media_type=media_type,
        filename=row["file_name"] if download else None,
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/{image_id}/thumbnail")
def thumbnail_image(image_id: str) -> Response:
    return _deliver(image_id, "thumbnail")


@router.get("/{image_id}/preview")
def preview_image(image_id: str) -> Response:
    return _deliver(image_id, "preview")


@router.get("/{image_id}/original")
def original_image(image_id: str) -> Response:
    return _deliver(image_id, "original")


@router.get("/{image_id}/download")
def download_image(image_id: str) -> Response:
    return _deliver(image_id, "original", download=True)
