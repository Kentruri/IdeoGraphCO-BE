"""Redacción de la síntesis triangulada con Gemini (Fase 3).

Una llamada por story con salida JSON garantizada (response_schema). La
respuesta se valida antes de guardarla: una síntesis vacía, demasiado
corta o con rayas no se publica y la story sigue con fuente única visible.
"""

import json
import logging
import re
import time
from dataclasses import dataclass

from src.triangulation.prompts import SYNTHESIS_SYSTEM_PROMPT, synthesis_user_prompt

logger = logging.getLogger(__name__)

# Texto de cada fuente que entra al prompt (caracteres). Con 5 fuentes son
# ~35.000 caracteres: cabe de sobra en el contexto y acota el costo.
SOURCE_TEXT_CHARS = 7000
MIN_WORDS = 250
MAX_TITLE_CHARS = 140

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "same_event_sources": {"type": "array", "items": {"type": "integer"}},
        "title": {"type": "string"},
        "paragraphs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["same_event_sources", "title", "paragraphs"],
}

_DASHES = re.compile(r"\s*[—–]\s*")


@dataclass
class Synthesis:
    title: str
    paragraphs: list[str]
    model: str
    # Índices (desde 0) de las fuentes que el modelo confirmó como el mismo hecho.
    used_sources: list[int]


def _clean(text: str) -> str:
    # La regla de estilo del proyecto: sin rayas. Si el modelo las cuela,
    # se cambian por comas en vez de descartar toda la síntesis.
    return _DASHES.sub(", ", " ".join(text.split())).strip(" ,")


def parse_synthesis(
    response_text: str | None, model: str, source_count: int, min_sources: int
) -> Synthesis | None:
    if not response_text:
        return None
    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        logger.warning("Síntesis no parseable: %s", response_text[:300])
        return None
    used = sorted(
        {
            number - 1
            for number in data.get("same_event_sources", [])
            if isinstance(number, int) and 1 <= number <= source_count
        }
    )
    if len(used) < min_sources:
        # El modelo no reconoce suficientes coberturas del mismo hecho: el
        # grupo estaba mal armado y no hay triangulación que publicar.
        logger.warning("Síntesis descartada: solo %d fuentes del mismo hecho", len(used))
        return None
    title = _clean(str(data.get("title", "")))
    paragraphs = [_clean(str(p)) for p in data.get("paragraphs", []) if str(p).strip()]
    words = sum(len(paragraph.split()) for paragraph in paragraphs)
    if not title or len(title) > MAX_TITLE_CHARS or len(paragraphs) < 3 or words < MIN_WORDS:
        logger.warning(
            "Síntesis descartada (titular %d car., %d párrafos, %d palabras)",
            len(title),
            len(paragraphs),
            words,
        )
        return None
    return Synthesis(title=title, paragraphs=paragraphs, model=model, used_sources=used)


def synthesize(
    client,
    model: str,
    sources: list[dict[str, str]],
    min_sources: int = 3,
    max_retries: int = 3,
) -> Synthesis | None:
    """`sources`: dicts con outlet, date, title y text (texto completo)."""
    prepared = [{**source, "text": source["text"][:SOURCE_TEXT_CHARS]} for source in sources]
    prompt = synthesis_user_prompt(prepared)
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config={
                    "system_instruction": SYNTHESIS_SYSTEM_PROMPT,
                    "response_mime_type": "application/json",
                    "response_schema": _RESPONSE_SCHEMA,
                    "temperature": 0.2,
                    "max_output_tokens": 8192,
                },
            )
            return parse_synthesis(response.text, model, len(sources), min_sources)
        except Exception as error:  # noqa: BLE001 — cualquier fallo del proveedor
            message = str(error)
            if attempt == max_retries - 1:
                logger.warning("Síntesis agotó %d intentos: %s", max_retries, message[:120])
                break
            wait = 60 if ("429" in message or "quota" in message.lower()) else 2 ** (attempt + 1)
            logger.warning("Error de síntesis (%s). Reintento en %ds", message[:80], wait)
            time.sleep(wait)
    return None
