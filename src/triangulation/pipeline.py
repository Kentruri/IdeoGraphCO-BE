"""Corrida de triangulación: agrupar, fusionar stories y sintetizar.

Idempotente, como la ingesta:
- Los grupos existentes se respetan y solo crecen (clustering.py, regla 4).
- Una story recibe síntesis una sola vez; si cambian sus fuentes, la
  síntesis anterior se borra y se rehace en la corrida siguiente.
- Si la síntesis falla, la story sigue publicada con su fuente original y
  se reintenta en la próxima corrida (lección de TUJ: un fallo no puede
  perder el hecho para siempre).

La noticia clasificada sigue siendo la original (el representativo, primer
medio en reportar). La síntesis es un texto aparte, etiquetado, que se
clasifica con el mismo modelo para mostrar su encuadre junto al original.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import null, select
from sqlalchemy.orm import Session, selectinload

from src.classifier.client import ClassifierClient
from src.config import Settings
from src.core.ids import new_story_id, slugify
from src.core.schema import ARTICLE_CLASSIFICATION_DONE, STORY_STATUS_PUBLISHED
from src.db.models import Article, Story
from src.ingestion.pipeline import ingest_urls
from src.ingestion.sources import source_display_name
from src.triangulation.clustering import ArticleView, Group, cluster_articles
from src.triangulation.search import StoryQuery, gather_candidates, match_coverage
from src.triangulation.synthesis import synthesize

logger = logging.getLogger(__name__)


@dataclass
class TriangulationStats:
    searched_stories: int = 0
    search_candidates: int = 0
    search_matches: int = 0
    search_saved: int = 0
    articles_considered: int = 0
    groups: int = 0
    stories_merged: int = 0
    synthesized: int = 0
    synthesis_failed: int = 0
    detached: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"búsqueda: noticias={self.searched_stories} candidatas={self.search_candidates} "
            f"coincidencias={self.search_matches} guardadas={self.search_saved} | "
            f"artículos={self.articles_considered} grupos={self.groups} "
            f"stories_fusionadas={self.stories_merged} sintetizadas={self.synthesized} "
            f"síntesis_fallidas={self.synthesis_failed} fuentes_devueltas={self.detached} "
            f"errores={len(self.errors)}"
        )


def utcnow() -> datetime:
    return datetime.now(UTC)


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def load_pool(session: Session, since: datetime | None) -> list[Article]:
    """Artículos clasificados de stories publicadas (opcionalmente recientes)."""
    query = (
        select(Article)
        .join(Story, Article.story_id == Story.id)
        .where(
            Story.status == STORY_STATUS_PUBLISHED,
            Article.classification_status == ARTICLE_CLASSIFICATION_DONE,
        )
        .options(selectinload(Article.story))
    )
    if since is not None:
        query = query.where(Article.published_at >= since)
    return list(session.scalars(query))


def _view(article: Article) -> ArticleView:
    return ArticleView(
        id=article.id,
        story_id=article.story_id,
        source_name=article.source_name,
        published_at=_as_aware(article.published_at),
        title=article.title,
        subtitle=article.subtitle,
        text=article.text,
    )


def _clear_synthesis(story: Story) -> None:
    # null() y no None: en una columna JSON, None se guarda como el JSON
    # `null`, que `IS NULL` no encuentra, y la story nunca se re-sintetizaría.
    story.synthesized_title = None
    story.synthesized_body = null()
    story.synthesized_model = None
    story.synthesized_at = None
    story.synthesized_classification = null()


def merge_group(session: Session, articles: list[Article]) -> tuple[Story, bool]:
    """Reúne los artículos en la story del primero en reportar.

    Devuelve la story destino y si cambió su conjunto de fuentes.
    """
    earliest = min(articles, key=lambda article: _as_aware(article.published_at))
    # Si el grupo ya tenía una story de varios medios (quizá con síntesis y
    # con su enlace ya compartido), esa conserva su slug; solo cambia cuál es
    # el representativo. Si no, la story destino es la del primero en reportar.
    multi = {article.story for article in articles if len(article.story.articles) > 1}
    target = (
        max(multi, key=lambda story: (story.synthesized_body is not None, len(story.articles)))
        if multi
        else earliest.story
    )
    changed = False
    for article in articles:
        if article.story_id == target.id:
            continue
        old_story = article.story
        article.story = target
        changed = True
        session.flush()
        if old_story is not None and not old_story.articles:
            session.delete(old_story)
    if changed:
        target.representative_article_id = earliest.id
        target.published_at = earliest.published_at
        target.title = earliest.title
        _clear_synthesis(target)
    return target, changed


def detach_article(session: Session, article: Article) -> Story:
    """Devuelve un artículo a su propia story de fuente única."""
    story = Story(
        id=new_story_id(),
        slug=slugify(article.title, article.id),
        title=article.title,
        status=STORY_STATUS_PUBLISHED,
        published_at=article.published_at,
        representative_article_id=article.id,
    )
    session.add(story)
    session.flush()
    article.story = story
    session.flush()
    return story


def _sources_payload(articles: list[Article]) -> list[dict[str, str]]:
    return [
        {
            "outlet": source_display_name(article.source_name),
            "date": _as_aware(article.published_at).strftime("%Y-%m-%d %H:%M UTC"),
            "title": article.title,
            "text": article.text,
        }
        for article in articles
    ]


def synthesize_story(
    session: Session,
    story: Story,
    gemini_client,
    classifier: ClassifierClient,
    settings: Settings,
    stats: TriangulationStats,
) -> bool:
    articles = sorted(story.articles, key=lambda article: _as_aware(article.published_at))
    result = synthesize(
        gemini_client,
        settings.synthesis_model,
        _sources_payload(articles),
        min_sources=settings.synthesis_min_sources,
    )
    if result is None:
        stats.synthesis_failed += 1
        return False

    representative_id = story.representative_article_id or articles[0].id
    used = {articles[index].id for index in result.used_sources}
    if representative_id not in used:
        # La síntesis debe tratar del hecho de la noticia clasificada; si el
        # modelo dejó por fuera al representativo, el grupo no sirve.
        logger.warning("Síntesis descartada: no incluye la noticia original (%s)", story.slug)
        stats.synthesis_failed += 1
        return False
    for article in articles:
        if article.id not in used:
            detach_article(session, article)
            stats.detached += 1

    story.synthesized_title = result.title
    story.synthesized_body = result.paragraphs
    story.synthesized_model = result.model
    story.synthesized_at = utcnow()
    classification = classifier.classify(result.title, "\n\n".join(result.paragraphs))
    story.synthesized_classification = (
        {
            "label": classification.label,
            "probabilities": classification.probabilities,
            "model_version": classification.model_version,
        }
        if classification is not None
        else None
    )
    stats.synthesized += 1
    return True


def search_coverage(
    session: Session,
    settings: Settings,
    classifier: ClassifierClient,
    stats: TriangulationStats,
    *,
    since: datetime | None,
    max_queries: int = 60,
    per_source: int = 120,
    threshold: float = 0.3,
    dry_run: bool = False,
) -> list[tuple[str, object, float]]:
    """Paso 1: para cada noticia reciente sin triangular, buscar otras coberturas."""
    query = (
        select(Story)
        .where(Story.status == STORY_STATUS_PUBLISHED, Story.synthesized_body.is_(None))
        .options(selectinload(Story.articles))
        .order_by(Story.published_at.desc())
    )
    if since is not None:
        query = query.where(Story.published_at >= since)
    queries: list[StoryQuery] = []
    for story in session.scalars(query):
        if len(story.articles) >= settings.synthesis_min_sources:
            continue
        lead = min(story.articles, key=lambda article: _as_aware(article.published_at))
        queries.append(
            StoryQuery(
                story_id=story.id,
                text=" ".join(part for part in (lead.title, lead.subtitle or "") if part),
                published_at=_as_aware(story.published_at),
                outlets=frozenset(article.source_name for article in story.articles),
            )
        )
        if len(queries) >= max_queries:
            break
    stats.searched_stories = len(queries)
    if not queries:
        return []
    candidates = gather_candidates(session, per_source=per_source)
    stats.search_candidates = len(candidates)
    matches = match_coverage(queries, candidates, threshold=threshold)
    stats.search_matches = len(matches)
    if dry_run or not matches:
        return matches
    ingested = ingest_urls(
        session,
        settings,
        classifier,
        [
            {
                "url": candidate.url,
                "source_name": candidate.source_name,
                "category": candidate.category,
                "rss_published_at": candidate.published_at,
            }
            for _story_id, candidate, _score in matches
        ],
    )
    stats.search_saved = ingested.saved
    stats.errors.extend(ingested.errors)
    return matches


def run_triangulation(
    session: Session,
    settings: Settings,
    classifier: ClassifierClient,
    *,
    days: float | None = 7,
    threshold: float = 0.25,
    window_hours: float = 48,
    max_sources: int = 5,
    do_synthesis: bool = True,
    dry_run: bool = False,
    limit: int | None = None,
    gemini_client=None,
    search: bool = False,
    search_threshold: float = 0.3,
    max_queries: int = 60,
) -> tuple[TriangulationStats, list[Group]]:
    stats = TriangulationStats()
    since = utcnow() - timedelta(days=days) if days else None
    if search:
        search_coverage(
            session,
            settings,
            classifier,
            stats,
            since=since,
            max_queries=max_queries,
            threshold=search_threshold,
            dry_run=dry_run,
        )
    pool = load_pool(session, since)
    stats.articles_considered = len(pool)
    groups = cluster_articles(
        [_view(article) for article in pool],
        threshold=threshold,
        window_hours=window_hours,
        max_sources=max_sources,
    )
    # Solo se triangula con ≥N medios: un par suelto no se fusiona, porque
    # dos notas del mismo tema no bastan para afirmar que son el mismo hecho
    # (y sin síntesis no hay verificación del LLM). Si después aparece un
    # tercer medio, el grupo se forma en esa corrida.
    groups = [group for group in groups if len(group.article_ids) >= settings.synthesis_min_sources]
    stats.groups = len(groups)
    if dry_run:
        return stats, groups

    by_id = {article.id: article for article in pool}
    for group in groups:
        try:
            _story, changed = merge_group(session, [by_id[i] for i in group.article_ids])
            stats.stories_merged += int(changed)
        except Exception as error:  # noqa: BLE001 — un grupo no tumba la corrida
            session.rollback()
            stats.errors.append(f"fusión falló ({error})")
            logger.exception("Error fusionando %s", group.article_ids)
    session.commit()

    if not do_synthesis:
        return stats, groups
    if gemini_client is None:
        if not settings.gemini_api_key:
            logger.warning("Sin GEMINI_API_KEY: se agrupa pero no se sintetiza")
            return stats, groups
        from google import genai

        gemini_client = genai.Client(api_key=settings.gemini_api_key)

    candidates = [
        story
        for story in session.scalars(
            select(Story)
            .where(Story.status == STORY_STATUS_PUBLISHED, Story.synthesized_body.is_(None))
            .options(selectinload(Story.articles))
        )
        if len(story.articles) >= settings.synthesis_min_sources
    ]
    candidates.sort(key=lambda story: _as_aware(story.published_at), reverse=True)
    for position, story in enumerate(candidates[:limit] if limit else candidates):
        if position:
            time.sleep(settings.filter_rate_limit_seconds)
        try:
            synthesize_story(session, story, gemini_client, classifier, settings, stats)
            session.commit()
        except Exception as error:  # noqa: BLE001
            session.rollback()
            stats.errors.append(f"{story.slug}: síntesis falló ({error})")
            logger.exception("Error sintetizando %s", story.slug)
    return stats, groups
