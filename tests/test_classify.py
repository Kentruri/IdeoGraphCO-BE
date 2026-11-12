import pytest

from src.api.app import app
from src.api.routes.classify import get_classifier_client, get_domain_filter
from src.classifier import domain_filter as domain_filter_module
from src.classifier.client import MockClassifierClient, NullClassifierClient
from src.classifier.domain_filter import DisabledDomainFilter, GeminiDomainFilter
from src.core.schema import IDEOLOGY_CLASSES
from src.ingestion import political_filter
from tests.fake_gemini import fake_gemini_client, filter_json

TEXTO = "El Congreso aprobó en último debate la reforma pensional tras meses de discusión."
RECETA = "Mezcle la harina con los huevos, agregue la leche y hornee a 180 grados por 40 minutos."


class SpyClassifier(MockClassifierClient):
    def __init__(self):
        self.calls = 0

    def classify(self, title, text):
        self.calls += 1
        return super().classify(title, text)


@pytest.fixture()
def with_gemini_key(monkeypatch):
    """Como si hubiera GEMINI_API_KEY: el filtro real de la mesa con Gemini simulado.

    Devuelve una función que recibe las respuestas de Gemini, instala el filtro
    en la ruta y devuelve el cliente simulado (para ver qué recibió).
    """

    def _no_sleep(seconds):
        raise AssertionError(f"el filtro de la mesa durmió {seconds}s")

    monkeypatch.setattr(political_filter.time, "sleep", _no_sleep)

    def _install(*responses):
        client = fake_gemini_client(*responses)
        gemini_filter = GeminiDomainFilter(
            client,
            model="gemini-2.5-flash-lite",
            escalate_model="gemini-2.5-flash",
            escalate_threshold=0.7,
        )
        app.dependency_overrides[get_domain_filter] = lambda: gemini_filter
        return client

    yield _install
    app.dependency_overrides.pop(get_domain_filter, None)


@pytest.fixture()
def spy_classifier():
    spy = SpyClassifier()
    app.dependency_overrides[get_classifier_client] = lambda: spy
    yield spy
    app.dependency_overrides.pop(get_classifier_client, None)


def test_classify_returns_a_classification(client):
    response = client.post("/api/v1/classify", json={"title": "Reforma aprobada", "text": TEXTO})
    assert response.status_code == 200
    data = response.json()
    assert data["label"] in IDEOLOGY_CLASSES
    assert set(data["probabilities"]) == set(IDEOLOGY_CLASSES)
    assert abs(sum(data["probabilities"].values()) - 1.0) < 0.001
    assert data["model_version"] == "mock-0.1.0"  # conftest fija CLASSIFIER_MODE=mock


def test_title_is_optional(client):
    assert client.post("/api/v1/classify", json={"text": TEXTO}).status_code == 200


def test_too_short_or_too_long_text_is_rejected(client):
    assert client.post("/api/v1/classify", json={"text": "muy corto"}).status_code == 422
    assert client.post("/api/v1/classify", json={"text": "a" * 30_001}).status_code == 422


def test_unavailable_classifier_answers_503(client):
    app.dependency_overrides[get_classifier_client] = NullClassifierClient
    try:
        response = client.post("/api/v1/classify", json={"text": TEXTO})
    finally:
        app.dependency_overrides.pop(get_classifier_client, None)
    assert response.status_code == 503
    assert "detail" in response.json()


def test_classify_does_not_persist_anything(client):
    client.post("/api/v1/classify", json={"text": TEXTO})
    assert client.get("/api/v1/stories").json()["total_items"] == 0


# --- Filtro de dominio (opcional) ------------------------------------------
# Caso 1: sin clave. Caso 2: con clave y texto no político. Caso 3: con clave
# y el filtro caído.


