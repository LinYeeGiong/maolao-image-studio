import asyncio
from io import BytesIO
from typing import get_args

import httpx
import pytest

from app.api.routes.images import (
    ImageSize,
    _upstream_error,
    build_image_download_request,
    build_maolao_request,
    download_generated_image,
    exception_message,
)


def test_square_size_uses_valid_maximum_pixel_budget() -> None:
    allowed_sizes = get_args(ImageSize)

    assert "2880x2880" in allowed_sizes
    assert "4096x4096" not in allowed_sizes


def test_builds_json_generation_request_without_reference_image() -> None:
    request = build_maolao_request(
        prompt="a neon cat",
        size="2880x2880",
        n=3,
        quality="low",
        reference_images=[],
    )

    assert request.action == "generations"
    assert request.json == {
        "model": "gpt-image-2-4k",
        "prompt": "a neon cat",
        "n": 3,
        "quality": "low",
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
        quality="high",
        reference_images=[reference],
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
    assert request.files[0][0] == "image"
    assert request.files[0][1][0] == "reference.png"


def test_builds_multipart_request_with_repeated_image_field_for_multiple_images() -> (
    None
):
    references = [
        ("first.png", BytesIO(b"first"), "image/png"),
        ("second.webp", BytesIO(b"second"), "image/webp"),
    ]

    request = build_maolao_request(
        prompt="combine both subjects",
        size="2880x2880",
        n=1,
        quality="low",
        reference_images=references,
    )

    assert request.action == "edits"
    assert request.files is not None
    assert [field for field, _ in request.files] == ["image", "image"]
    assert [upload[0] for _, upload in request.files] == ["first.png", "second.webp"]


def test_uses_absolute_https_result_url_without_api_authorization() -> None:
    request = build_image_download_request(
        result_item={"url": "https://example-bucket.s3.amazonaws.com/result.png"},
        upstream_task_id="task-1",
        index=0,
        base_url="https://maolaoapi.com",
        api_headers={"Authorization": "Bearer secret"},
    )

    assert request.url == "https://example-bucket.s3.amazonaws.com/result.png"
    assert request.headers is None


def test_authenticates_absolute_result_url_on_maolao_origin() -> None:
    headers = {"Authorization": "Bearer secret"}

    request = build_image_download_request(
        result_item={"url": "https://maolaoapi.com/v1/images/tasks/task-1/content/0"},
        upstream_task_id="task-1",
        index=0,
        base_url="https://maolaoapi.com",
        api_headers=headers,
    )

    assert request.headers == headers


def test_resolves_relative_result_url_with_api_authorization() -> None:
    headers = {"Authorization": "Bearer secret"}

    request = build_image_download_request(
        result_item={"url": "/v1/images/tasks/task-1/content/0"},
        upstream_task_id="task-1",
        index=0,
        base_url="https://maolaoapi.com",
        api_headers=headers,
    )

    assert request.url == "https://maolaoapi.com/v1/images/tasks/task-1/content/0"
    assert request.headers == headers


def test_falls_back_to_content_endpoint_when_result_url_is_missing() -> None:
    headers = {"Authorization": "Bearer secret"}

    request = build_image_download_request(
        result_item={},
        upstream_task_id="task/with slash",
        index=2,
        base_url="https://maolaoapi.com",
        api_headers=headers,
    )

    assert (
        request.url
        == "https://maolaoapi.com/v1/images/tasks/task%2Fwith%20slash/content/2"
    )
    assert request.headers == headers


def test_rejects_insecure_absolute_result_url() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        build_image_download_request(
            result_item={"url": "http://external.example/result.png"},
            upstream_task_id="task-1",
            index=0,
            base_url="https://maolaoapi.com",
            api_headers={"Authorization": "Bearer secret"},
        )


def test_rejects_untrusted_absolute_result_host() -> None:
    with pytest.raises(ValueError, match="trusted"):
        build_image_download_request(
            result_item={"url": "https://127.0.0.1/internal"},
            upstream_task_id="task-1",
            index=0,
            base_url="https://maolaoapi.com",
            api_headers={"Authorization": "Bearer secret"},
        )


def test_retries_temporary_download_failure_then_returns_image() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(404, json={"error": "not ready"}, request=request)
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\npng-data",
            headers={"content-type": "image/png"},
            request=request,
        )

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            request = build_image_download_request(
                result_item={
                    "url": "https://example-bucket.s3.amazonaws.com/result.png"
                },
                upstream_task_id="task-1",
                index=0,
                base_url="https://maolaoapi.com",
                api_headers={"Authorization": "Bearer secret"},
            )
            return await download_generated_image(
                client,
                request,
                attempts=3,
                retry_delay_seconds=0,
            )

    response = asyncio.run(run())

    assert response.content == b"\x89PNG\r\n\x1a\npng-data"
    assert attempts == 2


def test_download_opts_out_of_compressed_transfer_encodings() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("accept-encoding"))
        return httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\npng-data",
            headers={"content-type": "image/png"},
            request=request,
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            request = build_image_download_request(
                result_item={},
                upstream_task_id="task-1",
                index=0,
                base_url="https://maolaoapi.com",
                api_headers={"Authorization": "Bearer secret"},
            )
            await download_generated_image(client, request, retry_delay_seconds=0)

    asyncio.run(run())

    assert seen == ["identity"]


