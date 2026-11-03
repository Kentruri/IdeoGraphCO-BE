from src.api.app import app
from src.api.routes.classify import get_classifier_client
from src.classifier.client import NullClassifierClient
from src.core.schema import IDEOLOGY_CLASSES

TEXTO = "El Congreso aprobó en último debate la reforma pensional tras meses de discusión."


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
