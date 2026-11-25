"""Dependencias de FastAPI."""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from src.config import Settings, get_settings
from src.db.session import get_session_factory

DESK_EXPLANATIONS_DISABLED_DETAIL = (
    "La explicación en vivo no está activa en este servidor (DESK_EXPLANATION_MODE=off)."
)


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


class DeskExplanationsDisabled(Exception):
    """La explicación en vivo y las fichas están apagadas; la app responde 503."""


def require_desk_explanations(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    """Corta con 503 `explanations_disabled` antes de abrir el stream o de guardar."""
    if settings.desk_explanation_mode != "claude_cli":
        raise DeskExplanationsDisabled()
