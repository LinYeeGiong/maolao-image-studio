import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles

from app.api.main import api_router
from app.api.routes.images import resume_pending_turns
from app.core.database import init_database, media_dir
from app.core.image_storage import (
    retry_pending_deletions_once,
    retry_pending_uploads_once,
)
from app.core.settings import settings


async def run_storage_maintenance_once() -> None:
    await asyncio.gather(
        asyncio.to_thread(retry_pending_uploads_once),
        asyncio.to_thread(retry_pending_deletions_once),
    )


async def storage_maintenance_loop() -> None:
    while True:
        await run_storage_maintenance_once()
        await asyncio.sleep(settings.COS_RETRY_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_database()
    resume_pending_turns()
    maintenance = asyncio.create_task(storage_maintenance_loop())
    try:
        yield
    finally:
        maintenance.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
    lifespan=lifespan,
)

app.mount(f"{settings.API_V1_STR}/media", StaticFiles(directory=media_dir()), name="media")
app.include_router(api_router, prefix=settings.API_V1_STR)
