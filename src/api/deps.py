"""Dependencias de FastAPI."""

from collections.abc import Generator

from sqlalchemy.orm import Session

from src.db.session import get_session_factory


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
