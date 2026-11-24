"""Contrato v2: fechas siempre con zona (UTC) y nombre legible del medio."""

import os
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from src.db.models import Article, Story
from src.db.session import Base
from src.db.types import UTCDateTime
from src.ingestion.sources import SOURCES, source_display_name
from tests.conftest import BASE_DATE, make_story

BOGOTA = timezone(timedelta(hours=-5))

# ---------------------------------------------------------------------------
# Nombre para mostrar
# ---------------------------------------------------------------------------


def test_every_source_has_a_display_name():
    names = {slug: conf.get("name", "") for slug, conf in SOURCES.items()}
    missing = [slug for slug, name in names.items() if not name.strip()]
    assert missing == []
    # Dos medios con el mismo nombre serían indistinguibles en la UI
    assert len(set(names.values())) == len(names)


def test_source_display_name_falls_back_to_slug():
    assert source_display_name("lasillavacia") == "La Silla Vacía"
    assert source_display_name("fuente-retirada") == "fuente-retirada"


def test_api_exposes_source_display_name(client, db_session):
    story = make_story(db_session, 1, source_name="lasillavacia", extra_sources=1)

    item = client.get("/api/v1/stories").json()["items"][0]
    assert item["source_name"] == "lasillavacia"
    assert item["source_display_name"] == "La Silla Vacía"

    sources = client.get(f"/api/v1/stories/{story.slug}").json()["sources"]
    by_slug = {s["source_name"]: s["source_display_name"] for s in sources}
    # "medio-extra0" no está en el catálogo: viaja el slug
    assert by_slug == {"lasillavacia": "La Silla Vacía", "medio-extra0": "medio-extra0"}


# ---------------------------------------------------------------------------
# Fechas con zona horaria
# ---------------------------------------------------------------------------


def test_api_dates_are_utc_with_zone(client, db_session):
    story = make_story(db_session, 1, synthesized=True, extra_sources=1)

    item = client.get("/api/v1/stories").json()["items"][0]
    # SQLite devuelve la fecha sin zona; la API la sirve en UTC explícito
    assert item["published_at"] == "2026-07-01T13:00:00Z"

    detail = client.get(f"/api/v1/stories/{story.slug}").json()
    assert detail["published_at"] == "2026-07-01T13:00:00Z"
    assert detail["synthesized"]["generated_at"] == "2026-07-01T13:00:00Z"
    assert sorted(s["published_at"] for s in detail["sources"]) == [
        "2026-07-01T13:00:00Z",
        "2026-07-01T14:00:00Z",
    ]
    for value in [detail["published_at"], *(s["published_at"] for s in detail["sources"])]:
        assert datetime.fromisoformat(value).utcoffset() == timedelta(0)


def test_non_utc_offset_keeps_the_instant_in_sqlite(client, db_session):
    """Una hora en -05:00 (metadatos del medio) no se corre 5 horas.

    Sin UTCDateTime, SQLite guardaría la hora de pared (07:21) y la API la
    devolvería como si fuera UTC.
    """
    story = make_story(db_session, 1)
    bogota_time = datetime(2026, 7, 30, 7, 21, tzinfo=BOGOTA)
    story.published_at = bogota_time
    story.articles[0].published_at = bogota_time
    db_session.commit()

    item = client.get("/api/v1/stories").json()["items"][0]
    assert item["published_at"] == "2026-07-30T12:21:00Z"


def test_rows_written_without_zone_are_read_as_utc(db_session):
    """Filas viejas de SQLite (texto sin zona) se leen como UTC con zona."""
    story = make_story(db_session, 1)
    db_session.execute(
        text("UPDATE stories SET published_at = '2026-07-30 12:21:00.000000' WHERE id = :id"),
        {"id": story.id},
    )
    db_session.commit()
    db_session.expire_all()

    reloaded = db_session.get(Story, story.id)
    assert reloaded.published_at == datetime(2026, 7, 30, 12, 21, tzinfo=UTC)
    assert reloaded.published_at.tzinfo is not None


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_utc_datetime_type_normalizes_both_ways(dialect):
    column = UTCDateTime()
    bogota_time = datetime(2026, 7, 30, 7, 21, tzinfo=BOGOTA)
    expected = datetime(2026, 7, 30, 12, 21, tzinfo=UTC)

    # Lo que se guarda: el instante en UTC
    assert column.process_bind_param(bogota_time, dialect) == expected
    assert column.process_bind_param(bogota_time, dialect).utcoffset() == timedelta(0)
    assert column.process_bind_param(None, dialect) is None
    # Lo que se lee: SQLite sin zona, Postgres en la zona de la sesión
    assert column.process_result_value(datetime(2026, 7, 30, 12, 21), dialect) == expected
    read_back = column.process_result_value(bogota_time, dialect)
    assert read_back == expected and read_back.utcoffset() == timedelta(0)
    assert column.process_result_value(None, dialect) is None


@pytest.mark.skipif(
    not os.environ.get("IDEOGRAPHCO_TEST_POSTGRES_URL"),
    reason="IDEOGRAPHCO_TEST_POSTGRES_URL no está definida (Postgres desechable)",
)
def test_postgres_round_trip_in_another_session_timezone():
    """Con la sesión de Postgres en Bogotá, la fecha sale igual en UTC.

    Crea y borra las tablas: usar una base desechable, nunca la de producción.
    """
    engine = create_engine(
        os.environ["IDEOGRAPHCO_TEST_POSTGRES_URL"],
        connect_args={"options": "-c timezone=America/Bogota"},
    )
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        published = BASE_DATE.astimezone(BOGOTA)
        with Session(engine) as session:
            story = Story(
                id="st_pg", slug="pg", title="t", status="published",
                published_at=published, representative_article_id="a_pg",
            )
            story.articles.append(
                Article(
                    id="a_pg", url="https://example.com/pg", source_name="eltiempo",
                    source_category="nacional", title="t", text="x", snippet="x",
                    content_hash="h", published_at=published, scraped_at=published,
                )
            )
            session.add(story)
            session.commit()

        with Session(engine) as session:
            raw = session.execute(text("SELECT published_at FROM stories")).scalar_one()
            assert raw.utcoffset() == timedelta(hours=-5)  # psycopg, zona de la sesión
            reloaded = session.get(Story, "st_pg")
            assert reloaded.published_at == BASE_DATE
            assert reloaded.published_at.utcoffset() == timedelta(0)
            assert reloaded.articles[0].published_at.utcoffset() == timedelta(0)
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
