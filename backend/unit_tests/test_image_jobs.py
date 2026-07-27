import asyncio

import httpx

from app.core import image_jobs
from app.core.database import connect, init_database, now_iso
from app.core.settings import settings


def seed_turn(tmp_path, monkeypatch, *, route_mode: str = "auto") -> None:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    init_database()
    with connect() as connection:
        connection.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?)",
            ("conversation-1", "Test", now_iso(), now_iso()),
        )
        connection.execute(
            """INSERT INTO turns
               (id, conversation_id, prompt, effective_prompt, size, n, status,
                route_mode, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("turn-1", "conversation-1", "cat", "cat", "2880x2880", 1,
             "queued", route_mode, now_iso()),
        )


def test_auto_mode_falls_back_after_recoverable_failure(tmp_path, monkeypatch) -> None:
    seed_turn(tmp_path, monkeypatch)
    monkeypatch.setattr(image_jobs, "_provider_configured", lambda _: True)
    monkeypatch.setattr(image_jobs, "_save_results", lambda *_: None)

    async def execute(_, provider, __, ___):
        if provider == "maolao":
            raise image_jobs.ProviderFailure("recoverable", "upstream unavailable")
        return [(b"image", "image/png")]

    monkeypatch.setattr(image_jobs, "_execute_provider", execute)
    asyncio.run(image_jobs.process_turn_job({}, "turn-1"))

    with connect() as connection:
        turn = connection.execute("SELECT status FROM turns WHERE id = 'turn-1'").fetchone()
        attempts = connection.execute(
            "SELECT provider, status FROM provider_attempts WHERE turn_id = 'turn-1' ORDER BY position"
        ).fetchall()
    assert turn["status"] == "succeeded"
    assert [(row["provider"], row["status"]) for row in attempts] == [
        ("maolao", "failed"), ("relayrouter", "succeeded"),
    ]


def test_unknown_submission_stops_automatic_fallback(tmp_path, monkeypatch) -> None:
    seed_turn(tmp_path, monkeypatch)
    monkeypatch.setattr(image_jobs, "_provider_configured", lambda _: True)

    async def execute(*_):
        raise image_jobs.ProviderFailure("unknown_submission", "response timed out")

    monkeypatch.setattr(image_jobs, "_execute_provider", execute)
    asyncio.run(image_jobs.process_turn_job({}, "turn-1"))

    with connect() as connection:
        turn = connection.execute("SELECT status FROM turns WHERE id = 'turn-1'").fetchone()
        attempts = connection.execute(
            "SELECT provider FROM provider_attempts WHERE turn_id = 'turn-1'"
        ).fetchall()
    assert turn["status"] == "needs_attention"
    assert [row["provider"] for row in attempts] == ["maolao"]


def test_classifies_rate_limit_as_recoverable() -> None:
    response = httpx.Response(429, json={"error": {"message": "slow down"}})

    failure = image_jobs._classify_response(response)

    assert failure.kind == "recoverable"


def test_relayrouter_supports_reference_image_editing() -> None:
    assert image_jobs._provider_supports_references("relayrouter") is True
