"""Filtro de dominio de la mesa: el filtro de la ingesta, sin esperas."""

import pytest

from src.classifier.domain_filter import (
    DisabledDomainFilter,
    GeminiDomainFilter,
    build_domain_filter,
)
from src.config import Settings
from src.ingestion import political_filter
from tests.fake_gemini import fake_gemini_client as _client
from tests.fake_gemini import filter_json as _json

TEXTO = "El Congreso aprobó en último debate la reforma pensional tras meses de discusión."


def _filter(client) -> GeminiDomainFilter:
    return GeminiDomainFilter(
        client, model="lite", escalate_model="flash", escalate_threshold=0.7
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """La mesa no puede dormir: si el filtro intenta esperar, la prueba falla."""

    def _fail(seconds):
        raise AssertionError(f"el filtro de la mesa durmió {seconds}s")

    monkeypatch.setattr(political_filter.time, "sleep", _fail)


def test_political_verdict():
    client = _client(_json("political_article", 0.93, "Reforma en el Congreso"))
    verdict = _filter(client).check(TEXTO)
    assert verdict is not None and verdict.is_political
    assert verdict.category == "political_article"
    assert verdict.confidence == 0.93
    assert verdict.reason == "Reforma en el Congreso"
    assert verdict.escalated is False
    # Mismo prompt y mismo input que la ingesta.
    call = client.models.calls[0]
    assert call["model"] == "lite"
    assert call["contents"] == TEXTO
    assert call["config"]["system_instruction"] == political_filter.FILTER_SYSTEM_PROMPT


def test_non_political_verdict():
    verdict = _filter(_client(_json("nonpolitical_article", 0.97))).check("receta " * 20)
    assert verdict is not None and not verdict.is_political
    assert verdict.category == "nonpolitical_article"


def test_low_confidence_escalates_to_the_larger_model():
    client = _client(
        _json("political_article", 0.55),
        _json("nonpolitical_article", 0.88, "Deportes"),
    )
    verdict = _filter(client).check(TEXTO)
    assert [c["model"] for c in client.models.calls] == ["lite", "flash"]
    assert verdict is not None and verdict.category == "nonpolitical_article"
    assert verdict.escalated is True


def test_text_is_truncated_like_in_ingestion():
    client = _client(_json("political_article", 0.9))
    _filter(client).check("a" * 5000)
    assert len(client.models.calls[0]["contents"]) == 3000


def test_api_error_means_unavailable_after_a_single_attempt():
    client = _client(RuntimeError("429 RESOURCE_EXHAUSTED quota"))
    assert _filter(client).check(TEXTO) is None
    assert len(client.models.calls) == 1


def test_blocked_response_without_text_means_unavailable():
    assert _filter(_client(None)).check(TEXTO) is None


def test_unparseable_or_unknown_category_means_unavailable():
    assert _filter(_client("no es json")).check(TEXTO) is None
    assert _filter(_client(_json("political_foreign", 0.9))).check(TEXTO) is None


def test_failed_escalation_keeps_the_primary_verdict():
    client = _client(_json("political_article", 0.6), RuntimeError("503"))
    verdict = _filter(client).check(TEXTO)
    assert verdict is not None and verdict.is_political
    assert verdict.escalated is False


def test_without_key_the_filter_is_disabled():
    built = build_domain_filter(Settings(gemini_api_key=""))
    assert isinstance(built, DisabledDomainFilter)
    assert built.enabled is False


def test_with_key_builds_the_gemini_filter():
    # Crear el cliente no hace llamadas de red.
    built = build_domain_filter(Settings(gemini_api_key="clave-de-prueba"))
    assert isinstance(built, GeminiDomainFilter)
    assert built.enabled is True


def test_ingestion_keeps_its_three_retries(monkeypatch):
    monkeypatch.setattr(political_filter.time, "sleep", lambda _s: None)
    client = _client(RuntimeError("500"), RuntimeError("500"), _json("political_article", 0.9))
    is_political, info = political_filter.is_political_article(client, TEXTO, escalate_model=None)
    assert is_political and info is not None
    assert len(client.models.calls) == 3
