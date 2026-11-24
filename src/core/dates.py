"""Fechas: todo el BE trabaja en UTC con zona horaria explícita."""

from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    """El mismo instante en UTC y con zona.

    Un datetime sin zona se toma como UTC: es la convención de la ingesta
    (RSS y metadatos sin zona se guardan en UTC) y lo que SQLite devuelve,
    porque no guarda la zona.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