def test_retries_corrupted_gzip_stream_then_returns_image() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            # Claims gzip content-encoding but the body is not valid gzip,
            # simulating a CDN stream that was cut short or corrupted.
            return httpx.Response(
                200,
                content=b"not-actually-gzip-data",
                headers={"content-type": "image/png", "content-encoding": "gzip"},
                request=request,
            )
        return httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\npng-data",
            headers={"content-type": "image/png"},
            request=request,
        )

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            request = build_image_download_request(
                result_item={
                    "url": "https://example-bucket.s3.amazonaws.com/result.png"
                },
                upstream_task_id="task-1",
                index=0,
                base_url="https://maolaoapi.com",
                api_headers={"Authorization": "Bearer secret"},
            )
            return await download_generated_image(
                client,
                request,
                attempts=3,
                retry_delay_seconds=0,
            )

    response = asyncio.run(run())

    assert response.content == b"\x89PNG\r\n\x1a\npng-data"
    assert attempts == 2


def test_raises_clear_error_when_stream_stays_corrupted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"not-actually-gzip-data",
            headers={"content-type": "image/png", "content-encoding": "gzip"},
            request=request,
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            request = build_image_download_request(
                result_item={
                    "url": "https://example-bucket.s3.amazonaws.com/result.png"
                },
                upstream_task_id="task-1",
                index=0,
                base_url="https://maolaoapi.com",
                api_headers={"Authorization": "Bearer secret"},
            )
            await download_generated_image(
                client,
                request,
                attempts=2,
                retry_delay_seconds=0,
            )

    with pytest.raises(RuntimeError, match="corrupted"):
        asyncio.run(run())


def test_does_not_retry_permanent_download_failure() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            400,
            json={"error": {"message": "invalid result URL"}},
            request=request,
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            request = build_image_download_request(
                result_item={
                    "url": "https://example-bucket.s3.amazonaws.com/result.png"
                },
                upstream_task_id="task-1",
                index=0,
                base_url="https://maolaoapi.com",
                api_headers={"Authorization": "Bearer secret"},
            )
            await download_generated_image(
                client,
                request,
                attempts=3,
                retry_delay_seconds=0,
            )

    with pytest.raises(RuntimeError, match="invalid result URL"):
        asyncio.run(run())

    assert attempts == 1


def test_rejects_successful_non_image_download() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html>not an image</html>",
            headers={"content-type": "text/html"},
            request=request,
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            request = build_image_download_request(
                result_item={
                    "url": "https://example-bucket.s3.amazonaws.com/result.png"
                },
                upstream_task_id="task-1",
                index=0,
                base_url="https://maolaoapi.com",
                api_headers={"Authorization": "Bearer secret"},
            )
            await download_generated_image(client, request, retry_delay_seconds=0)

    with pytest.raises(RuntimeError, match="image content type"):
        asyncio.run(run())


def test_rejects_download_larger_than_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\nlarge",
            headers={"content-type": "image/png", "content-length": "13"},
            request=request,
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            request = build_image_download_request(
                result_item={
                    "url": "https://example-bucket.s3.amazonaws.com/result.png"
                },
                upstream_task_id="task-1",
                index=0,
                base_url="https://maolaoapi.com",
                api_headers={"Authorization": "Bearer secret"},
            )
            await download_generated_image(
                client,
                request,
                retry_delay_seconds=0,
                max_bytes=12,
            )

    with pytest.raises(RuntimeError, match="size limit"):
        asyncio.run(run())


def test_does_not_retry_nonstandard_600_status() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(600, text="nonstandard", request=request)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            request = build_image_download_request(
                result_item={},
                upstream_task_id="task-1",
                index=0,
                base_url="https://maolaoapi.com",
                api_headers={"Authorization": "Bearer secret"},
            )
            await download_generated_image(
                client,
                request,
                attempts=3,
                retry_delay_seconds=0,
            )

    with pytest.raises(RuntimeError, match="nonstandard"):
        asyncio.run(run())

    assert attempts == 1


def test_limits_error_response_body_before_reporting_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b"0123456789", request=request)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            request = build_image_download_request(
                result_item={},
                upstream_task_id="task-1",
                index=0,
                base_url="https://maolaoapi.com",
                api_headers={"Authorization": "Bearer secret"},
            )
            await download_generated_image(
                client,
                request,
                retry_delay_seconds=0,
                max_error_bytes=8,
            )

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(run())

    assert "01234567" in str(exc_info.value)
    assert "0123456789" not in str(exc_info.value)


def test_summarizes_gateway_html_error_page_instead_of_dumping_markup() -> None:
    response = httpx.Response(
        502,
        text="<!DOCTYPE html><html><head><title>502: Bad gateway</title></head></html>",
        headers={"content-type": "text/html; charset=UTF-8"},
    )

    message = _upstream_error(response)

    assert "502" in message
    assert "<" not in message


def test_keeps_plain_text_upstream_error_body() -> None:
    response = httpx.Response(400, text="quota exceeded")

    assert _upstream_error(response) == "quota exceeded"


def test_formats_empty_exception_with_class_name() -> None:
    assert exception_message(httpx.ReadTimeout("")) == "ReadTimeout"
