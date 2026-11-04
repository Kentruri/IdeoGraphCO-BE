"""Filtro LLM de politicidad (portado de IdeoGraphCO, mismo comportamiento).

Clasifica el texto en 4 categorías y solo se conserva "political_article".
Primario barato (flash-lite) con escalado automático al modelo mayor cuando
la confianza es baja. JSON garantizado vía response_schema.
"""

import json
import logging
import time

from src.ingestion.prompts import FILTER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

FILTER_CATEGORIES: list[str] = [
    "political_article",
    "nonpolitical_article",
    "biography_static",
    "garbage",
]

_FILTER_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": FILTER_CATEGORIES},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason": {"type": "string"},
    },
    "required": ["category", "confidence", "reason"],
}


def parse_filter_response(response_text: str) -> dict | None:
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.warning("Filter response no parseable (%s): %s", e, response_text[:400])
        return None


def call_filter_with_retry(client, model: str, text: str, max_retries: int = 3) -> str | None:
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model,
                contents=text,
                config={
                    "system_instruction": FILTER_SYSTEM_PROMPT,
                    "response_mime_type": "application/json",
                    "response_schema": _FILTER_RESPONSE_SCHEMA,
                    "thinking_config": {"thinking_budget": 0},
                    "max_output_tokens": 512,
                    "temperature": 0.0,
                },
            )
            return response.text
        except Exception as e:
            if attempt == max_retries - 1:
                # Último intento: no dormir para nada, reportar el fallo ya.
                logger.warning("Filtro agotó %d intentos: %s", max_retries, str(e)[:100])
                break
            error_msg = str(e)
            if "429" in error_msg or "quota" in error_msg.lower():
                wait = 60
                logger.warning("Rate limit del filtro. Esperando %ds...", wait)
            else:
                wait = 2 ** (attempt + 1)
                logger.warning(
                    "Error filter intento %d/%d: %s. Esperando %ds...",
                    attempt + 1,
                    max_retries,
                    error_msg[:100],
                    wait,
                )
            time.sleep(wait)
    return None


def is_political_article(
    client,
    text: str,
    model: str = "gemini-2.5-flash-lite",
    max_chars: int = 3000,
    escalate_model: str | None = "gemini-2.5-flash",
    escalate_threshold: float = 0.7,
    max_retries: int = 3,
) -> tuple[bool, dict | None]:
    """(es_político, info). Si la confianza del primario es baja, escala.

    `max_retries` es por modelo. La ingesta usa 3 (puede esperar hasta 60 s
    por un 429); la mesa de redacción usa 1 para no dejar colgada la petición.
    """
    truncated = text[:max_chars]
    response = call_filter_with_retry(client, model, truncated, max_retries=max_retries)
    if response is None:
        return False, None

    data = parse_filter_response(response)
    if data is None:
        return False, None

    data["escalated"] = False
    conf = data.get("confidence")
    if escalate_model and isinstance(conf, int | float) and conf < escalate_threshold:
        primary_conf = conf
        response_esc = call_filter_with_retry(
            client, escalate_model, truncated, max_retries=max_retries
        )
        if response_esc is not None:
            data_esc = parse_filter_response(response_esc)
            if data_esc is not None:
                data = data_esc
                data["escalated"] = True
                data["primary_confidence"] = primary_conf

    return data.get("category") == "political_article", data
