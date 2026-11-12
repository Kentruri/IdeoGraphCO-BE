"""Reclasificación de artículos guardados cuando cambia el modelo servido.

Cada artículo guarda la versión del modelo que emitió su clasificación
(`classification_model_version`). Al servir un artefacto nuevo (otra
`--model-version`), este paso busca los artículos clasificados con otra
versión y los vuelve a pasar por el MISMO cliente que usa la ingesta.

Garantías:
- Por lotes: cada lote se confirma por separado, así una corrida cortada
  conserva lo ya hecho y la siguiente retoma solo lo que falta (la consulta
  es "clasificación de otra versión", que deja de cumplirse al guardar).
- Un fallo del servicio NO toca el artículo: conserva la clasificación
  anterior y su story sigue publicada. Se reintenta en la próxima corrida.
- Si el servicio responde con una versión distinta a la consultada al
  empezar (cambiaron el artefacto a mitad de corrida), se detiene sin
  guardar ese resultado: mezclar dos modelos en una pasada no es reclasificar.
- Los artículos `pending` no se tocan: los reintenta la ingesta.
"""

import logging
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from src.classifier.client import ClassifierClient
from src.core.schema import ARTICLE_CLASSIFICATION_DONE, ARTICLE_CLASSIFICATION_PENDING
from src.db.models import Article, utcnow

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 50

# Tras N fallos seguidos se asume el servicio caído y se corta la corrida
# (mismo criterio que el filtro LLM de la ingesta).
MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class ReclassifyStats:
    target_version: str | None = None
    # Artículos con clasificación de otra versión al empezar, por versión vieja
    outdated_by_version: dict[str | None, int] = field(default_factory=dict)
    pending: int = 0
    processed: int = 0
    reclassified: int = 0
    label_changed: int = 0
    failures: int = 0
    batches: int = 0
    remaining: int = 0
    transitions: Counter = field(default_factory=Counter)  # (vieja, nueva) → n
    aborted: str | None = None

    @property
    def outdated(self) -> int:
        return sum(self.outdated_by_version.values())

    def versions_summary(self) -> str:
        return ", ".join(
            f"{version or 'sin versión'}={count}"
            for version, count in sorted(
                self.outdated_by_version.items(), key=lambda item: (-item[1], item[0] or "")
            )
        )

    def summary(self) -> str:
        return (
            f"procesados={self.processed} reclasificados={self.reclassified} "
            f"cambiaron_de_clase={self.label_changed} fallos={self.failures} "
            f"lotes={self.batches} restantes={self.remaining}"
        )


def _outdated_condition(target_version: str):
    # `!=` con NULL da NULL en SQL: los clasificados sin versión se piden aparte.
    return (Article.classification_status == ARTICLE_CLASSIFICATION_DONE) & or_(
        Article.classification_model_version.is_(None),
        Article.classification_model_version != target_version,
    )


def count_outdated(session: Session, target_version: str) -> dict[str | None, int]:
    """Artículos clasificados con una versión distinta a `target_version`."""
    rows = session.execute(
        select(Article.classification_model_version, func.count())
        .where(_outdated_condition(target_version))
        .group_by(Article.classification_model_version)
    ).all()
    return {version: count for version, count in rows}


def run_reclassify(
    session: Session,
    classifier: ClassifierClient,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int | None = None,
    dry_run: bool = False,
    max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
) -> ReclassifyStats:
    """Reclasifica los artículos de otra versión con el modelo que se sirve hoy."""
    if batch_size < 1:
        raise ValueError("batch_size debe ser ≥ 1")
    stats = ReclassifyStats()

    target_version = classifier.current_model_version()
    if not target_version:
        stats.aborted = (
            "el clasificador no informa su versión (¿servicio caído o CLASSIFIER_MODE=off?)"
        )
        return stats
    stats.target_version = target_version

    stats.outdated_by_version = count_outdated(session, target_version)
    stats.pending = session.scalar(
        select(func.count()).where(
            Article.classification_status == ARTICLE_CLASSIFICATION_PENDING
        )
    ) or 0

    if dry_run:
        stats.remaining = stats.outdated
        return stats

    consecutive_failures = 0
    last_id = ""
    while limit is None or stats.processed < limit:
        size = batch_size if limit is None else min(batch_size, limit - stats.processed)
        # Paginación por id (no por OFFSET): los que fallan siguen siendo de
        # otra versión y, con OFFSET, se volverían a pedir en el mismo run.
        batch = session.scalars(
            select(Article)
            .options(selectinload(Article.story))
            .where(_outdated_condition(target_version), Article.id > last_id)
            .order_by(Article.id)
            .limit(size)
        ).all()
        if not batch:
            break

        changed_in_batch = 0
        for article in batch:
            last_id = article.id
            stats.processed += 1
            result = classifier.classify(article.title, article.text)

            if result is None:
                stats.failures += 1
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    stats.aborted = (
                        f"{consecutive_failures} fallos seguidos del clasificador; "
                        "se detiene (vuelve a correr el comando para retomar)"
                    )
                    break
                continue
            consecutive_failures = 0

            if result.model_version != target_version:
                stats.processed -= 1  # no se guardó: queda para la próxima corrida
                stats.aborted = (
                    f"el servicio cambió de modelo a mitad de corrida "
                    f"({target_version} → {result.model_version}); vuelve a correr el comando"
                )
                break

            old_label = article.classification_label
            article.classification_label = result.label
            article.classification_probabilities = result.probabilities
            article.classification_model_version = result.model_version
            stats.reclassified += 1
            if old_label != result.label:
                stats.label_changed += 1
                changed_in_batch += 1
                stats.transitions[(old_label, result.label)] += 1
            if article.story.representative_article_id == article.id:
                # Lo que muestra la story (la clasificación del representativo) cambió
                article.story.updated_at = utcnow()

        session.commit()
        stats.batches += 1
        logger.info(
            "Lote %d: %d artículos, %d cambiaron de clase (%d/%d)",
            stats.batches, len(batch), changed_in_batch, stats.processed, stats.outdated,
        )
        if stats.aborted:
            break

    stats.remaining = sum(count_outdated(session, target_version).values())
    if stats.aborted:
        logger.error("Reclasificación detenida: %s", stats.aborted)
    logger.info("Reclasificación terminada: %s", stats.summary())
    return stats
