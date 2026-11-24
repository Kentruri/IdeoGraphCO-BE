"""Modelo de datos: stories (la unidad navegable) y articles (fuentes reales).

Decisión de diseño (ver CONTRACT.md): dos tablas relacionadas, NO un registro
monolítico. Cada artículo real conserva su propia clasificación ideológica;
la story agrupa 1..5 artículos de la misma historia y guarda la síntesis
(nullable, Fase 3).
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.schema import (
    ARTICLE_CLASSIFICATION_PENDING,
    STORY_STATUS_INGESTED,
)
from src.db.session import Base
from src.db.types import UTCDateTime


def utcnow() -> datetime:
    return datetime.now(UTC)


class Story(Base):
    __tablename__ = "stories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=STORY_STATUS_INGESTED, index=True)

    # Fecha de la historia = published_at del artículo representativo
    # (el primero en reportar). Denormalizada para ordenar el listado.
    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)

    representative_article_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Síntesis triangulada (Fase 3, src/triangulation): nullable hasta que
    # exista y solo con ≥3 medios del mismo hecho
    synthesized_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    synthesized_body: Mapped[list | None] = mapped_column(JSON, nullable=True)  # list[str]
    synthesized_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    synthesized_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Clasificación de la síntesis con el mismo modelo que la original
    # ({label, probabilities, model_version}), para comparar encuadres.
    synthesized_classification: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow
    )

    articles: Mapped[list["Article"]] = relationship(
        back_populates="story", order_by="Article.published_at"
    )


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        Index("ix_articles_story_published", "story_id", "published_at"),
    )

    # ID determinista desde la URL (mismo criterio que el pipeline ML)
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id"), index=True)

    url: Mapped[str] = mapped_column(String(1000), unique=True, index=True)
    source_name: Mapped[str] = mapped_column(String(80), index=True)
    source_category: Mapped[str] = mapped_column(String(20), index=True)

    title: Mapped[str] = mapped_column(Text)
    subtitle: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    language: Mapped[str] = mapped_column(String(8), default="es")

    # Texto normalizado (minúsculas, sin tildes) para búsqueda consistente
    # entre SQLite y Postgres. Se construye en la ingesta.
    search_text: Mapped[str] = mapped_column(Text, default="", index=True)

    # Texto completo: SOLO uso interno (clasificación/síntesis).
    # La API pública expone únicamente `snippet`.
    text: Mapped[str] = mapped_column(Text)
    snippet: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(32), index=True)
    reading_time_minutes: Mapped[int] = mapped_column(default=1)
    image_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    scraped_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    # Clasificación ideológica propia (servicio externo IdeoGraphCO)
    classification_status: Mapped[str] = mapped_column(
        String(20), default=ARTICLE_CLASSIFICATION_PENDING, index=True
    )
    classification_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    classification_probabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    classification_model_version: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Explicación de la clasificación en lenguaje natural (src/explanation). La
    # redacta un LLM a partir del texto, la distribución y el codebook; no es el
    # razonamiento del clasificador. Se escribe una vez por clasificación:
    # `explanation_basis` guarda cuál ({label, second, model_version}) y, si el
    # artículo se reclasifica, la API deja de servirla hasta que se rehaga.
    explanation_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation_basis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Cuánto apoya el texto cada clase explicada, según el LLM ({clase: alto|medio|bajo}).
    explanation_support: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    explanation_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    explanation_generated_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    story: Mapped[Story] = relationship(back_populates="articles")


class SkippedUrl(Base):
    """URLs ya evaluadas y descartadas (no políticas, duplicados de contenido).

    Evita re-descargar y re-pasar por el filtro LLM las mismas candidatas en
    cada corrida del cron — sin esto, cada corrida re-paga el costo del filtro
    por artículos ya rechazados.
    """

    __tablename__ = "skipped_urls"

    url: Mapped[str] = mapped_column(String(1000), primary_key=True)
    reason: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
