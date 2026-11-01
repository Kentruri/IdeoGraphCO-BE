"""Cliente del clasificador ideológico.

El clasificador NO vive en este repo: es el modelo de la tesis (repo
IdeoGraphCO) expuesto como servicio. Aquí solo se consume, detrás de una
interfaz para poder desarrollar sin el servicio (modo mock).

Contrato esperado del servicio real (a acordar con IdeoGraphCO):
    POST {CLASSIFIER_API_URL}/classify
    body:     {"title": str, "text": str}
    respuesta: {"label": str, "probabilities": {clase: float x8}, "model_version": str}
"""

import hashlib
import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from src.config import Settings
from src.core.schema import IDEOLOGY_CLASSES

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    label: str
    probabilities: dict[str, float]
    model_version: str


class ClassifierClient(Protocol):
    def classify(self, title: str, text: str) -> ClassificationResult | None:
        """Devuelve la clasificación o None si el servicio falló."""
        ...


class MockClassifierClient:
    """Distribuciones deterministas de prueba (desarrollo sin el servicio real).

    Determinista: el mismo texto produce siempre la misma distribución, así
    las corridas repetidas del pipeline no 'cambian de opinión'.
    """

    MODEL_VERSION = "mock-0.1.0"

    def classify(self, title: str, text: str) -> ClassificationResult | None:
        digest = hashlib.sha256((title + text[:2000]).encode("utf-8")).digest()
        # 8 pesos pseudoaleatorios pero deterministas; softmax-normalizados
        raw = [1.0 + digest[i] / 32.0 for i in range(len(IDEOLOGY_CLASSES))]
        # Acentuar una clase dominante para que el mock se parezca a una
        # salida real (una clase ~35-50%, resto repartido)
        dominant = digest[8] % len(IDEOLOGY_CLASSES)
        raw[dominant] *= 3.0
        total = sum(raw)
        probabilities = {
            cls: round(weight / total, 4)
            for cls, weight in zip(IDEOLOGY_CLASSES, raw, strict=True)
        }
        # Corregir residuo de redondeo en la dominante para que sume 1.0
        residual = round(1.0 - sum(probabilities.values()), 4)
        dominant_cls = IDEOLOGY_CLASSES[dominant]
        probabilities[dominant_cls] = round(probabilities[dominant_cls] + residual, 4)
        return ClassificationResult(
            label=max(probabilities, key=probabilities.get),
            probabilities=probabilities,
            model_version=self.MODEL_VERSION,
        )


class HttpClassifierClient:
    """Cliente HTTP del servicio de inferencia real (IdeoGraphCO)."""

    def __init__(self, base_url: str, timeout_seconds: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def classify(self, title: str, text: str) -> ClassificationResult | None:
        try:
            response = httpx.post(
                f"{self._base_url}/classify",
                json={"title": title, "text": text},
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
            probabilities = data["probabilities"]
            missing = set(IDEOLOGY_CLASSES) - set(probabilities)
            if missing:
                logger.error("Respuesta del clasificador sin clases %s", missing)
                return None
            label = data.get("label")
            if label not in IDEOLOGY_CLASSES:
                # Un label nulo/desconocido rompería el contrato tipado del FE
                # y publicaría stories sin clasificación válida. Devolver None
                # deja el artículo en `pending` y se reintenta en la siguiente
                # corrida.
                logger.error("Respuesta del clasificador con label inválido: %r", label)
                return None
            return ClassificationResult(
                label=label,
                probabilities=probabilities,
                model_version=data.get("model_version", "unknown"),
            )
        except (httpx.HTTPError, KeyError, ValueError) as e:
            logger.warning("Clasificador no disponible: %s", str(e)[:200])
            return None


class NullClassifierClient:
    """Modo 'off': no clasifica; los artículos quedan pending."""

    def classify(self, title: str, text: str) -> ClassificationResult | None:
        return None


def get_classifier(settings: Settings) -> ClassifierClient:
    if settings.classifier_mode == "http":
        if not settings.classifier_api_url:
            raise ValueError("CLASSIFIER_MODE=http requiere CLASSIFIER_API_URL")
        return HttpClassifierClient(
            settings.classifier_api_url, settings.classifier_timeout_seconds
        )
    if settings.classifier_mode == "off":
        return NullClassifierClient()
    return MockClassifierClient()
