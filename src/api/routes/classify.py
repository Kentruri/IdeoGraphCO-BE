"""Clasificación a demanda para la mesa de redacción del prototipo (OE4).

No guarda nada: recibe titular y texto, los pasa por el mismo cliente del
clasificador que usa la ingesta (`CLASSIFIER_MODE`) y devuelve la
ClassificationDTO. Con `http` responde el modelo servido en `inference/`;
con `mock`, la distribución determinista de desarrollo.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from src.classifier.client import ClassifierClient, get_classifier
from src.config import get_settings
from src.schemas.dto import ClassificationDTO, ClassifyRequestDTO

router = APIRouter(prefix="/api/v1", tags=["classify"])


def get_classifier_client() -> ClassifierClient:
    return get_classifier(get_settings())


@router.post("/classify", response_model=ClassificationDTO)
def classify(
    body: ClassifyRequestDTO,
    classifier: Annotated[ClassifierClient, Depends(get_classifier_client)],
) -> ClassificationDTO:
    result = classifier.classify(body.title, body.text)
    if result is None:
        # Servicio caído o modo `off`: el FE muestra el error en vez de
        # inventar un veredicto.
        raise HTTPException(
            status_code=503,
            detail="El clasificador no está disponible en este momento.",
        )
    return ClassificationDTO(
        label=result.label,
        probabilities=result.probabilities,
        model_version=result.model_version,
    )
