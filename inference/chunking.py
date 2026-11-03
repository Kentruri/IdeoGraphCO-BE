"""Troceo del artículo en ventanas, idéntico al dataset de entrenamiento.

Réplica de `IdeoGraphDataset._chunk_article` (repo IdeoGraphCO,
src/training/data/dataset.py). Va aparte y sin PyTorch para poder probarlo
en el CI del BE, que no instala el modelo.

Los tres parámetros salen del artefacto exportado, nunca de valores escritos
a mano (docs/traspaso-inferencia-BE.md, regla 1): entrenar con 16 ventanas y
servir con 8 ya pasó una vez y no da ningún error, solo peores predicciones
en los artículos largos.
"""

from __future__ import annotations

STRATEGIES = ("sliding_window", "truncate")


def chunk_token_ids(
    full_ids: list[int],
    chunk_size: int,
    chunk_stride: int,
    max_chunks: int,
    strategy: str = "sliding_window",
) -> list[list[int]]:
    """Parte los ids del texto (sin tokens especiales) en ventanas.

    Cada ventana deja dos posiciones libres para [CLS] y [SEP]. Con
    512/384/16, la última ventana posible empieza en el token 5.760 y
    termina en el 6.270: lo que exceda se pierde, igual que al entrenar.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"Estrategia de troceo desconocida: {strategy!r}")
    content_length = chunk_size - 2
    if not full_ids:
        return [[]]
    if strategy == "truncate":
        return [full_ids[:content_length]]

    chunks: list[list[int]] = []
    start = 0
    while start < len(full_ids) and len(chunks) < max_chunks:
        end = min(start + content_length, len(full_ids))
        chunks.append(full_ids[start:end])
        if end == len(full_ids):
            break
        start += chunk_stride
    return chunks
