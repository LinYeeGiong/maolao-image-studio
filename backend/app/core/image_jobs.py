from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from time import monotonic
from typing import Any, Literal
from urllib.parse import quote, urlparse
from uuid import uuid4

import httpx

from app.api.routes.images import (
    IDENTITY_ENCODING_HEADERS,
    _save_generated_image,
    _upstream_error,
    build_image_download_request,
    build_maolao_request,
    download_generated_image,
)
from app.core.database import connect, now_iso, row_dict
from app.core.settings import settings

Provider = Literal["maolao", "relayrouter", "openai"]
ErrorKind = Literal["recoverable", "incompatible", "fatal", "unknown_submission"]
PROVIDERS: tuple[Provider, ...] = ("maolao", "relayrouter", "openai")


@dataclass(frozen=True)
class ProviderFailure(Exception):
    kind: ErrorKind
    message: str


def _provider_configured(provider: Provider) -> bool:
    return {
        "maolao": bool(settings.MAOLAO_API_KEY),
        "relayrouter": bool(settings.RELAYROUTER_API_KEY),
        "openai": bool(settings.OPENAI_API_KEY),
    }[provider]


def _provider_supports_references(provider: Provider) -> bool:
    return provider in PROVIDERS


def _headers(provider: Provider) -> dict[str, str]:
    key = {
        "maolao": settings.MAOLAO_API_KEY,
        "relayrouter": settings.RELAYROUTER_API_KEY,
        "openai": settings.OPENAI_API_KEY,
    }[provider]
    return {"Authorization": f"Bearer {key}"}


def _load_turn(turn_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        return row_dict(connection.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone())


def _update_turn(turn_id: str, **values: Any) -> None:
    if not values:
        return
    assignments = ", ".join(f"{key} = ?" for key in values)
    with connect() as connection:
        connection.execute(
            f"UPDATE turns SET {assignments} WHERE id = ?", (*values.values(), turn_id)
        )  # noqa: S608


def _references_for_turn(turn: dict[str, Any]) -> list[tuple[str, bytes, str]]:
    from app.api.routes.images import _references_for_turn as load_references

    return [
        (name, buffer.getvalue(), media_type)
        for name, buffer, media_type in load_references(turn)
    ]


def _attempts(turn_id: str) -> list[dict[str, Any]]:
    with connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM provider_attempts WHERE turn_id = ? ORDER BY position", (turn_id,)
            ).fetchall()
        ]


