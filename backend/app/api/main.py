from fastapi import APIRouter

from app.api.routes import images

api_router = APIRouter()
api_router.include_router(images.router)
