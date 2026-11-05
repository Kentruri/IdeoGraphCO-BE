"""Similitud TF-IDF entre artículos, sin dependencias externas.

Se representa cada artículo por su titular (con doble peso), el subtítulo y
la entradilla (los primeros ~900 caracteres), que es donde la prensa
concentra el qué, quién, cuándo y dónde. Unigramas sobre texto normalizado
(minúsculas, sin tildes) y sin palabras vacías.

Calibración (4 nov. 2026, 54 artículos): las coberturas de un mismo hecho
(el atentado de El Estrecho, Patía) quedaron entre 0,29 y 0,37, y hechos
distintos del mismo tema (prórroga de la Ley 418 frente a la ley de
sometimiento) en 0,20. Con bigramas o con más texto las dos poblaciones se
mezclaban (todo por debajo de 0,22).
"""

import math
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from src.core.text import normalize_for_search

# Arranque del texto que entra a la representación (caracteres).
LEAD_CHARS = 900

_TOKEN_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """
    a al algo algunas algunos ante antes aquel aquella aquellas aquellos aqui asi
    aun aunque bajo bien cada casi como con contra cual cuales cuando cuanto de
    del desde donde dos durante e el ella ellas ellos en entre era eran es esa
    esas ese eso esos esta estaba estaban estan estar este esto estos fue fueron
    ha habia han hasta hay he la las le les lo los mas me mi mientras mismo muy
    ni no nos o otra otras otro otros para pero poco por porque que quien quienes
    se segun ser si sido sin sino sobre su sus tambien tan tanto te tiene tienen
    todo todos tras tu un una uno unos y ya
    dijo dice aseguro afirmo explico indico senalo agrego anadio sostuvo
    ano anos dia dias hoy ayer manana semana mes meses lunes martes miercoles
    jueves viernes sabado domingo enero febrero marzo abril mayo junio julio
    agosto septiembre octubre noviembre diciembre
    pais nacional gobierno colombia colombiano colombiana colombianos
    noticia noticias articulo foto video lea tambien leer
    """.split()
)


def tokens(text: str) -> list[str]:
    normalized = normalize_for_search(text)
    return [
        token
        for token in _TOKEN_RE.findall(normalized)
        if len(token) > 2 and token not in STOPWORDS and not token.isdigit()
    ]


def terms(text: str) -> list[str]:
    """Términos del vector: unigramas con contenido."""
    return tokens(text)


def document_text(title: str, subtitle: str | None, text: str) -> str:
    """Titular dos veces (más peso), subtítulo y arranque del cuerpo."""
    return " ".join(part for part in (title, title, subtitle or "", text[:LEAD_CHARS]) if part)


@dataclass
class TfidfIndex:
    """Vectores TF-IDF normalizados (L2) y un índice invertido para el coseno."""

    vectors: list[dict[str, float]]
    postings: dict[str, list[tuple[int, float]]]

    @classmethod
    def build(cls, documents: Sequence[str]) -> "TfidfIndex":
        counts = [Counter(terms(document)) for document in documents]
        document_frequency: Counter[str] = Counter()
        for count in counts:
            document_frequency.update(count.keys())
        total = len(documents)

        vectors: list[dict[str, float]] = []
        postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for index, count in enumerate(counts):
            weights = {
                term: (1 + math.log(frequency))
                * (math.log((1 + total) / (1 + document_frequency[term])) + 1)
                for term, frequency in count.items()
            }
            norm = math.sqrt(sum(weight * weight for weight in weights.values())) or 1.0
            vector = {term: weight / norm for term, weight in weights.items()}
            vectors.append(vector)
            for term, weight in vector.items():
                postings[term].append((index, weight))
        return cls(vectors=vectors, postings=dict(postings))

    def neighbours(self, index: int, minimum: float) -> dict[int, float]:
        """Coseno de `index` contra todos los demás, solo los que pasan `minimum`."""
        scores: dict[int, float] = defaultdict(float)
        for term, weight in self.vectors[index].items():
            for other, other_weight in self.postings[term]:
                if other != index:
                    scores[other] += weight * other_weight
        return {other: score for other, score in scores.items() if score >= minimum}

    def cosine(self, a: int, b: int) -> float:
        small, large = sorted((self.vectors[a], self.vectors[b]), key=len)
        return sum(weight * large.get(term, 0.0) for term, weight in small.items())
