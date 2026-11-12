"""Búsqueda dirigida de coberturas: para cada noticia, otras notas del mismo hecho.

La ingesta solo trae las notas más recientes de cada medio, así que dos
coberturas del mismo hecho coinciden en la base solo por suerte. Esta
búsqueda la hace explícita: reúne las candidatas que publican los RSS y
mapas de sitio de los 83 medios del catálogo (cientos por corrida), las
compara con el titular de cada noticia sin triangular usando el titular
del RSS o, si no lo hay, las palabras de la URL, y solo descarga las que
se parecen. Lo descargado pasa por la ingesta normal (filtro de
politicidad y clasificación) y luego por el agrupamiento, que decide con
el texto completo.
"""

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Article, SkippedUrl
from src.ingestion.parser import discover_candidate_urls
from src.ingestion.sources import SOURCES
from src.triangulation.similarity import TfidfIndex, tokens

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_THRESHOLD = 0.3
DEFAULT_PER_SOURCE = 120
DEFAULT_PER_STORY = 4
DEFAULT_MAX_DOWNLOADS = 120
# Las candidatas con fecha (RSS) deben estar cerca de la noticia.
SEARCH_WINDOW_HOURS = 72
# Mínimo de palabras con contenido en común: evita que una sola palabra
# rara ("Patía") arrastre notas de otro hecho del mismo lugar.
MIN_SHARED_TERMS = 2
# Un titular de nota tiene varias palabras con contenido; los sitemaps
# también listan páginas de etiqueta ("gustavo-petro", "comision-primera")
# que no son notas y solo suman ruido.
MIN_CANDIDATE_TERMS = 4

_ID_SEGMENT = re.compile(r"^[a-z]*\d[\w-]*$|^\d+$")


@dataclass(frozen=True)
class Candidate:
    url: str
    source_name: str
    category: str
    title: str
    published_at: datetime | None


@dataclass(frozen=True)
class StoryQuery:
    """Lo que se busca: el titular (y subtítulo) de una noticia sin triangular."""

    story_id: str
    text: str
    published_at: datetime
    outlets: frozenset[str]


def slug_words(url: str) -> str:
    """Palabras del último tramo legible de la URL (el slug del titular)."""
    segments = [unquote(part) for part in urlparse(url).path.split("/") if part]
    for segment in reversed(segments):
        segment = re.sub(r"\.(html?|php|aspx?)$", "", segment.lower())
        if _ID_SEGMENT.match(segment) or len(segment) < 12:
            continue
        return re.sub(r"[-_]+", " ", segment)
    return ""


def _known_urls(session: Session) -> set[str]:
    known = set(session.scalars(select(Article.url)))
    known.update(session.scalars(select(SkippedUrl.url)))
    return known


def gather_candidates(
    session: Session,
    *,
    per_source: int = DEFAULT_PER_SOURCE,
    source_names: Iterable[str] | None = None,
) -> list[Candidate]:
    """Candidatas de todo el catálogo que todavía no están en la base."""
    known = _known_urls(session)
    selected = set(source_names) if source_names else None
    candidates: list[Candidate] = []
    for name, config in SOURCES.items():
        if selected is not None and name not in selected:
            continue
        try:
            found = discover_candidate_urls(config, max_candidates=per_source)
        except Exception as error:  # noqa: BLE001 — una fuente caída no frena la búsqueda
            logger.warning("Búsqueda: %s falló (%s)", name, str(error)[:80])
            continue
        for item in found:
            url = item["url"]
            if url in known:
                continue
            title = (item.get("title") or "").strip() or slug_words(url)
            if len(tokens(title)) < MIN_CANDIDATE_TERMS:
                continue
            candidates.append(
                Candidate(
                    url=url,
                    source_name=name,
                    category=config["category"],
                    title=title,
                    published_at=item.get("rss_published_at"),
                )
            )
    logger.info("Búsqueda: %d candidatas nuevas en el catálogo", len(candidates))
    return candidates


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def match_coverage(
    queries: list[StoryQuery],
    candidates: list[Candidate],
    *,
    threshold: float = DEFAULT_SEARCH_THRESHOLD,
    per_story: int = DEFAULT_PER_STORY,
    max_downloads: int = DEFAULT_MAX_DOWNLOADS,
) -> list[tuple[str, Candidate, float]]:
    """Por cada noticia, las candidatas más parecidas de otros medios.

    Devuelve (story_id, candidata, similitud), una candidata por medio y por
    noticia, sin repetir URL entre noticias, ordenadas de más a menos parecida.
    """
    if not queries or not candidates:
        return []
    index = TfidfIndex.build([query.text for query in queries] + [c.title for c in candidates])
    offset = len(queries)
    window = timedelta(hours=SEARCH_WINDOW_HOURS)
    proposals: list[tuple[float, str, Candidate]] = []
    for position, query in enumerate(queries):
        query_terms = set(tokens(query.text))
        best_by_outlet: dict[str, tuple[float, Candidate]] = {}
        for other, score in index.neighbours(position, threshold).items():
            if other < offset:
                continue
            candidate = candidates[other - offset]
            if candidate.source_name in query.outlets:
                continue
            if candidate.published_at is not None and abs(
                _as_aware(candidate.published_at) - _as_aware(query.published_at)
            ) > window:
                continue
            if len(query_terms & set(tokens(candidate.title))) < MIN_SHARED_TERMS:
                continue
            current = best_by_outlet.get(candidate.source_name)
            if current is None or score > current[0]:
                best_by_outlet[candidate.source_name] = (score, candidate)
        ranked = sorted(best_by_outlet.values(), key=lambda pair: -pair[0])[:per_story]
        proposals.extend((score, query.story_id, candidate) for score, candidate in ranked)

    proposals.sort(key=lambda item: -item[0])
    chosen: list[tuple[str, Candidate, float]] = []
    seen: set[str] = set()
    for score, story_id, candidate in proposals:
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        chosen.append((story_id, candidate, round(score, 4)))
        if len(chosen) >= max_downloads:
            break
    return chosen
