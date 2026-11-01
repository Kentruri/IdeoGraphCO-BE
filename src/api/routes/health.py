from fastapi import APIRouter

from src.config import APP_VERSION

router = APIRouter(tags=["health"])


@router.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok", "version": APP_VERSION}
