from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles

from app.api.main import api_router
from app.api.routes.images import resume_pending_turns
from app.core.database import init_database, media_dir
from app.core.settings import settings


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_database()
    resume_pending_turns()
    yield


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
