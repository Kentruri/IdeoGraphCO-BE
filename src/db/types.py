"""Tipos de columna propios."""

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from src.core.dates import as_utc


class UTCDateTime(TypeDecorator[datetime]):
    """DateTime(timezone=True) que se guarda en UTC y se lee en UTC con zona.

    SQLite no guarda la zona: SQLAlchemy escribe la hora de pared del valor
    (una hora en -05:00 quedaría como si fuera UTC) y la devuelve sin zona.
    Postgres (timestamptz) sí la guarda y la devuelve en la zona de la sesión.
    Con este tipo las dos bases reciben el instante en UTC y entregan un
    datetime en UTC con zona. El DDL es el mismo de DateTime(timezone=True).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return as_utc(value) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return as_utc(value) if value is not None else None
