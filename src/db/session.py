"""Motor y sesiones de SQLAlchemy.

SQLite en desarrollo, Postgres (Supabase) en producción — la única
diferencia es DATABASE_URL. Para el prototipo el esquema se crea con
`init_db()` (create_all); si el esquema evoluciona en producción,
migrar a Alembic.
"""

import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _build_engine():
    settings = get_settings()
    url = settings.database_url
    connect_args = {}
    if url.startswith("sqlite"):
        # Asegurar el directorio del archivo .db
        db_path = url.replace("sqlite:///", "", 1)
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False}
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


def init_db() -> None:
    """Crea las tablas si no existen (idempotente)."""
    from src.db import models  # noqa: F401 — registra los modelos en Base

    Base.metadata.create_all(bind=get_engine())
    logger.info("Esquema de BD verificado/creado")
