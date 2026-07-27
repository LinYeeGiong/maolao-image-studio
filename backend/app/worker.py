from __future__ import annotations

from arq.connections import RedisSettings

from app.core.database import init_database
from app.core.image_jobs import process_turn_job
from app.core.settings import settings


async def worker_startup(_: dict[str, object]) -> None:
    init_database()


class WorkerSettings:
    functions = [process_turn_job]
    on_startup = worker_startup
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    queue_name = settings.ARQ_QUEUE_NAME
    job_timeout = settings.ARQ_JOB_TIMEOUT_SECONDS
    max_jobs = settings.ARQ_MAX_JOBS
    keep_result = 0