def test_without_key_there_is_no_filter_and_the_user_is_trusted(
    client, spy_classifier, monkeypatch
):
    # conftest deja GEMINI_API_KEY vacía: la ruta usa su filtro real, desactivado.
    assert isinstance(get_domain_filter(), DisabledDomainFilter)

    def _must_not_run(*args, **kwargs):
        raise AssertionError("sin clave no se llama al filtro")

    monkeypatch.setattr(domain_filter_module, "is_political_article", _must_not_run)
    response = client.post("/api/v1/classify", json={"text": RECETA})
    assert response.status_code == 200
    assert response.json()["political_filter"] == "disabled"
    assert spy_classifier.calls == 1


def test_with_key_a_political_text_passes_and_is_classified(
    client, with_gemini_key, spy_classifier
):
    gemini = with_gemini_key(filter_json("political_article", 0.93, "Reforma en el Congreso"))
    response = client.post("/api/v1/classify", json={"title": "Reforma", "text": TEXTO})
    assert response.status_code == 200
    data = response.json()
    assert data["political_filter"] == "passed"
    assert data["label"] in IDEOLOGY_CLASSES
    assert spy_classifier.calls == 1
    # Al filtro va solo el cuerpo, como en la ingesta.
    assert [call["contents"] for call in gemini.models.calls] == [TEXTO]


@pytest.mark.parametrize("category", ["nonpolitical_article", "biography_static", "garbage"])
def test_with_key_a_non_political_text_is_rejected_without_classifying(
    client, with_gemini_key, spy_classifier, category
):
    with_gemini_key(filter_json(category, 0.91, "Receta de cocina"))
    response = client.post("/api/v1/classify", json={"text": RECETA})
    assert response.status_code == 422
    data = response.json()
    assert data["code"] == "not_political"
    assert data["category"] == category
    assert data["confidence"] == 0.91
    assert data["reason"] == "Receta de cocina"
    assert isinstance(data["detail"], str) and data["detail"]
    assert spy_classifier.calls == 0


def test_with_key_a_doubtful_verdict_escalates_before_rejecting(client, with_gemini_key):
    gemini = with_gemini_key(
        filter_json("political_article", 0.55),
        filter_json("nonpolitical_article", 0.88, "Resultado deportivo"),
    )
    response = client.post("/api/v1/classify", json={"text": RECETA})
    assert response.status_code == 422
    assert response.json()["category"] == "nonpolitical_article"
    assert [c["model"] for c in gemini.models.calls] == [
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
    ]


@pytest.mark.parametrize(
    "gemini_response",
    [
        RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded"),
        RuntimeError("503 UNAVAILABLE"),
        None,  # respuesta bloqueada, sin texto
        "no es json",
    ],
    ids=["cuota", "caido", "bloqueada", "ilegible"],
)
def test_with_key_a_failing_filter_does_not_block_the_desk(
    client, with_gemini_key, spy_classifier, gemini_response
):
    gemini = with_gemini_key(gemini_response)
    response = client.post("/api/v1/classify", json={"text": TEXTO})
    assert response.status_code == 200
    assert response.json()["political_filter"] == "unavailable"
    assert spy_classifier.calls == 1
    assert len(gemini.models.calls) == 1  # un solo intento, sin esperas


def test_length_validation_runs_before_the_filter(client, with_gemini_key):
    gemini = with_gemini_key(filter_json("political_article", 0.9))
    response = client.post("/api/v1/classify", json={"text": "muy corto"})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)  # validación estándar de FastAPI
    assert gemini.models.calls == []


def test_rejection_wins_over_an_unavailable_classifier(client, with_gemini_key):
    with_gemini_key(filter_json("nonpolitical_article", 0.95))
    app.dependency_overrides[get_classifier_client] = NullClassifierClient
    try:
        response = client.post("/api/v1/classify", json={"text": RECETA})
    finally:
        app.dependency_overrides.pop(get_classifier_client, None)
    assert response.status_code == 422
    assert response.json()["code"] == "not_political"
