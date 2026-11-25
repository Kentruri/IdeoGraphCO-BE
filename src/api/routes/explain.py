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
Atiende una explicación a la vez por proceso (el BE corre con un solo worker):
cada una es un `claude -p` con la suscripción del usuario. El turno lo toma la
dependencia `desk_turn`, que al terminar la respuesta, también si el cliente
cortó la conexión, cierra el stream de la explicación (lo que mata el proceso
de `claude`, ver src/explanation/stream.py) y suelta el turno.
"""

import logging
import threading
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator
from contextlib import aclosing
from typing import Annotated

import anyio
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
# Tope para cerrar el stream de una explicación al terminar la respuesta. Cerrar
# mata `claude -p` y espera a que salga (stream._stop ya acota esa espera).
CLOSE_TIMEOUT_SECONDS = 15.0


class DeskTurn:
    """El turno de la explicación en vivo de una petición y los streams que abrió."""

    def __init__(self, acquired: bool):
        self.acquired = acquired
        self._streams: list[AsyncGenerator] = []

    def track(self, stream: AsyncGenerator) -> AsyncGenerator:
        self._streams.append(stream)
        return stream

    async def close(self) -> None:
        for stream in reversed(self._streams):
            await stream.aclose()


async def desk_turn() -> AsyncIterator[DeskTurn]:
    """Toma el turno de la explicación en vivo y lo suelta al terminar la respuesta.

    FastAPI corre el generador del endpoint en una tarea aparte y, si el cliente
    corta la conexión, cancela esa tarea sin cerrar el generador (PEP 789). Si la
    cancelación llega mientras el generador espera en un `yield`, ni su `finally`
    ni el que mata `claude -p` corren hasta que pase el recolector de basura: el
    proceso seguiría gastando cuota y la mesa quedaría "ocupada". El cierre de
    esta dependencia, en cambio, corre siempre al terminar la respuesta: ahí se
    cierra el stream de la explicación y se suelta el turno.
    """
    turn = DeskTurn(_busy.acquire(blocking=False))
    try:
        yield turn
    finally:
        with anyio.CancelScope(shield=True):
            try:
                with anyio.move_on_after(CLOSE_TIMEOUT_SECONDS) as scope:
                    await turn.close()
                if scope.cancelled_caught:
                    logger.warning(
                        "Explicación en vivo: cerrar el stream superó %.0f s", CLOSE_TIMEOUT_SECONDS
                    )
            except Exception:  # noqa: BLE001 (el turno se suelta igual)
                logger.exception("Explicación en vivo: error al cerrar el stream")
            finally:
                if turn.acquired:
                    _busy.release()


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
    turn: Annotated[DeskTurn, Depends(desk_turn)],
    classifier: Annotated[ClassifierClient, Depends(get_classifier_client)],
    domain_filter: Annotated[DomainFilter, Depends(get_domain_filter)],
    explainer: Annotated[StreamExplainer, Depends(get_stream_explainer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterable[ServerSentEvent]:
    if not turn.acquired:
        yield _error("busy", BUSY_DETAIL)
        return
    # El turno guarda el stream para cerrarlo al final de la respuesta; aclosing
    # lo cierra antes si este generador termina o lo cierran.
    events = turn.track(_explain(body, classifier, domain_filter, explainer, settings))
    try:
        async with aclosing(events):
            async for event in events:
                yield event
    except ExplainerUnavailable as error:
        logger.warning("Explicación en vivo sin proveedor: %s", error)
        yield _error("explainer_unavailable", str(error))
    except Exception:  # noqa: BLE001 (el stream termina con un error legible)
        logger.exception("Explicación en vivo: error inesperado")
        yield _error("explanation_failed", FAILED_DETAIL)


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
