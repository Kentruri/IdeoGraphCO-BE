"""Filtro de dominio OPCIONAL de la mesa de redacción (POST /api/v1/classify).

El modelo se entrenó solo con noticias políticas colombianas que pasaron el
filtro de politicidad de la ingesta; ante cualquier otro texto igual elige
una de las 8 clases y el resultado no significa nada. Cuando hay
GEMINI_API_KEY, la mesa pasa el texto por ese MISMO filtro
(`src/ingestion/political_filter.py`: mismo prompt, mismos modelos, mismo
escalado y mismo recorte a 3.000 caracteres) antes de clasificarlo.

Tres casos, los mismos que en la ingesta (`src/ingestion/pipeline.py`):

- Sin GEMINI_API_KEY (lo normal hoy): el filtro está DESACTIVADO, no se
  llama a ningún LLM y se confía en el usuario: la mesa clasifica todo
  texto que cumpla el largo. Solo se avisa una vez en el log.
- Con clave y un texto que el filtro no acepta: la mesa no clasifica
  (422 `not_political`).
- Con clave pero el filtro falla (cuota, servicio caído, respuesta
  ilegible): `check` devuelve None y la mesa clasifica igual, sin bloquear.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from src.config import Settings
from src.ingestion.political_filter import FILTER_CATEGORIES, is_political_article

logger = logging.getLogger(__name__)

POLITICAL_CATEGORY = "political_article"


@dataclass(frozen=True)
class DomainVerdict:
    category: str
    confidence: float | None
    reason: str
    escalated: bool

    @property
    def is_political(self) -> bool:
        return self.category == POLITICAL_CATEGORY


class DomainFilter(Protocol):
    # False = no hay filtro configurado y la ruta ni siquiera llama a `check`.
    enabled: bool

    def check(self, text: str) -> DomainVerdict | None:
        """Veredicto del filtro, o None si el filtro falló."""
        ...


class DisabledDomainFilter:
    """Sin GEMINI_API_KEY: no hay filtro y se confía en el usuario."""

    enabled = False

    def check(self, text: str) -> DomainVerdict | None:
        return None


class GeminiDomainFilter:
    """El filtro de la ingesta con un solo intento por modelo.

    La ingesta reintenta hasta 3 veces y espera 60 s ante un 429; dentro de
    una petición HTTP eso dejaría la mesa colgada, así que aquí un fallo
    cuenta de inmediato y la mesa sigue sin filtro.
    """

    enabled = True

    def __init__(
        self,
        client,
        *,
        model: str,
        escalate_model: str | None,
        escalate_threshold: float,
    ):
        self._client = client
        self._model = model
        self._escalate_model = escalate_model
        self._escalate_threshold = escalate_threshold

    def check(self, text: str) -> DomainVerdict | None:
        try:
            _, info = is_political_article(
                self._client,
                text,
                model=self._model,
                escalate_model=self._escalate_model,
                escalate_threshold=self._escalate_threshold,
                max_retries=1,
            )
        except Exception as e:
            # p. ej. una respuesta bloqueada llega con text=None y json.loads
            # lanza TypeError, que el parser de la ingesta no atrapa.
            logger.warning("Filtro de politicidad falló: %s", str(e)[:200])
            return None
        if info is None:
            return None
        category = info.get("category")
        if category not in FILTER_CATEGORIES:
            logger.warning("Filtro de politicidad con categoría inválida: %r", category)
            return None
        confidence = info.get("confidence")
        return DomainVerdict(
            category=category,
            confidence=float(confidence) if isinstance(confidence, int | float) else None,
            reason=str(info.get("reason") or ""),
            escalated=bool(info.get("escalated")),
        )


def build_domain_filter(settings: Settings) -> DomainFilter:
    if not settings.gemini_api_key:
        logger.warning(
            "Sin GEMINI_API_KEY: el filtro de politicidad de la mesa de redacción "
            "se desactiva y se clasifica todo texto (se confía en el usuario)"
        )
        return DisabledDomainFilter()
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(
                timeout=int(settings.classify_filter_timeout_seconds * 1000)
            ),
        )
    except Exception as e:
        logger.error(
            "No se pudo crear el cliente de Gemini; la mesa sigue sin filtro: %s", str(e)[:200]
        )
        return DisabledDomainFilter()
    return GeminiDomainFilter(
        client,
        model=settings.filter_model,
        escalate_model=settings.filter_escalate_model or None,
        escalate_threshold=settings.filter_escalate_threshold,
    )
