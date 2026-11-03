"""Servicio de inferencia: el clasificador del OE2 expuesto por HTTP.

Corre aparte de la API de lectura porque necesita PyTorch: la API pública
sigue siendo liviana y la ingesta lo consume con `CLASSIFIER_MODE=http`.

Contrato (el de `src/classifier/client.py::HttpClassifierClient`):

    POST /classify  {"title": str, "text": str}
        → {"label": str, "probabilities": {clase: float ×8}, "model_version": str}
    GET  /health    → {"status": "ok", "model_version": str}

Las probabilidades van en [0, 1] y suman 1.

Uso local:
    IDEOGRAPH_ARTIFACT=models/beto-cribado-s42 uvicorn inference.api:app --port 8080
"""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

_state: dict = {"predictor": None}
# Un solo forward a la vez: el modelo no es seguro entre hilos en MPS y en
# CPU dos artículos simultáneos solo compiten por los mismos núcleos.
_lock = threading.Lock()


class ClassifyRequest(BaseModel):
    title: str = ""
    text: str = Field(min_length=1, description="Cuerpo del artículo (texto plano)")


class ClassifyResponse(BaseModel):
    label: str
    probabilities: dict[str, float]
    model_version: str


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if _state["predictor"] is None:
        artifact = os.environ.get("IDEOGRAPH_ARTIFACT")
        if not artifact or not Path(artifact).is_dir():
            raise RuntimeError(
                "Define IDEOGRAPH_ARTIFACT con la carpeta del artefacto exportado "
                "(python -m inference.export_checkpoint …)"
            )
        # Import diferido: PyTorch solo se carga en el servicio, no en los tests.
        from inference.predictor import IdeologyPredictor

        _state["predictor"] = IdeologyPredictor(artifact, device=os.environ.get("IDEOGRAPH_DEVICE"))
    yield


app = FastAPI(
    title="IdeoGraphCO — Servicio de inferencia",
    description="Clasificador multiclase de orientación ideológica (8 clases) para noticias políticas colombianas.",
    lifespan=lifespan,
)


def _predictor():
    predictor = _state["predictor"]
    if predictor is None:
        raise HTTPException(status_code=503, detail="Modelo no cargado")
    return predictor


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model_version": _predictor().model_version}


@app.post("/classify", response_model=ClassifyResponse)
def classify(request: ClassifyRequest) -> ClassifyResponse:
    predictor = _predictor()
    with _lock:
        result = predictor.classify(request.title, request.text)
    return ClassifyResponse(**result)
