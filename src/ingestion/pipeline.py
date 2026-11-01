"""Pipeline de ingesta: descubrir → extraer → filtrar → clasificar → guardar.

Cada corrida es idempotente:
- Dedup por URL (articles.url UNIQUE) y por hash de contenido (cables
  republicados con otra URL).
- Los artículos con clasificación pendiente se reintentan en la corrida
  siguiente; una story solo se publica cuando su representativo está
  clasificado.

Fase actual (1): cada artículo crea su propia story (fuente única).
Fase 2 introducirá el clustering que agrupa artículos en stories
multi-fuente; este módulo ya persiste todo lo que ese paso necesita
(texto completo, hash, published_at).
"""

import hashlib
import logging
import random
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.classifier.client import ClassifierClient
from src.config import Settings
from src.core.ids import new_story_id, slugify
from src.core.schema import (
    ARTICLE_CLASSIFICATION_DONE,
    ARTICLE_CLASSIFICATION_PENDING,
    SNIPPET_MAX_CHARS,
    STORY_STATUS_INGESTED,
    STORY_STATUS_PUBLISHED,
    WORDS_PER_MINUTE,
)
from src.core.text import normalize_for_search
from src.db.models import Article, SkippedUrl, Story
from src.ingestion.parser import adaptive_sleep, discover_candidate_urls, extract_article
from src.ingestion.robots import is_url_allowed
from src.ingestion.sources import SOURCES

logger = logging.getLogger(__name__)

# Timeout por defecto para sockets sin timeout explícito (feedparser,
# robotparser, etc.): sin esto, UN feed o dominio colgado bloquea el cron.
DEFAULT_SOCKET_TIMEOUT_SECONDS = 20

# Tras N fallos consecutivos del filtro LLM (cuota agotada, servicio caído)
# se desactiva el filtro por el resto de la corrida en vez de quemar
# 3 reintentos × 60s por CADA artículo restante.
FILTER_CIRCUIT_BREAKER_FAILURES = 3

SKIP_REASON_NOT_POLITICAL = "not_political"
SKIP_REASON_DUPLICATE_CONTENT = "duplicate_content"


@dataclass
class IngestStats:
    discovered: int = 0
    skipped_existing: int = 0
    skipped_robots: int = 0
    skipped_duplicate_content: int = 0
    filtered_out: int = 0
    filter_failures: int = 0
    extraction_failed: int = 0
    saved: int = 0
    classified: int = 0
    pending_classification: int = 0
    reclassified_pending: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"descubiertas={self.discovered} guardadas={self.saved} "
            f"clasificadas={self.classified} pendientes={self.pending_classification} "
            f"repetidas={self.skipped_existing} dup_contenido={self.skipped_duplicate_content} "
            f"no_politicas={self.filtered_out} fallos_filtro={self.filter_failures} "
            f"robots={self.skipped_robots} "
            f"fallos_extraccion={self.extraction_failed} "
            f"pendientes_reclasificadas={self.reclassified_pending}"
        )


def compute_content_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def make_snippet(text: str, max_chars: int = SNIPPET_MAX_CHARS) -> str:
    """Primeros ~N caracteres cortados en límite de palabra."""
    flattened = " ".join(text.split())
    if len(flattened) <= max_chars:
        return flattened
    cut = flattened[:max_chars]
    cut = cut[: cut.rfind(" ")] if " " in cut else cut
    return cut.rstrip(",.;:") + "…"


def reading_time_minutes(text: str) -> int:
    return max(1, round(len(text.split()) / WORDS_PER_MINUTE))


def _classify_article(article: Article, classifier: ClassifierClient) -> bool:
    """Ejecuta el clasificador y actualiza el artículo. True si clasificó."""
    result = classifier.classify(article.title, article.text)
    if result is None:
        article.classification_status = ARTICLE_CLASSIFICATION_PENDING
        return False
    article.classification_status = ARTICLE_CLASSIFICATION_DONE
    article.classification_label = result.label
    article.classification_probabilities = result.probabilities
    article.classification_model_version = result.model_version
    return True


