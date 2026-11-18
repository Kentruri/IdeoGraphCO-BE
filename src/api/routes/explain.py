"""Explicación en vivo para la mesa de redacción (Server-Sent Events).

`POST /api/v1/explain` recibe lo mismo que /classify, clasifica el texto por su
cuenta (nunca explica una clasificación que mande el cliente) y transmite la
explicación mientras `claude -p` la escribe. No guarda nada. El contrato de los
eventos está en CONTRACT.md:

    classification  la clasificación del texto (ClassifyResponseDTO)
    status          {"phase": "thinking" | "writing"}
    delta           {"text": <caracteres nuevos del párrafo>}
    retry           {"reason": <regla rota>}, el borrador se descarta y viene otro intento
    explanation     la salida estructurada final, validada en el servidor
    error           {"code": <código>, "detail": <texto>}, termina sin explicación

Apagada por defecto (DESK_EXPLANATION_MODE=off: 503 antes de abrir el stream).
Atiende una explicación a la vez: cada una es un `claude -p` con la suscripción
del usuario. Si el cliente corta la conexión, FastAPI cancela este generador y
el proceso de `claude` se mata (ver src/explanation/stream.py).
"""

import logging
import threading
from collections.abc import AsyncIterable
from contextlib import aclosing
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.sse import EventSourceResponse, ServerSentEvent

from src.adapters.story_adapter import EXPLANATION_DISCLAIMER
from src.api.deps import require_desk_explanations
from src.api.routes.classify import (
    NOT_POLITICAL_DETAIL,
    get_classifier_client,
    get_domain_filter,
    screen_text,
)
from src.classifier.client import ClassifierClient
from src.classifier.domain_filter import DomainFilter
from src.config import Settings, get_settings
from src.explanation.basis import classes_to_explain
from src.explanation.generator import ExplainerUnavailable
from src.explanation.prompts import ExplanationRequest
from src.explanation.stream import (
    Delta,
    Done,
    Failed,
    Retry,
    Status,
    StreamExplainer,
)
from src.explanation.stream import get_stream_explainer as build_stream_explainer
from src.schemas.dto import (
    ClassifyRequestDTO,
    ClassifyResponseDTO,
    ExplainDeltaDTO,
    ExplainErrorDTO,
    ExplainRetryDTO,
    ExplainStatusDTO,
    FeatureDisabledErrorDTO,
    LiveExplanationDTO,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["classify"])

BUSY_DETAIL = "Ya hay una explicación en curso. Intente de nuevo cuando termine."
CLASSIFIER_UNAVAILABLE_DETAIL = "El clasificador no está disponible en este momento."
FAILED_DETAIL = "No se pudo redactar una explicación que cumpla las reglas."
NO_TITLE = "(sin titular)"

# Una explicación a la vez en todo el proceso. Lock de hilos y no de asyncio
# para no depender del event loop (las pruebas abren uno por cliente).
_busy = threading.Lock()


def get_stream_explainer(settings: Annotated[Settings, Depends(get_settings)]) -> StreamExplainer:
    return build_stream_explainer(settings)


def _event(name: str, data) -> ServerSentEvent:
    return ServerSentEvent(event=name, data=data)


def _error(code: str, detail: str, **extra) -> ServerSentEvent:
    return _event("error", ExplainErrorDTO(code=code, detail=detail, **extra))


@router.post(
    "/explain",
    response_class=EventSourceResponse,
    dependencies=[Depends(require_desk_explanations)],
    responses={503: {"model": FeatureDisabledErrorDTO}},
)
async def explain(
    body: ClassifyRequestDTO,
    classifier: Annotated[ClassifierClient, Depends(get_classifier_client)],
    domain_filter: Annotated[DomainFilter, Depends(get_domain_filter)],
    explainer: Annotated[StreamExplainer, Depends(get_stream_explainer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterable[ServerSentEvent]:
    if not _busy.acquire(blocking=False):
        yield _error("busy", BUSY_DETAIL)
        return
    try:
        async for event in _explain(body, classifier, domain_filter, explainer, settings):
            yield event
    except ExplainerUnavailable as error:
        logger.warning("Explicación en vivo sin proveedor: %s", error)
        yield _error("explainer_unavailable", str(error))
    except Exception:  # noqa: BLE001 (el stream termina con un error legible)
        logger.exception("Explicación en vivo: error inesperado")
        yield _error("explanation_failed", FAILED_DETAIL)
    finally:
        _busy.release()


async def _explain(
    body: ClassifyRequestDTO,
    classifier: ClassifierClient,
    domain_filter: DomainFilter,
    explainer: StreamExplainer,
    settings: Settings,
) -> AsyncIterable[ServerSentEvent]:
    # El filtro y el clasificador son llamadas HTTP bloqueantes: van a un hilo.
    screening = await run_in_threadpool(screen_text, domain_filter, body.text)
    if screening.rejected is not None:
        verdict = screening.rejected
        yield _error(
            "not_political",
            NOT_POLITICAL_DETAIL,
            category=verdict.category,
            reason=verdict.reason,
        )
        return

    result = await run_in_threadpool(classifier.classify, body.title, body.text)
    if result is None:
        yield _error("classifier_unavailable", CLASSIFIER_UNAVAILABLE_DETAIL)
        return
    yield _event(
        "classification",
        ClassifyResponseDTO(
            label=result.label,
            probabilities=result.probabilities,
            model_version=result.model_version,
            political_filter=screening.political_filter,
        ),
    )

    label, second = classes_to_explain(
        result.label, result.probabilities, settings.explanation_second_min_probability
    )
    request = ExplanationRequest(
        title=body.title.strip() or NO_TITLE,
        text=body.text,
        probabilities=dict(result.probabilities),
        label=label,
        second=second,
    )
    async with aclosing(explainer.stream(request)) as events:
        async for event in events:
            if isinstance(event, Status):
                yield _event("status", ExplainStatusDTO(phase=event.phase))
            elif isinstance(event, Delta):
                yield _event("delta", ExplainDeltaDTO(text=event.text))
            elif isinstance(event, Retry):
                yield _event("retry", ExplainRetryDTO(reason=event.reason))
            elif isinstance(event, Done):
                explanation = event.explanation
                yield _event(
                    "explanation",
                    LiveExplanationDTO(
                        text=explanation.text,
                        classes=[label, *([second] if second else [])],
                        support=explanation.support,
                        model=explanation.model,
                        prompt_version=explanation.prompt_version,
                        disclaimer=EXPLANATION_DISCLAIMER,
                    ),
                )
                return
            elif isinstance(event, Failed):
                logger.warning("Explicación en vivo fallida: %s", event.reason)
                yield _error("explanation_failed", FAILED_DETAIL, reason=event.reason)
                return
