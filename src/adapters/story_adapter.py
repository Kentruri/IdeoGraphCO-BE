"""ORM → DTO. Si el contrato cambia, se toca aquí y en schemas/dto.py."""

from src.core.schema import ARTICLE_CLASSIFICATION_DONE
from src.db.models import Article, Story
from src.explanation.basis import is_current
from src.ingestion.sources import source_display_name
from src.schemas.dto import (
    ClassificationDTO,
    ExplanationDTO,
    SourceArticleDTO,
    StoryDetailDTO,
    StoryListItemDTO,
    SynthesizedDTO,
)

SYNTH_DISCLAIMER_TEMPLATE = (
    "Texto sintético generado por IA a partir de {n} medios que cubrieron el mismo "
    "hecho, para ofrecer un encuadre más balanceado. Solo usa información de esas "
    "fuentes, pero puede contener errores: contraste con los originales."
)

# El modelo asigna la clase y la IA explica qué del texto la sustenta: el rótulo
# nombra las dos partes sin presentar el párrafo como el razonamiento interno
# del clasificador.
EXPLANATION_DISCLAIMER = (
    "Explicación redactada por IA a partir de la clase que asignó el modelo y "
    "del codebook."
)


def representative_article(story: Story) -> Article:
    """El artículo con published_at más antiguo (el primero en reportar)."""
    if story.representative_article_id:
        for article in story.articles:
            if article.id == story.representative_article_id:
                return article
    return min(story.articles, key=lambda a: a.published_at)


def classification_dto(article: Article) -> ClassificationDTO | None:
    if (
        article.classification_status != ARTICLE_CLASSIFICATION_DONE
        or not article.classification_label
        or not article.classification_probabilities
    ):
        return None
    return ClassificationDTO(
        label=article.classification_label,
        probabilities=article.classification_probabilities,
        model_version=article.classification_model_version or "unknown",
    )


def to_list_item(story: Story) -> StoryListItemDTO:
    rep = representative_article(story)
    classification = classification_dto(rep)
    if classification is None:
        # Una story publicada siempre tiene representativo clasificado
        # (invariante del pipeline). Si se rompe, mejor fallar ruidoso.
        raise ValueError(f"Story publicada sin clasificación: {story.slug}")
    return StoryListItemDTO(
        id=story.id,
        slug=story.slug,
        title=story.title,
        subtitle=rep.subtitle,
        source_name=rep.source_name,
        source_display_name=source_display_name(rep.source_name),
        source_category=rep.source_category,
        author=rep.author,
        published_at=story.published_at,
        image_url=rep.image_url,
        reading_time_minutes=rep.reading_time_minutes,
        classification=classification,
        sources_count=len(story.articles),
        has_synthesized=story.synthesized_body is not None,
    )


def to_source_dto(article: Article) -> SourceArticleDTO:
    return SourceArticleDTO(
        id=article.id,
        source_name=article.source_name,
        source_display_name=source_display_name(article.source_name),
        source_category=article.source_category,
        title=article.title,
        snippet=article.snippet,
        url=article.url,
        author=article.author,
        published_at=article.published_at,
        classification=classification_dto(article),
    )


def _synthesis_classification(story: Story) -> ClassificationDTO | None:
    data = story.synthesized_classification
    if not data or not data.get("label") or not data.get("probabilities"):
        return None
    return ClassificationDTO(
        label=data["label"],
        probabilities=data["probabilities"],
        model_version=data.get("model_version") or "unknown",
    )


def explanation_dto(article: Article) -> ExplanationDTO | None:
    """La explicación de la clasificación vigente; None si falta o quedó vieja."""
    if not is_current(article):
        return None
    basis = article.explanation_basis
    return ExplanationDTO(
        text=article.explanation_text,
        classes=[basis["label"], *([basis["second"]] if basis.get("second") else [])],
        model=article.explanation_model or "unknown",
        generated_at=article.explanation_generated_at,
        disclaimer=EXPLANATION_DISCLAIMER,
    )


def to_detail(story: Story) -> StoryDetailDTO:
    base = to_list_item(story)
    synthesized = None
    # Se exige body Y generated_at: una síntesis a medio escribir (body sin
    # timestamp) se trata como inexistente en vez de tumbar el detalle.
    if story.synthesized_body and story.synthesized_at:
        synthesized = SynthesizedDTO(
            title=story.synthesized_title or story.title,
            body=story.synthesized_body,
            model=story.synthesized_model or "unknown",
            generated_at=story.synthesized_at,
            disclaimer=SYNTH_DISCLAIMER_TEMPLATE.format(n=len(story.articles)),
            classification=_synthesis_classification(story),
        )
    return StoryDetailDTO(
        **base.model_dump(),
        keywords=story.keywords or [],
        explanation=explanation_dto(representative_article(story)),
        synthesized=synthesized,
        sources=[to_source_dto(article) for article in story.articles],
    )