def _publish_if_ready(story: Story) -> None:
    """Publica la story si su representativo quedó clasificado.

    Exige además label y probabilities no vacíos (defensa en profundidad:
    el invariante "publicada ⇒ clasificación completa" no debe depender
    solo del status).
    """
    for article in story.articles:
        if (
            article.id == story.representative_article_id
            and article.classification_status == ARTICLE_CLASSIFICATION_DONE
            and article.classification_label
            and article.classification_probabilities
        ):
            story.status = STORY_STATUS_PUBLISHED
            return


def _persist_article(session: Session, data: dict, classifier: ClassifierClient,
                     stats: IngestStats, rss_published_at=None) -> None:
    """Crea Article + Story (fuente única, Fase 1) y clasifica.

    Preferencia de fecha: RSS (autoritativa del medio) > metadatos de la
    página (trafilatura, a veces errónea) > momento del scraping.
    """
    published_at = rss_published_at or data["published_at"] or data["scraped_at"]

    story = Story(
        id=new_story_id(),
        slug=slugify(data["title"], data["id"]),
        title=data["title"],
        status=STORY_STATUS_INGESTED,
        published_at=published_at,
        representative_article_id=data["id"],
        keywords=[],
    )
    article = Article(
        id=data["id"],
        story_id=story.id,
        url=data["url"],
        source_name=data["source"],
        source_category=data["category"],
        title=data["title"],
        subtitle=data["subtitle"],
        author=", ".join(data["authors"]) if data["authors"] else None,
        search_text=normalize_for_search(
            f"{data['title']} {data['subtitle'] or ''} {data['source']}"
        ),
        text=data["text"],
        snippet=make_snippet(data["text"]),
        content_hash=compute_content_hash(data["text"]),
        reading_time_minutes=reading_time_minutes(data["text"]),
        image_url=data["image_url"],
        published_at=published_at,
        scraped_at=data["scraped_at"],
    )
    story.articles.append(article)

    if _classify_article(article, classifier):
        stats.classified += 1
    else:
        stats.pending_classification += 1

    session.add(story)
    _publish_if_ready(story)
    session.commit()
    stats.saved += 1
    logger.info(
        "  GUARDADO [%s] %s (%s)",
        data["source"], data["title"][:80], article.classification_label or "pendiente",
    )


def _url_exists(session: Session, url: str) -> bool:
    return session.scalar(select(Article.id).where(Article.url == url)) is not None


def _url_skipped(session: Session, url: str) -> bool:
    return session.scalar(select(SkippedUrl.url).where(SkippedUrl.url == url)) is not None


def _mark_skipped(session: Session, url: str, reason: str) -> None:
    session.add(SkippedUrl(url=url, reason=reason))
    session.commit()


def _content_exists(session: Session, content_hash: str) -> bool:
    return (
        session.scalar(select(Article.id).where(Article.content_hash == content_hash))
        is not None
    )


def retry_pending_classifications(
    session: Session, classifier: ClassifierClient, stats: IngestStats
) -> None:
    """Reintenta clasificar artículos pendientes y publica sus stories."""
    pending = (
        session.scalars(
            select(Article).where(
                Article.classification_status == ARTICLE_CLASSIFICATION_PENDING
            )
        ).all()
    )
    for article in pending:
        if _classify_article(article, classifier):
            stats.reclassified_pending += 1
            _publish_if_ready(article.story)
    if pending:
        session.commit()


