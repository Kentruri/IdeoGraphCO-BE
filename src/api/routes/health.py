from typing import Annotated

from fastapi import APIRouter, Depends

from src.config import APP_VERSION, Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/api/v1/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    return {
        "status": "ok",
        "version": APP_VERSION,
        # La mesa de redacción solo ofrece la explicación en vivo si está activa.
        "desk_explanations": settings.desk_explanation_mode == "claude_cli",
    }
