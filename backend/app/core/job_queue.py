from __future__ import annotations

import logging

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.database import connect
from app.core.settings import settings

logger = logging.getLogger(__name__)


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.REDIS_URL)


async def enqueue_turn(turn_id: str) -> bool:
    """Best-effort enqueue; SQLite keeps the durable source of pending work."""
    redis: ArqRedis | None = None
    try:
        redis = await create_pool(redis_settings())
        await redis.enqueue_job(
            "process_turn_job",
            turn_id,
            _job_id=f"turn:{turn_id}",
            _queue_name=settings.ARQ_QUEUE_NAME,
        )
        return True
    except Exception:
        logger.exception("Unable to enqueue image turn %s", turn_id)
        return False
    finally:
        if redis is not None:
            await redis.aclose()


async def reconcile_pending_turns() -> None:
    with connect() as connection:
        rows = connection.execute(
            "SELECT id FROM turns WHERE status IN ('queued', 'processing')"
        ).fetchall()
    for row in rows:
        await enqueue_turn(str(row["id"]))
