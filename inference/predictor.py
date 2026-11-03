"""Predictor: carga el artefacto exportado y clasifica un artículo en 8 clases.

Reproduce el forward de `IdeoClassifier` (repo IdeoGraphCO) sin depender de
ese código ni de PyTorch Lightning:

    titular limpio + cuerpo → ventanas (troceo del artefacto)
        → un vector [CLS] por ventana → promedio de las ventanas
        → capa lineal de 8 salidas → softmax
"""

from __future__ import annotations

import json
from pathlib import Path

import torch
import torch.nn as nn
from safetensors.torch import load_file
from transformers import AutoModel, AutoTokenizer

from inference.chunking import STRATEGIES, chunk_token_ids
from inference.model_input import build_model_input
from src.core.schema import IDEOLOGY_CLASSES

ARTIFACT_META = "ideographco.json"


def pick_device(device: str | None = None) -> torch.device:
    if device:
        return torch.device(device)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


class IdeologyPredictor:
    """Artefacto exportado → clase predicha + distribución de las 8 clases."""

    def __init__(self, artifact_dir: str | Path, device: str | None = None) -> None:
        artifact_dir = Path(artifact_dir)
        meta = json.loads((artifact_dir / ARTIFACT_META).read_text(encoding="utf-8"))

        # Regla 3 del traspaso: un desajuste de orden no falla, devuelve la
        # clase equivocada con plena confianza. Por eso se verifica al cargar
        # y no se confía en que las dos listas sigan iguales.
        if meta["classes"] != IDEOLOGY_CLASSES:
            raise ValueError(
                "El orden de clases del artefacto no coincide con src/core/schema.py: "
                f"{meta['classes']} != {IDEOLOGY_CLASSES}"
            )
        chunking = meta["chunking"]
        self.chunk_size = int(chunking["chunk_size"])
        self.chunk_stride = int(chunking["chunk_stride"])
        self.max_chunks = int(chunking["max_chunks"])
        self.strategy = chunking["strategy"]
        if self.strategy not in STRATEGIES:
            raise ValueError(f"Estrategia de troceo desconocida: {self.strategy!r}")
        self.model_version: str = meta["model_version"]

        self.device = pick_device(device)
        encoder_dir = artifact_dir / "encoder"
        self.tokenizer = AutoTokenizer.from_pretrained(encoder_dir)
        self.encoder = AutoModel.from_pretrained(encoder_dir).to(self.device).eval()
        head = load_file(str(artifact_dir / "head.safetensors"))
        num_classes, hidden_size = head["weight"].shape
        self.classifier = nn.Linear(hidden_size, num_classes)
        self.classifier.load_state_dict(head)
        self.classifier.to(self.device).eval()

        # Mismos tokens especiales que el dataset: los tokenizadores tipo
        # RoBERTa (MrBERT-es) no declaran cls/sep y delimitan con <s> … </s>.
        tok = self.tokenizer
        self._cls_id = tok.cls_token_id if tok.cls_token_id is not None else tok.bos_token_id
        self._sep_id = tok.sep_token_id if tok.sep_token_id is not None else tok.eos_token_id
        self._pad_id = tok.pad_token_id
        # XLNet lleva <cls> al FINAL de la secuencia; la familia BERT, al inicio.
        self._cls_at_end = self.encoder.config.model_type == "xlnet"

    def _windows(self, text: str) -> tuple[torch.Tensor, torch.Tensor]:
        full_ids = self.tokenizer(text, add_special_tokens=False, truncation=False)["input_ids"]
        chunks = chunk_token_ids(
            full_ids, self.chunk_size, self.chunk_stride, self.max_chunks, self.strategy
        )
        if self._cls_at_end:
            seqs = [list(c) + [self._sep_id, self._cls_id] for c in chunks]
        else:
            seqs = [[self._cls_id] + list(c) + [self._sep_id] for c in chunks]
        seqs = [s[: self.chunk_size] for s in seqs]
        # Relleno a la derecha hasta la ventana más larga: el entrenamiento
        # rellena hasta chunk_size, pero las posiciones enmascaradas no
        # cambian la salida del encoder.
        width = max(len(s) for s in seqs)
        input_ids = [s + [self._pad_id] * (width - len(s)) for s in seqs]
        attention = [[1] * len(s) + [0] * (width - len(s)) for s in seqs]
        return (
            torch.tensor(input_ids, dtype=torch.long, device=self.device),
            torch.tensor(attention, dtype=torch.long, device=self.device),
        )

    @torch.no_grad()
    def predict_proba(self, title: str | None, text: str) -> list[float]:
        """Probabilidades de las 8 clases, en el orden de IDEOLOGY_CLASSES."""
        input_ids, attention_mask = self._windows(build_model_input(title, text))
        hidden = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
        if self._cls_at_end:
            last = attention_mask.sum(dim=1).clamp(min=1) - 1
            cls = hidden[torch.arange(hidden.size(0), device=hidden.device), last]
        else:
            cls = hidden[:, 0]
        # V_doc = promedio de los [CLS]: aquí todas las ventanas son reales.
        doc = cls.mean(dim=0, keepdim=True)
        return torch.softmax(self.classifier(doc), dim=-1)[0].float().cpu().tolist()

    def classify(self, title: str | None, text: str) -> dict:
        """Respuesta del contrato: label + probabilidades en [0, 1] + versión."""
        probs = self.predict_proba(title, text)
        best = max(range(len(probs)), key=lambda i: probs[i])
        probabilities = {cls: round(p, 4) for cls, p in zip(IDEOLOGY_CLASSES, probs, strict=True)}
        # El redondeo a 4 decimales puede dejar la suma en 0,9999 o 1,0001;
        # el residuo va a la clase ganadora para que sumen 1, como pide el
        # contrato (y como hace el mock).
        label = IDEOLOGY_CLASSES[best]
        residual = round(1.0 - sum(probabilities.values()), 4)
        probabilities[label] = round(probabilities[label] + residual, 4)
        return {
            "label": label,
            "probabilities": probabilities,
            "model_version": self.model_version,
        }
