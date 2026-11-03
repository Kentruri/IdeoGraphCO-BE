"""Servicio de inferencia: troceo, composición de la entrada y contrato HTTP.

El modelo real no se carga aquí (el CI no instala PyTorch): el troceo y la
composición son puros, y el endpoint se prueba con un predictor de mentira.
La paridad con el entrenamiento se verifica aparte, corriendo el servicio
sobre la validación del cribado (ver README, «Servicio de inferencia»).
"""

import pytest
from fastapi.testclient import TestClient

from inference import api
from inference.chunking import chunk_token_ids
from inference.model_input import build_model_input, strip_outlet_suffix
from src.core.schema import IDEOLOGY_CLASSES


class TestChunking:
    def test_covers_up_to_token_6270_with_16_windows(self):
        """512/384/16 es el troceo del entrenamiento: la última ventana
        termina en el token 6.270 (capítulo 8.2 de la tesis)."""
        chunks = chunk_token_ids(list(range(10_000)), 512, 384, 16)
        assert len(chunks) == 16
        assert all(len(c) == 510 for c in chunks)
        assert chunks[1][0] == 384
        assert chunks[-1][-1] == 6_269

    def test_short_article_is_one_window(self):
        assert chunk_token_ids(list(range(100)), 512, 384, 16) == [list(range(100))]

    def test_last_window_stops_at_the_end(self):
        chunks = chunk_token_ids(list(range(600)), 512, 384, 16)
        assert len(chunks) == 2
        assert chunks[-1] == list(range(384, 600))

    def test_truncate_keeps_the_first_window(self):
        assert chunk_token_ids(list(range(2_000)), 512, 384, 16, "truncate") == [list(range(510))]

    def test_empty_text_still_yields_a_window(self):
        assert chunk_token_ids([], 512, 384, 16) == [[]]

    def test_unknown_strategy_fails(self):
        with pytest.raises(ValueError):
            chunk_token_ids([1, 2, 3], 512, 384, 16, "otra")


class TestModelInput:
    """Copia de tests/test_model_input.py del repo IdeoGraphCO: si la copia
    de build_model_input se desvía, estos casos lo delatan."""

    def test_strips_real_outlet_suffixes(self):
        casos = [
            ("Exportación de carne creció | CONtexto Ganadero", "Exportación de carne creció"),
            ("Se evaluó Savia Salud - Concejo de Medellín", "Se evaluó Savia Salud"),
            ("Informe de conflicto armado - Indepaz", "Informe de conflicto armado"),
        ]
        for titulo, esperado in casos:
            assert strip_outlet_suffix(titulo) == esperado, titulo

    def test_keeps_legitimate_sentence_fragments(self):
        for titulo in (
            "La reforma pensional - qué sigue ahora",
            "Crisis en el Congreso: el pulso por la reforma",
        ):
            assert strip_outlet_suffix(titulo) == titulo, titulo

    def test_prepends_clean_title(self):
        out = build_model_input("Reforma aprobada | EL DIARIO", "El Congreso votó a favor.")
        assert out == "Reforma aprobada\n\nEl Congreso votó a favor."

    def test_does_not_duplicate_a_title_already_in_the_body(self):
        out = build_model_input(
            "Reforma aprobada | EL DIARIO",
            "Reforma aprobada | EL DIARIO\nEl Congreso votó a favor.",
        )
        assert out == "Reforma aprobada\n\nEl Congreso votó a favor."

    def test_survives_missing_pieces(self):
        assert build_model_input(None, "Solo cuerpo.") == "Solo cuerpo."
        assert build_model_input("Solo titular", "") == "Solo titular"


class _FakePredictor:
    model_version = "fake-1.0"

    def __init__(self):
        self.calls = []

    def classify(self, title, text):
        self.calls.append((title, text))
        probabilities = {cls: 0.05 for cls in IDEOLOGY_CLASSES}
        probabilities["globalismo"] = 0.65
        return {"label": "globalismo", "probabilities": probabilities, "model_version": self.model_version}


@pytest.fixture()
def inference_client():
    fake = _FakePredictor()
    api._state["predictor"] = fake
    with TestClient(api.app) as client:
        yield client, fake
    api._state["predictor"] = None


def test_classify_follows_the_contract(inference_client):
    client, fake = inference_client
    response = client.post("/classify", json={"title": "Cumbre en Bogotá", "text": "El canciller…"})
    assert response.status_code == 200
    data = response.json()
    assert data["label"] in IDEOLOGY_CLASSES
    assert set(data["probabilities"]) == set(IDEOLOGY_CLASSES)
    assert abs(sum(data["probabilities"].values()) - 1.0) < 1e-6
    assert data["model_version"] == "fake-1.0"
    # El servicio recibe título y cuerpo por separado y los compone él.
    assert fake.calls == [("Cumbre en Bogotá", "El canciller…")]


def test_health_reports_the_model_version(inference_client):
    client, _ = inference_client
    assert client.get("/health").json() == {"status": "ok", "model_version": "fake-1.0"}


def test_empty_text_is_rejected(inference_client):
    client, _ = inference_client
    assert client.post("/classify", json={"title": "Solo titular", "text": ""}).status_code == 422


def test_without_artifact_the_service_refuses_to_start(monkeypatch):
    monkeypatch.delenv("IDEOGRAPH_ARTIFACT", raising=False)
    api._state["predictor"] = None
    with pytest.raises(RuntimeError, match="IDEOGRAPH_ARTIFACT"):
        with TestClient(api.app):
            pass