def run_ingest(
    session: Session,
    settings: Settings,
    classifier: ClassifierClient,
    source_names: list[str] | None = None,
    categories: list[str] | None = None,
    max_per_source: int = 5,
    use_filter: bool = True,
) -> IngestStats:
    """Corre una pasada completa de ingesta sobre las fuentes seleccionadas."""
    stats = IngestStats()

    # Red de seguridad para libs que abren sockets sin timeout (feedparser).
    socket.setdefaulttimeout(DEFAULT_SOCKET_TIMEOUT_SECONDS)

    selected_items = [
        (name, cfg)
        for name, cfg in SOURCES.items()
        if (source_names is None or name in source_names)
        and (categories is None or cfg["category"] in categories)
    ]
    if not selected_items:
        stats.errors.append("Ninguna fuente coincide con la selección")
        return stats

    # Orden aleatorio por corrida: si el job muere por timeout, no son
    # siempre las mismas fuentes del final las que se quedan sin procesar.
    random.shuffle(selected_items)

    gemini_client = None
    if use_filter:
        if not settings.gemini_api_key:
            logger.warning("Sin GEMINI_API_KEY: el filtro de politicidad se desactiva")
            use_filter = False
        else:
            from google import genai

            gemini_client = genai.Client(api_key=settings.gemini_api_key)

    last_filter_call = 0.0
    consecutive_filter_failures = 0

    for name, source_config in selected_items:
        logger.info("Fuente: %s (%s)", name, source_config["category"])
        try:
            candidates = discover_candidate_urls(source_config, max_candidates=max_per_source * 3)
        except Exception as e:
            stats.errors.append(f"{name}: descubrimiento falló ({e})")
            continue
        stats.discovered += len(candidates)

        saved_for_source = 0
        consecutive_errors = 0

        for candidate in candidates:
            url = candidate["url"]
            if saved_for_source >= max_per_source:
                break

            if _url_exists(session, url) or _url_skipped(session, url):
                stats.skipped_existing += 1
                continue

            if not is_url_allowed(url):
                stats.skipped_robots += 1
                continue

            data = extract_article(url, name, source_config["category"])
            if data is None or not data["title"]:
                stats.extraction_failed += 1
                consecutive_errors += 1
                adaptive_sleep(consecutive_errors)
                continue
            consecutive_errors = 0

            if _content_exists(session, compute_content_hash(data["text"])):
                stats.skipped_duplicate_content += 1
                _mark_skipped(session, url, SKIP_REASON_DUPLICATE_CONTENT)
                adaptive_sleep(0)
                continue

            if use_filter and gemini_client is not None:
                import time as _time

                from src.ingestion.political_filter import is_political_article

                # Rate limit proactivo para el free tier de Gemini
                elapsed = _time.monotonic() - last_filter_call
                if elapsed < settings.filter_rate_limit_seconds:
                    _time.sleep(settings.filter_rate_limit_seconds - elapsed)
                is_political, info = is_political_article(
                    gemini_client,
                    data["text"],
                    model=settings.filter_model,
                    escalate_model=settings.filter_escalate_model or None,
                    escalate_threshold=settings.filter_escalate_threshold,
                )
                last_filter_call = _time.monotonic()

                if info is None:
                    # Fallo del filtro (cuota/servicio) ≠ "no político": no se
                    # descarta la URL, se reintentará en la siguiente corrida.
                    stats.filter_failures += 1
                    consecutive_filter_failures += 1
                    if consecutive_filter_failures >= FILTER_CIRCUIT_BREAKER_FAILURES:
                        logger.error(
                            "Filtro LLM caído (%d fallos seguidos): se desactiva "
                            "por el resto de la corrida",
                            consecutive_filter_failures,
                        )
                        stats.errors.append("Filtro LLM desactivado por fallos consecutivos")
                        use_filter = False
                    adaptive_sleep(0)
                    continue

                consecutive_filter_failures = 0
                if not is_political:
                    stats.filtered_out += 1
                    _mark_skipped(session, url, SKIP_REASON_NOT_POLITICAL)
                    adaptive_sleep(0)
                    continue

            try:
                _persist_article(
                    session, data, classifier, stats,
                    rss_published_at=candidate.get("rss_published_at"),
                )
                saved_for_source += 1
            except Exception as e:
                session.rollback()
                stats.errors.append(f"{name}: persistencia falló para {url} ({e})")
                logger.exception("Error persistiendo %s", url)

            adaptive_sleep(0)

    retry_pending_classifications(session, classifier, stats)
    logger.info("Ingesta terminada: %s", stats.summary())
    return stats


def utcnow() -> datetime:
    return datetime.now(UTC)
