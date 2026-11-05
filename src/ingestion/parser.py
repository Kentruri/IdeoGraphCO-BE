"""Descubrimiento y extracción de artículos (portado de IdeoGraphCO).

Cambios respecto al scraper de la tesis:
- Sin acoplamiento a SQLite: la deduplicación la decide el pipeline
  (contra la BD del backend), no este módulo.
- Sin tqdm (corre en cron, los logs bastan).
- Orientado a frescura: RSS primero y en orden (los feeds vienen
  newest-first); el sitemap solo si la fuente no declara RSS.
- Extrae también subtitle (description) e image de los metadatos,
  que el contrato del frontend necesita.
"""

import logging
import random
import re
import time
from datetime import UTC, datetime

import feedparser
import trafilatura
from trafilatura.settings import use_config
from trafilatura.sitemaps import sitemap_search

from src.core.ids import article_id
from src.ingestion.cleaner import clean_article_text
from src.ingestion.http_config import get_random_user_agent

logger = logging.getLogger(__name__)

for noisy in (
    "courlan",
    "trafilatura",
    "htmldate",
    "trafilatura.metadata",
    "trafilatura.htmlprocessing",
):
    logging.getLogger(noisy).setLevel(logging.ERROR)

_TRAFILATURA_CONFIG = use_config()
_TRAFILATURA_CONFIG.set("DEFAULT", "DOWNLOAD_TIMEOUT", "15")
_TRAFILATURA_CONFIG.set("DEFAULT", "EXTRACTION_TIMEOUT", "20")

MIN_TEXT_CHARS = 400

_BASE_DELAY_MIN = 1.0
_BASE_DELAY_MAX = 3.0
_BACKOFF_FACTOR = 2.0
_MAX_DELAY = 30.0


def _set_random_user_agent() -> None:
    _TRAFILATURA_CONFIG.set("DEFAULT", "USER_AGENTS", get_random_user_agent())


def adaptive_sleep(consecutive_errors: int) -> None:
    """Espera adaptativa entre requests: crece exponencialmente con errores."""
    base = random.uniform(_BASE_DELAY_MIN, _BASE_DELAY_MAX)
    delay = min(base * (_BACKOFF_FACTOR**consecutive_errors), _MAX_DELAY)
    time.sleep(delay)


# ---------------------------------------------------------------------------
# Descubrimiento de URLs
# ---------------------------------------------------------------------------


def _filter_political_urls(urls: list[str], url_filters: list[str]) -> list[str]:
    if not url_filters:
        return urls
    pattern = re.compile("|".join(re.escape(f) for f in url_filters), re.IGNORECASE)
    return [url for url in urls if pattern.search(url)]


def _rss_entry_date(entry) -> datetime | None:
    """Fecha de publicación del entry RSS (struct_time → datetime UTC)."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def discover_urls_rss(feeds: list[str]) -> list[dict]:
    """Candidatas desde RSS: [{url, rss_published_at, title}] en orden del feed.

    El titular del feed lo usa la búsqueda dirigida de la triangulación para
    comparar candidatas sin descargarlas.

    La fecha del feed es la autoritativa del medio — trafilatura a veces
    extrae fechas erróneas de la página (sidebars, artículos relacionados).
    """
    candidates: list[dict] = []
    seen: set[str] = set()
    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                url = entry.get("link", "")
                if url and url not in seen:
                    seen.add(url)
                    candidates.append(
                        {
                            "url": url,
                            "rss_published_at": _rss_entry_date(entry),
                            "title": entry.get("title", ""),
                        }
                    )
        except Exception:
            logger.warning("Error parseando RSS %s", feed_url)
    return candidates


def discover_urls_sitemap(base_url: str, url_filters: list[str], timeout: int = 30) -> list[str]:
    """Sitemap con timeout vía thread (algunos sitios cuelgan o son gigantes)."""
    import threading

    result: list[str] = []
    error: list[str] = []

    def _search() -> None:
        try:
            all_urls = sitemap_search(base_url)
            if all_urls:
                result.extend(_filter_political_urls(list(all_urls), url_filters))
        except Exception as e:
            error.append(str(e))

    thread = threading.Thread(target=_search, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        logger.warning("Sitemap de %s timeout tras %ds", base_url, timeout)
        return []
    if error:
        logger.warning("Error leyendo sitemap de %s: %s", base_url, error[0][:100])
        return []
    return result


def discover_candidate_urls(source_config: dict, max_candidates: int) -> list[dict]:
    """Candidatas de una fuente: [{url, rss_published_at}], priorizando frescura.

    RSS primero y EN ORDEN (newest-first). Sitemap solo como respaldo si la
    fuente no declara feeds — a diferencia del scraper de la tesis, aquí no
    queremos histórico sino lo último. Las candidatas de sitemap no traen
    fecha (rss_published_at=None).
    """
    rss_feeds = source_config.get("rss_feeds", [])
    url_filters = source_config.get("url_filters", [])
    mode = source_config.get("mode", "direct")

    candidates: list[dict] = []
    if rss_feeds:
        candidates = discover_urls_rss(rss_feeds)
        logger.info("  RSS: %d URLs", len(candidates))

    if not candidates:
        sitemap_urls = discover_urls_sitemap(
            source_config["url"], url_filters if mode == "sitemap" else []
        )
        logger.info("  Sitemap: %d URLs", len(sitemap_urls))
        candidates = [{"url": url, "rss_published_at": None} for url in sitemap_urls]

    return candidates[:max_candidates]


# ---------------------------------------------------------------------------
# Extracción
# ---------------------------------------------------------------------------


def _parse_published_at(raw_date: str | None) -> datetime | None:
    if not raw_date:
        return None
    try:
        parsed = datetime.fromisoformat(raw_date)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def extract_article(url: str, source: str, category: str) -> dict | None:
    """Descarga y extrae un artículo. Devuelve dict o None si no es usable.

    No toca la base de datos: dedup y persistencia son del pipeline.
    """
    try:
        _set_random_user_agent()
        downloaded = trafilatura.fetch_url(url, config=_TRAFILATURA_CONFIG)
        if downloaded is None:
            return None

        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
        )
        if not text or len(text) < MIN_TEXT_CHARS:
            return None

        metadata = trafilatura.extract_metadata(downloaded)
        authors: list[str] = []
        if metadata and metadata.author:
            authors = [a.strip() for a in metadata.author.split(";") if a.strip()]

        text = clean_article_text(text, source_name=source, authors=authors)
        if len(text) < MIN_TEXT_CHARS:
            return None

        return {
            "id": article_id(url),
            "url": url,
            "text": text,
            "title": (metadata.title if metadata and metadata.title else "").strip(),
            "subtitle": (metadata.description or "").strip() or None if metadata else None,
            "authors": authors,
            "image_url": (metadata.image or None) if metadata else None,
            "source": source,
            "category": category,
            "published_at": _parse_published_at(metadata.date if metadata else None),
            "scraped_at": datetime.now(UTC),
        }
    except TimeoutError as e:
        logger.warning("Timeout extrayendo %s: %s", url, e)
        return None
    except (ConnectionError, OSError) as e:
        logger.warning("Error de red en %s: %s", url, e)
        return None
    except Exception as e:
        logger.warning(
            "Error inesperado extrayendo %s (%s): %s", url, type(e).__name__, str(e)[:120]
        )
        return None
