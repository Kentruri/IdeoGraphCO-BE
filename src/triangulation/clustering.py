"""Agrupamiento de coberturas del mismo hecho (Fase 2).

Reglas, en orden de importancia:
1. Un medio aporta como máximo un artículo por grupo: triangular es
   contrastar medios distintos, no notas del mismo diario.
2. Solo se unen artículos publicados dentro de una ventana corta: dos notas
   parecidas con semanas de diferencia suelen ser hechos distintos del
   mismo tema (otra sesión del Congreso, otra marcha).
3. Enlace medio (average linkage) en vez de enlace simple: un grupo solo
   absorbe a otro si se parecen en promedio, no por un único par. Así se
   evita el encadenamiento A~B~C con A y C sin nada en común.
4. Lo que ya está agrupado (stories multi-fuente de corridas anteriores) se
   respeta: cada corrida solo suma, nunca deshace.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from src.triangulation.similarity import TfidfIndex, document_text

DEFAULT_THRESHOLD = 0.25
DEFAULT_WINDOW_HOURS = 48
DEFAULT_MAX_SOURCES = 5
# El promedio de similitud entre dos grupos debe alcanzar esta fracción
# del umbral para fusionarlos.
AVERAGE_LINKAGE_FACTOR = 0.8


@dataclass(frozen=True)
class ArticleView:
    """Lo que el agrupamiento necesita de un artículo (desacoplado del ORM)."""

    id: str
    story_id: str
    source_name: str
    published_at: datetime
    title: str
    subtitle: str | None
    text: str


@dataclass(frozen=True)
class Group:
    """Un grupo de artículos del mismo hecho y su similitud media interna."""

    article_ids: tuple[str, ...]
    cohesion: float


def cluster_articles(
    articles: Sequence[ArticleView],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    max_sources: int = DEFAULT_MAX_SOURCES,
) -> list[Group]:
    """Devuelve los grupos de 2+ artículos (de medios distintos) del mismo hecho."""
    if len(articles) < 2:
        return []
    index = TfidfIndex.build(
        [document_text(article.title, article.subtitle, article.text) for article in articles]
    )
    count = len(articles)
    parent = list(range(count))
    members: dict[int, list[int]] = {i: [i] for i in range(count)}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        root_a, root_b = find(a), find(b)
        if root_a == root_b:
            return
        parent[root_b] = root_a
        members[root_a].extend(members.pop(root_b))

    # Regla 4: los grupos existentes arrancan unidos.
    by_story: dict[str, int] = {}
    for i, article in enumerate(articles):
        if article.story_id in by_story:
            union(by_story[article.story_id], i)
        else:
            by_story[article.story_id] = i

    window_seconds = window_hours * 3600
    edges: list[tuple[float, int, int]] = []
    for i in range(count):
        for j, score in index.neighbours(i, threshold).items():
            if j <= i or articles[i].source_name == articles[j].source_name:
                continue
            gap = abs((articles[i].published_at - articles[j].published_at).total_seconds())
            if gap <= window_seconds:
                edges.append((score, i, j))
    edges.sort(reverse=True)

    for _score, i, j in edges:
        root_i, root_j = find(i), find(j)
        if root_i == root_j:
            continue
        group_i, group_j = members[root_i], members[root_j]
        if len(group_i) + len(group_j) > max_sources:
            continue
        sources_i = {articles[k].source_name for k in group_i}
        if any(articles[k].source_name in sources_i for k in group_j):
            continue
        dates = [articles[k].published_at for k in group_i + group_j]
        if (max(dates) - min(dates)).total_seconds() > window_seconds:
            continue
        average = sum(index.cosine(a, b) for a in group_i for b in group_j) / (
            len(group_i) * len(group_j)
        )
        if average < threshold * AVERAGE_LINKAGE_FACTOR:
            continue
        union(i, j)

    groups: list[Group] = []
    for root, indices in members.items():
        if find(root) != root or len(indices) < 2:
            continue
        pairs = [(a, b) for pos, a in enumerate(indices) for b in indices[pos + 1 :]]
        cohesion = sum(index.cosine(a, b) for a, b in pairs) / len(pairs)
        ordered = sorted(indices, key=lambda k: articles[k].published_at)
        groups.append(
            Group(
                article_ids=tuple(articles[k].id for k in ordered),
                cohesion=round(cohesion, 4),
            )
        )
    groups.sort(key=lambda group: (-len(group.article_ids), -group.cohesion))
    return groups
