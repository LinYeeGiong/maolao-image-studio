from io import BytesIO
from typing import get_args

from app.api.routes.images import ImageSize, build_maolao_request


def test_square_size_uses_valid_maximum_pixel_budget() -> None:
    allowed_sizes = get_args(ImageSize)

    assert "2880x2880" in allowed_sizes
    assert "4096x4096" not in allowed_sizes


def test_builds_json_generation_request_without_reference_image() -> None:
    request = build_maolao_request(
        prompt="a neon cat",
        size="2880x2880",
        n=3,
        reference_image=None,
    )

    assert request.action == "generations"
    assert request.json == {
        "model": "gpt-image-2-4k",
        "prompt": "a neon cat",
        "n": 3,
        "quality": "high",
        "response_format": "b64_json",
        "size": "2880x2880",
    }
    assert request.data is None
    assert request.files is None


def test_builds_multipart_edit_request_with_reference_image() -> None:
    reference = ("reference.png", BytesIO(b"png-data"), "image/png")

    request = build_maolao_request(
        prompt="keep the subject",
        size="2160x3840",
        n=2,
        reference_image=reference,
    )

    assert request.action == "edits"
    assert request.json is None
    assert request.data == {
        "model": "gpt-image-2-4k",
        "prompt": "keep the subject",
        "n": "2",
        "quality": "high",
        "response_format": "b64_json",
        "size": "2160x3840",
    }
    assert request.files is not None
    assert request.files["image"][0] == "reference.png"
