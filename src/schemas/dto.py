"""DTOs del contrato v2 con el frontend (ver CONTRACT.md).

snake_case intencional: es el formato que viaja por la API.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from src.core.dates import as_utc

# Fechas del contrato: siempre ISO 8601 en UTC con zona ("…Z"). SQLite
# devuelve las fechas sin zona y Postgres en la zona de la sesión; aquí se
# normalizan para que el FE no las lea como hora local.
UtcDatetime = Annotated[datetime, AfterValidator(as_utc)]

# Tope del texto a clasificar: ~30.000 caracteres equivalen a los 6.270
# tokens que el modelo alcanza a leer con 16 ventanas; lo que pase de ahí
# el troceo lo descartaría de todos modos.
CLASSIFY_MAX_TEXT_CHARS = 30_000
CLASSIFY_MIN_TEXT_CHARS = 40


class ClassificationDTO(BaseModel):
    label: str
    probabilities: dict[str, float]
    model_version: str


class ClassifyRequestDTO(BaseModel):
    title: str = Field("", max_length=300)
    text: str = Field(min_length=CLASSIFY_MIN_TEXT_CHARS, max_length=CLASSIFY_MAX_TEXT_CHARS)


class ClassifyResponseDTO(ClassificationDTO):
    # Filtro de politicidad (opcional):
    # "disabled": sin GEMINI_API_KEY no hay filtro; se confía en el usuario.
    # "passed": el filtro confirmó que es una noticia política colombiana.
    # "unavailable": hay clave pero el filtro falló; se clasificó sin él.
    political_filter: Literal["disabled", "passed", "unavailable"]


class NotPoliticalErrorDTO(BaseModel):
    """422 de /classify cuando el filtro dice que el texto no es del dominio."""

    detail: str
    code: Literal["not_political"] = "not_political"
    # nonpolitical_article | biography_static | garbage
    category: str
    confidence: float | None
    reason: str


class StoryListItemDTO(BaseModel):
    id: str
    slug: str
    title: str
    subtitle: str | None
    source_name: str  # slug del catálogo (src/ingestion/sources.py)
    source_display_name: str  # nombre legible del medio
    source_category: str
    author: str | None
    published_at: UtcDatetime
    image_url: str | None
    reading_time_minutes: int
    classification: ClassificationDTO
    sources_count: int
    has_synthesized: bool


class SourceArticleDTO(BaseModel):
    id: str
    source_name: str
    source_display_name: str
    source_category: str
    title: str
    snippet: str
    url: str
    author: str | None
    published_at: UtcDatetime
    classification: ClassificationDTO | None


class SynthesizedDTO(BaseModel):
    """Noticia sintética por triangulación (≥3 medios del mismo hecho)."""

    title: str
    body: list[str]
    model: str
    generated_at: UtcDatetime
    disclaimer: str
    # Encuadre de la síntesis según el mismo clasificador; null si el
    # servicio no estaba disponible al generarla.
    classification: ClassificationDTO | None = None


class StoryDetailDTO(StoryListItemDTO):
    keywords: list[str]
    synthesized: SynthesizedDTO | None
    sources: list[SourceArticleDTO]


class PaginatedStoriesDTO(BaseModel):
    items: list[StoryListItemDTO]
    page: int
    page_size: int
    total_items: int
    total_pages: int
