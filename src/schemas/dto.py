"""DTOs del contrato v2 con el frontend (ver CONTRACT.md).

snake_case intencional: es el formato que viaja por la API.
"""

from datetime import datetime

from pydantic import BaseModel, Field

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


class StoryListItemDTO(BaseModel):
    id: str
    slug: str
    title: str
    subtitle: str | None
    source_name: str
    source_category: str
    author: str | None
    published_at: datetime
    image_url: str | None
    reading_time_minutes: int
    classification: ClassificationDTO
    sources_count: int
    has_synthesized: bool


class SourceArticleDTO(BaseModel):
    id: str
    source_name: str
    source_category: str
    title: str
    snippet: str
    url: str
    author: str | None
    published_at: datetime
    classification: ClassificationDTO | None


class SynthesizedDTO(BaseModel):
    title: str
    body: list[str]
    model: str
    generated_at: datetime
    disclaimer: str


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
