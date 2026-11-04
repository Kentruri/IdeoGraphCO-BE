"""Clasificación a demanda para la mesa de redacción del prototipo (OE4).

No guarda nada: recibe titular y texto, lo pasa por el mismo cliente del
clasificador que usa la ingesta (`CLASSIFIER_MODE`) y devuelve la
clasificación. Con `http` responde el modelo servido en `inference/`; con
`mock`, la distribución determinista de desarrollo.

Filtro de dominio opcional (ver `src/classifier/domain_filter.py`):

- Sin GEMINI_API_KEY (lo normal hoy) no hay filtro: se confía en el usuario
  y se clasifica todo texto que cumpla el largo (`political_filter: "disabled"`).
- Con clave, el texto pasa antes por el filtro de politicidad de la ingesta;
  si no es una noticia política colombiana, 422 `not_political` sin clasificar
  (`"passed"` si la aceptó).
- Con clave pero el filtro falla, se clasifica igual (`"unavailable"`) y se
  avisa en el log.
"""

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from src.classifier.client import ClassifierClient, get_classifier
from src.classifier.domain_filter import DomainFilter, DomainVerdict, build_domain_filter
from src.config import get_settings
from src.schemas.dto import ClassifyRequestDTO, ClassifyResponseDTO, NotPoliticalErrorDTO

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["classify"])

NOT_POLITICAL_DETAIL = (
    "El texto no parece una noticia política colombiana; el modelo solo "
    "se entrenó con ese tipo de noticias."
)


def get_classifier_client() -> ClassifierClient:
    return get_classifier(get_settings())


@lru_cache
def get_domain_filter() -> DomainFilter:
    # Un solo cliente de Gemini para todo el proceso.
    return build_domain_filter(get_settings())


@router.post(
    "/classify",
    response_model=ClassifyResponseDTO,
    responses={422: {"model": NotPoliticalErrorDTO}},
)
def classify(
    body: ClassifyRequestDTO,
    classifier: Annotated[ClassifierClient, Depends(get_classifier_client)],
    domain_filter: Annotated[DomainFilter, Depends(get_domain_filter)],
):
    political_filter = "disabled"  # sin clave: ni se llama al filtro
    if domain_filter.enabled:
        # Solo el cuerpo, como en la ingesta: el corpus pasó el filtro así.
        verdict = domain_filter.check(body.text)
        if verdict is None:
            logger.warning(
                "Mesa de redacción: el filtro de politicidad falló, se clasifica sin él"
            )
            political_filter = "unavailable"
        elif not verdict.is_political:
            return _not_political_response(verdict)
        else:
            political_filter = "passed"

    result = classifier.classify(body.title, body.text)
    if result is None:
        # Servicio caído o modo `off`: el FE muestra el error en vez de
        # inventar un veredicto.
        raise HTTPException(
            status_code=503,
            detail="El clasificador no está disponible en este momento.",
        )
    return ClassifyResponseDTO(
        label=result.label,
        probabilities=result.probabilities,
        model_version=result.model_version,
        political_filter=political_filter,
    )


def _not_political_response(verdict: DomainVerdict) -> JSONResponse:
    logger.info(
        "Mesa de redacción: texto rechazado por el filtro (%s, %.2f): %s",
        verdict.category,
        verdict.confidence or 0.0,
        verdict.reason,
    )
    error = NotPoliticalErrorDTO(
        detail=NOT_POLITICAL_DETAIL,
        category=verdict.category,
        confidence=verdict.confidence,
        reason=verdict.reason,
    )
    return JSONResponse(status_code=422, content=error.model_dump())
