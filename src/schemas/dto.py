"""DTOs del contrato v2 con el frontend (ver CONTRACT.md).

snake_case intencional: es el formato que viaja por la API.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, model_validator

from src.core.dates import as_utc
from src.core.schema import IDEOLOGY_CLASSES

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


class ExplanationDTO(BaseModel):
    """Por qué la noticia cae en su clase predicha (y en la segunda, si pesa).

    La redacta un LLM a partir del texto del representativo, la distribución
    del clasificador y el codebook de las clases. Describe elementos de
    encuadre del texto; no es el razonamiento interno del clasificador, y por
    eso se muestra siempre con `disclaimer`.
    """

    text: str
    # Clases que explica: la predicha y, si su probabilidad llega al umbral
    # (20 %), la segunda.
    classes: list[str]
    model: str
    # Versión de los prompts con que se escribió; null en las anteriores a que
    # se guardara (src/explanation/prompts.py, EXPLANATION_PROMPT_VERSION).
    prompt_version: str | None = None
    generated_at: UtcDatetime
    disclaimer: str


class StoryDetailDTO(StoryListItemDTO):
    keywords: list[str]
    # Explicación de `classification` (la del representativo); null si aún no
    # se generó o si el artículo se reclasificó después.
    explanation: ExplanationDTO | None = None
    synthesized: SynthesizedDTO | None
    sources: list[SourceArticleDTO]


class PaginatedStoriesDTO(BaseModel):
    items: list[StoryListItemDTO]
    page: int
    page_size: int
    total_items: int
    total_pages: int


# ---------------------------------------------------------------------------
# Explicación en vivo de la mesa de redacción (POST /api/v1/explain, SSE)
# ---------------------------------------------------------------------------

SupportLevel = Literal["alto", "medio", "bajo"]


class FeatureDisabledErrorDTO(BaseModel):
    """503 de /explain y /explanation-reviews mientras DESK_EXPLANATION_MODE=off."""

    detail: str
    code: Literal["explanations_disabled"] = "explanations_disabled"


class ExplainStatusDTO(BaseModel):
    """Evento `status`: el LLM empezó a razonar o a escribir la respuesta."""

    phase: Literal["thinking", "writing"]


class ExplainDeltaDTO(BaseModel):
    """Evento `delta`: caracteres nuevos del párrafo, en el orden en que llegan."""

    text: str


class ExplainRetryDTO(BaseModel):
    """Evento `retry`: el borrador rompió una regla y se descarta; viene otro intento."""

    reason: str


class LiveExplanationDTO(BaseModel):
    """Evento `explanation`: la salida estructurada final, ya validada en el servidor."""

    text: str
    classes: list[str]
    # Cuánto apoya el texto cada clase de `classes`, según el LLM que redactó la
    # explicación. Es su lectura, no una medida de acierto del clasificador.
    support: dict[str, SupportLevel]
    model: str
    prompt_version: str
    disclaimer: str


class ExplainErrorDTO(BaseModel):
    """Evento `error`: termina el stream sin explicación."""

    code: Literal[
        "busy",
        "not_political",
        "classifier_unavailable",
        "explainer_unavailable",
        "explanation_failed",
    ]
    detail: str
    # not_political: categoría y motivo del filtro de politicidad.
    # explanation_failed: qué falló (la regla rota, el código de salida o el tiempo).
    category: str | None = None
    reason: str | None = None


# ---------------------------------------------------------------------------
# Ficha de revisión de una explicación en vivo (POST /api/v1/explanation-reviews)
# ---------------------------------------------------------------------------

REVIEW_TEXT_MAX_CHARS = 2_000
REVIEW_NOTES_MAX_CHARS = 500
PROBABILITY_SUM_TOLERANCE = 0.01


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class ReviewedExplanationDTO(BaseModel):
    """La explicación tal como llegó a la mesa (evento `explanation`)."""

    text: str = Field(min_length=1, max_length=REVIEW_TEXT_MAX_CHARS)
    classes: list[str] = Field(min_length=1, max_length=2)
    support: dict[str, SupportLevel]
    model: str = Field(min_length=1, max_length=80)
    prompt_version: str = Field(min_length=1, max_length=40)


class ReviewJudgementDTO(BaseModel):
    """Lo que decide quien revisa. `support` y `revised_text` llegan prellenados."""

    support: dict[str, SupportLevel]
    revised_text: Annotated[str | None, AfterValidator(_blank_to_none)] = Field(
        None, max_length=REVIEW_TEXT_MAX_CHARS
    )
    fits_text: Literal["si", "en_parte", "no"]
    neutral: bool
    notes: Annotated[str | None, AfterValidator(_blank_to_none)] = Field(
        None, max_length=REVIEW_NOTES_MAX_CHARS
    )


class ExplanationReviewRequestDTO(BaseModel):
    title: str = Field("", max_length=300)
    text: str = Field(min_length=CLASSIFY_MIN_TEXT_CHARS, max_length=CLASSIFY_MAX_TEXT_CHARS)
    classification: ClassificationDTO
    explanation: ReviewedExplanationDTO
    review: ReviewJudgementDTO

    @model_validator(mode="after")
    def _consistent(self) -> "ExplanationReviewRequestDTO":
        classification = self.classification
        if set(classification.probabilities) != set(IDEOLOGY_CLASSES):
            raise ValueError("probabilities debe traer exactamente las 8 clases")
        if any(not 0.0 <= value <= 1.0 for value in classification.probabilities.values()):
            raise ValueError("cada probabilidad debe estar entre 0 y 1")
        if abs(sum(classification.probabilities.values()) - 1.0) > PROBABILITY_SUM_TOLERANCE:
            raise ValueError("las probabilidades deben sumar 1")
        if classification.label not in IDEOLOGY_CLASSES:
            raise ValueError(f"clase desconocida: {classification.label}")

        classes = self.explanation.classes
        if any(name not in IDEOLOGY_CLASSES for name in classes):
            raise ValueError("explanation.classes trae una clase desconocida")
        if len(set(classes)) != len(classes):
            raise ValueError("explanation.classes repite una clase")
        if classes[0] != classification.label:
            raise ValueError("la primera clase explicada debe ser la clase predicha")
        if set(self.explanation.support) != set(classes):
            raise ValueError("explanation.support debe calificar cada clase explicada")
        if set(self.review.support) != set(classes):
            raise ValueError("review.support debe calificar cada clase explicada")
        return self


class ExplanationReviewCreatedDTO(BaseModel):
    id: str
    created_at: UtcDatetime