def _create_attempt(turn_id: str, provider: Provider, position: int) -> dict[str, Any]:
    attempt = {
        "id": str(uuid4()), "turn_id": turn_id, "provider": provider,
        "position": position, "status": "submitting", "created_at": now_iso(),
    }
    with connect() as connection:
        connection.execute(
            """INSERT INTO provider_attempts
               (id, turn_id, provider, position, status, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
            (attempt["id"], turn_id, provider, position, attempt["status"], attempt["created_at"]),
        )
    return attempt


def _update_attempt(attempt_id: str, **values: Any) -> None:
    if not values:
        return
    assignments = ", ".join(f"{key} = ?" for key in values)
    with connect() as connection:
        connection.execute(
            f"UPDATE provider_attempts SET {assignments} WHERE id = ?", (*values.values(), attempt_id)
        )  # noqa: S608


def _safe_message(message: str) -> str:
    collapsed = " ".join(message.split())
    for marker in ("Bearer ", "sk-"):
        if marker in collapsed:
            collapsed = collapsed.split(marker, 1)[0].rstrip() + " [redacted]"
    return collapsed[:500] or "Upstream request failed"


def _classify_response(response: httpx.Response) -> ProviderFailure:
    message = _safe_message(_upstream_error(response))
    if response.status_code == 429 or 500 <= response.status_code <= 599:
        return ProviderFailure("recoverable", message)
    if response.status_code in {401, 403}:
        return ProviderFailure("fatal", message)
    lowered = message.lower()
    if any(term in lowered for term in ("unsupported", "not support", "size", "model", "edit")):
        return ProviderFailure("incompatible", message)
    return ProviderFailure("fatal", message)


def _classify_exception(exc: Exception) -> ProviderFailure:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError)):
        return ProviderFailure("recoverable", "Unable to connect to upstream provider")
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout)):
        return ProviderFailure("unknown_submission", "Provider response timed out after the request was sent")
    if isinstance(exc, httpx.TimeoutException):
        return ProviderFailure("recoverable", "Upstream request timed out")
    if isinstance(exc, httpx.DecodingError) or (
        isinstance(exc, RuntimeError) and "stream was corrupted" in str(exc)
    ):
        # Transient transport corruption (e.g. a gzip stream cut short by the
        # upstream CDN), not a permanent problem with the request itself.
        return ProviderFailure("recoverable", "Downloaded image data was corrupted in transit")
    return ProviderFailure("fatal", _safe_message(str(exc) or type(exc).__name__))


async def _download_url(client: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ProviderFailure("fatal", "Provider returned an invalid image URL")
    response = await client.get(
        url, headers=IDENTITY_ENCODING_HEADERS, follow_redirects=True
    )
    if not response.is_success:
        raise _classify_response(response)
    content_type = response.headers.get("content-type", "image/png").split(";", 1)[0]
    if not content_type.startswith("image/"):
        raise ProviderFailure("fatal", "Provider returned a non-image result")
    if len(response.content) > 50 * 1024 * 1024:
        raise ProviderFailure("fatal", "Generated image exceeds the size limit")
    return response.content, content_type


async def _run_maolao(
    client: httpx.AsyncClient, turn: dict[str, Any], attempt: dict[str, Any]
) -> list[tuple[bytes, str]]:
    references = _references_for_turn(turn)
    upstream_task_id = attempt.get("external_task_id")
    if not upstream_task_id:
        from io import BytesIO

        request = build_maolao_request(
            prompt=turn["effective_prompt"], size=turn["size"], n=turn["n"],
            quality=turn.get("quality", "low"),
            reference_images=[(name, BytesIO(content), content_type) for name, content, content_type in references],
        )
        try:
            response = await client.post(
                f"{settings.MAOLAO_BASE_URL.rstrip('/')}/v1/images/tasks",
                params={"action": request.action}, headers=_headers("maolao"), json=request.json,
                data=request.data, files=request.files,
            )
        except Exception as exc:
            raise _classify_exception(exc) from exc
        if not response.is_success:
            raise _classify_response(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderFailure("fatal", "Maolao returned an invalid task response") from exc
        upstream_task_id = payload.get("task_id") or payload.get("id")
        if not upstream_task_id:
            raise ProviderFailure("fatal", "Maolao did not return a task ID")
        _update_attempt(attempt["id"], external_task_id=str(upstream_task_id), status="processing", submitted_at=now_iso())
        _update_turn(turn["id"], upstream_task_id=str(upstream_task_id), status="processing")
    while True:
        try:
            response = await client.get(
                f"{settings.MAOLAO_BASE_URL.rstrip('/')}/v1/images/tasks/{quote(str(upstream_task_id), safe='')}",
                headers=_headers("maolao"), timeout=30,
            )
        except Exception as exc:
            raise _classify_exception(exc) from exc
        if not response.is_success:
            raise _classify_response(response)
        task_payload = response.json()
        status = task_payload.get("status", "processing")
        if status in {"succeeded", "failed"}:
            break
        await asyncio.sleep(settings.TASK_POLL_INTERVAL_SECONDS)
    if task_payload.get("status") == "failed":
        message = _safe_message(str(task_payload.get("error") or "Maolao image task failed"))
        lowered = message.lower()
        kind: ErrorKind = "fatal" if any(term in lowered for term in ("safety", "policy", "moderation")) else "recoverable"
        raise ProviderFailure(kind, message)
    delivered = (task_payload.get("result") or {}).get("data") or []
    results: list[tuple[bytes, str]] = []
    for index, result_item in enumerate(delivered or [{} for _ in range(int(turn["n"]))]):
        request = build_image_download_request(
            result_item=result_item if isinstance(result_item, dict) else {},
            upstream_task_id=str(upstream_task_id), index=index,
            base_url=settings.MAOLAO_BASE_URL, api_headers=_headers("maolao"),
        )
        try:
            response = await download_generated_image(client, request)
        except Exception as exc:
            raise _classify_exception(exc) from exc
        results.append((response.content, response.headers.get("content-type", "image/png")))
    return results


async def _run_openai_compatible(
    client: httpx.AsyncClient, provider: Provider, turn: dict[str, Any]
) -> list[tuple[bytes, str]]:
    references = _references_for_turn(turn)
    base_url = settings.RELAYROUTER_BASE_URL if provider == "relayrouter" else settings.OPENAI_BASE_URL
    model = settings.RELAYROUTER_IMAGE_MODEL if provider == "relayrouter" else settings.OPENAI_IMAGE_MODEL
    try:
        if references:
            from io import BytesIO

            response = await client.post(
                f"{base_url.rstrip('/')}/images/edits", headers=_headers(provider),
                data={"model": model, "prompt": turn["effective_prompt"], "n": str(turn["n"]), "size": turn["size"], "quality": turn.get("quality", "low")},
                files=[("image[]", (name, BytesIO(content), content_type)) for name, content, content_type in references],
            )
        else:
            response = await client.post(
                f"{base_url.rstrip('/')}/images/generations", headers=_headers(provider),
                json={"model": model, "prompt": turn["effective_prompt"], "n": turn["n"], "size": turn["size"], "quality": turn.get("quality", "low")},
            )
    except Exception as exc:
        raise _classify_exception(exc) from exc
    if not response.is_success:
        raise _classify_response(response)
    try:
        items = response.json().get("data") or []
    except ValueError as exc:
        raise ProviderFailure("fatal", "Provider returned an invalid image response") from exc
    results: list[tuple[bytes, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        encoded = item.get("b64_json")
        if isinstance(encoded, str):
            try:
                results.append((base64.b64decode(encoded), "image/png"))
            except ValueError as exc:
                raise ProviderFailure("fatal", "Provider returned invalid image data") from exc
        elif isinstance(item.get("url"), str):
            results.append(await _download_url(client, item["url"]))
    if not results:
        raise ProviderFailure("fatal", "Provider returned no images")
    return results


async def _execute_provider(
    client: httpx.AsyncClient, provider: Provider, turn: dict[str, Any], attempt: dict[str, Any]
) -> list[tuple[bytes, str]]:
    if provider == "maolao":
        return await _run_maolao(client, turn, attempt)
    return await _run_openai_compatible(client, provider, turn)


def _save_results(turn_id: str, results: list[tuple[bytes, str]]) -> None:
    with connect() as connection:
        existing = {
            row["position"] for row in connection.execute(
                "SELECT position FROM images WHERE turn_id = ? AND kind = 'generated'", (turn_id,)
            ).fetchall()
        }
    for position, (content, content_type) in enumerate(results):
        if position not in existing:
            _save_generated_image(turn_id=turn_id, position=position, content=content, content_type=content_type)


def _finish_turn(turn: dict[str, Any], status: str, error: str | None, started: float) -> None:
    _update_turn(
        turn["id"], status=status, error=error,
        elapsed_seconds=round(monotonic() - started, 3), completed_at=now_iso(),
    )
    with connect() as connection:
        connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now_iso(), turn["conversation_id"]))


def _attempt_payload(turn_id: str) -> str:
    entries = []
    for attempt in _attempts(turn_id):
        message = attempt.get("error_message")
        entries.append(f"{attempt['provider']}: {message or attempt['status']}")
    return "; ".join(entries) or "No provider is enabled"


async def process_turn_job(_: dict[str, Any], turn_id: str) -> None:
    started = monotonic()
    turn = _load_turn(turn_id)
    if turn is None or turn["status"] in {"succeeded", "failed", "partially_succeeded", "needs_attention"}:
        return
    existing = _attempts(turn_id)
    if any(a["status"] == "submitting" and a["provider"] != "maolao" for a in existing):
        _finish_turn(turn, "needs_attention", "A provider request may have been accepted but did not return a response", started)
        return
    _update_turn(turn_id, status="processing", error=None)
    references = _references_for_turn(turn)
    plan: list[Provider]
    if turn.get("route_mode") == "manual":
        plan = [turn["selected_provider"]]
    else:
        plan = list(PROVIDERS)
    prior = {attempt["provider"]: attempt for attempt in existing}
    timeout = httpx.Timeout(
        settings.PROVIDER_REQUEST_TIMEOUT_SECONDS,
        connect=settings.PROVIDER_CONNECT_TIMEOUT_SECONDS,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        for position, provider in enumerate(plan):
            if provider not in PROVIDERS:
                _finish_turn(turn, "failed", "Unsupported provider selection", started)
                return
            if not _provider_configured(provider):
                attempt = _create_attempt(turn_id, provider, position)
                _update_attempt(attempt["id"], status="skipped", error_kind="incompatible", error_message="Provider is not configured", completed_at=now_iso())
                continue
            if references and not _provider_supports_references(provider):
                attempt = _create_attempt(turn_id, provider, position)
                _update_attempt(attempt["id"], status="skipped", error_kind="incompatible", error_message="Provider does not support reference-image editing", completed_at=now_iso())
                if turn.get("route_mode") == "manual":
                    _finish_turn(turn, "failed", "RelayRouter does not support reference-image editing", started)
                    return
                continue
            attempt = prior.get(provider) or _create_attempt(turn_id, provider, position)
            if provider == "maolao" and attempt.get("external_task_id"):
                _update_attempt(attempt["id"], status="processing")
            elif provider != "maolao":
                _update_attempt(attempt["id"], submitted_at=now_iso())
            try:
                results = await _execute_provider(client, provider, turn, attempt)
            except ProviderFailure as failure:
                _update_attempt(attempt["id"], status="failed", error_kind=failure.kind, error_message=failure.message, completed_at=now_iso())
                if failure.kind == "unknown_submission":
                    _finish_turn(turn, "needs_attention", failure.message, started)
                    return
                if turn.get("route_mode") == "manual" or failure.kind == "fatal":
                    _finish_turn(turn, "failed", failure.message, started)
                    return
                continue
            _save_results(turn_id, results)
            status = "succeeded" if len(results) >= int(turn["n"]) else "partially_succeeded"
            _update_attempt(attempt["id"], status=status, completed_at=now_iso())
            _finish_turn(turn, status, None if status == "succeeded" else f"Only {len(results)} of {turn['n']} images were returned", started)
            return
    _finish_turn(turn, "failed", _attempt_payload(turn_id), started)
