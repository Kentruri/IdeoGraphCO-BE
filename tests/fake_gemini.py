"""Cliente de Gemini simulado: las pruebas del filtro no necesitan una clave real."""

import json
from types import SimpleNamespace


class FakeModels:
    """Imita `client.models` de google-genai: una respuesta (o excepción) por llamada."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(text=response)


def fake_gemini_client(*responses) -> SimpleNamespace:
    """Cada respuesta es el JSON que devolvería Gemini, None (bloqueada) o una excepción."""
    return SimpleNamespace(models=FakeModels(responses))


def filter_json(category: str, confidence: float, reason: str = "motivo") -> str:
    return json.dumps({"category": category, "confidence": confidence, "reason": reason})
