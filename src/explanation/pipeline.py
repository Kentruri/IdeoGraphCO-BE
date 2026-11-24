"""Genera y guarda las explicaciones que faltan.

Idempotente, como la reclasificación:
- Se explica cada artículo clasificado que no tenga una explicación vigente
  (`basis.is_current`): los nuevos y los que se reclasificaron después.
- Cada explicación se confirma apenas llega, así una corrida cortada conserva
  lo hecho y la siguiente retoma solo lo que falta.
- Un fallo del LLM no toca el artículo: queda sin explicación (la API manda
  null) y se reintenta en la próxima corrida. Si el proveedor se queda sin
  cuota o sin login, o fallan varios seguidos, la corrida se detiene.
- Primero van los artículos que se ven en la interfaz (el representativo de
  una story publicada), de lo más reciente a lo más antiguo.
"""

import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, defer, selectinload

from src.config import Settings
from src.core.schema import ARTICLE_CLASSIFICATION_DONE, STORY_STATUS_PUBLISHED
from src.db.models import Article, utcnow
from src.explanation.basis import basis_for, is_classified, is_current
from src.explanation.generator import Explainer, ExplainerUnavailable, Explanation
from src.explanation.prompts import ExplanationRequest

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class ExplainStats:
    # Artículos clasificados sin explicación vigente al empezar.
    pending: int = 0
    processed: int = 0
    explained: int = 0
    with_second: int = 0
    failures: int = 0
    # Apoyo del texto a la clase predicha, según el LLM (alto/medio/bajo → n).
    support: Counter = field(default_factory=Counter)
    aborted: str | None = None

    def summary(self) -> str:
        support = " ".join(f"{level}={self.support[level]}" for level in ("alto", "medio", "bajo"))
        return (
            f"pendientes={self.pending} procesadas={self.processed} "
            f"explicadas={self.explained} con_segunda_clase={self.with_second} "
            f"fallos={self.failures} apoyo_clase_predicha: {support}"
        )


def _visible(article: Article) -> bool:
    story = article.story
    return story.status == STORY_STATUS_PUBLISHED and story.representative_article_id == article.id


def select_pending(
    session: Session, *, force: bool = False, article_ids: list[str] | None = None
) -> list[Article]:
    """Artículos clasificados por explicar, primero los que se ven en la interfaz."""
    query = (
        select(Article)
        .options(selectinload(Article.story), defer(Article.text))
        .where(Article.classification_status == ARTICLE_CLASSIFICATION_DONE)
    )
    if article_ids is not None:
        query = query.where(Article.id.in_(article_ids))
    articles = [
        article
        for article in session.scalars(query)
        if is_classified(article) and (force or not is_current(article))
    ]
    articles.sort(
        key=lambda article: (not _visible(article), -article.published_at.timestamp(), article.id)
    )
    return articles


def _save(article: Article, basis: dict, explanation: Explanation) -> None:
    article.explanation_text = explanation.text
    article.explanation_basis = basis
    article.explanation_support = explanation.support
    article.explanation_model = explanation.model
    article.explanation_generated_at = utcnow()
    if article.story.representative_article_id == article.id:
        # Cambió lo que muestra el detalle de la story.
        article.story.updated_at = utcnow()


def run_explanations(
    session: Session,
    explainer: Explainer,
    settings: Settings,
    *,
    limit: int | None = None,
    force: bool = False,
    dry_run: bool = False,
    article_ids: list[str] | None = None,
    concurrency: int = 1,
    max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
    on_saved=None,
) -> ExplainStats:
    """Explica los artículos pendientes. `on_saved(article)` se llama tras guardar cada uno."""
    stats = ExplainStats()
    pending = select_pending(session, force=force, article_ids=article_ids)
    stats.pending = len(pending)
    if limit is not None:
        pending = pending[:limit]
    if dry_run or not pending:
        return stats

    jobs: list[tuple[Article, dict, ExplanationRequest]] = []
    for article in pending:
        basis = basis_for(article, settings.explanation_second_min_probability)
        request = ExplanationRequest(
            title=article.title,
            text=article.text,
            probabilities=dict(article.classification_probabilities),
            label=basis["label"],
            second=basis["second"],
        )
        jobs.append((article, basis, request))

    consecutive_failures = 0
    # Solo los hilos llaman al LLM; la sesión de SQLAlchemy se usa únicamente
    # en este hilo, que guarda cada resultado al llegar.
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(explainer.explain, request): (article, basis, request)
                   for article, basis, request in jobs}
        for future in as_completed(futures):
            if future.cancelled():
                continue
            article, basis, request = futures[future]
            stats.processed += 1
            try:
                explanation = future.result()
            except ExplainerUnavailable as error:
                explanation = None
                if stats.aborted is None:
                    stats.aborted = str(error)
                    # Las que ya están en curso terminan y se guardan; las demás no arrancan.
                    for other in futures:
                        other.cancel()
            except Exception as error:  # noqa: BLE001 — un artículo no tumba la corrida
                explanation = None
                logger.exception("Error explicando %s: %s", article.id, error)

            if explanation is None:
                stats.failures += 1
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures and stats.aborted is None:
                    stats.aborted = (
                        f"{consecutive_failures} fallos seguidos del LLM; "
                        "se detiene (vuelve a correr el comando para retomar)"
                    )
                    for other in futures:
                        other.cancel()
                continue

            consecutive_failures = 0
            _save(article, basis, explanation)
            session.commit()
            stats.explained += 1
            stats.with_second += int(request.second is not None)
            stats.support[explanation.support[request.label]] += 1
            if on_saved is not None:
                on_saved(article)

    if stats.aborted:
        logger.error("Explicaciones detenidas: %s", stats.aborted)
    logger.info("Explicaciones: %s", stats.summary())
    return stats
