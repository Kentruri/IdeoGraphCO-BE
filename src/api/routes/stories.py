"""Endpoints públicos de lectura (contrato v2, ver CONTRACT.md)."""

import logging
import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, aliased, selectinload

from src.adapters.story_adapter import to_detail, to_list_item
from src.api.deps import get_db
from src.core.schema import SOURCE_CATEGORIES, STORY_STATUS_PUBLISHED
from src.core.text import escape_like, normalize_for_search
from src.db.models import Article, Story
from src.schemas.dto import PaginatedStoriesDTO, StoryDetailDTO, StoryListItemDTO

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["stories"])

MAX_PAGE_SIZE = 50


def _representative_join_condition():
    """Condición para unir la story con su artículo representativo."""
    return Article.id == Story.representative_article_id


@router.get("/stories", response_model=PaginatedStoriesDTO)
def list_stories(
    db: Annotated[Session, Depends(get_db)],
    page: int = Query(1, ge=1),
    page_size: int = Query(9, ge=1, le=MAX_PAGE_SIZE),
    source_category: str | None = Query(None),
    q: str | None = Query(None, max_length=200),
) -> PaginatedStoriesDTO:
    # Cadena vacía (?source_category=) se ignora en vez de responder 400
    source_category = source_category or None
    if source_category is not None and source_category not in SOURCE_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"source_category inválida: {source_category}")

    base = (
        select(Story)
        .join(Article, _representative_join_condition())
        .where(Story.status == STORY_STATUS_PUBLISHED)
    )

    if source_category:
        base = base.where(Article.source_category == source_category)

    if q and q.strip():
        # Misma normalización que en la ingesta (articles.search_text):
        # insensible a tildes/mayúsculas e idéntica en SQLite y Postgres.
        # escape_like evita que % y _ del usuario actúen como comodines.
        needle = f"%{escape_like(normalize_for_search(q))}%"
        # Alias para la subconsulta: sin él, el Article del JOIN exterior se
        # auto-correlaciona y el EXISTS queda sin FROM.
        any_article = aliased(Article)
        base = base.where(
            exists(
                select(any_article.id).where(
                    any_article.story_id == Story.id,
                    any_article.search_text.like(needle, escape="\\"),
                )
            )
        )

    total_items = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    total_pages = max(1, math.ceil(total_items / page_size))
    page = min(page, total_pages)

    stories = (
        db.scalars(
            base.options(selectinload(Story.articles))
            .order_by(Story.published_at.desc(), Story.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .unique()
        .all()
    )

    # Una story con datos corruptos (invariante roto) se loguea y se omite:
    # no debe tumbar la página entera con un 500.
    items: list[StoryListItemDTO] = []
    for story in stories:
        try:
            items.append(to_list_item(story))
        except ValueError:
            logger.exception("Story corrupta omitida del listado: %s", story.slug)

    return PaginatedStoriesDTO(
        items=items,
        page=page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
    )


@router.get("/stories/{slug}", response_model=StoryDetailDTO)
def story_detail(slug: str, db: Annotated[Session, Depends(get_db)]) -> StoryDetailDTO:
    story = db.scalar(
        select(Story)
        .options(selectinload(Story.articles))
        .where(Story.slug == slug, Story.status == STORY_STATUS_PUBLISHED)
    )
    if story is None:
        raise HTTPException(status_code=404, detail=f"No existe una noticia con slug: {slug}")
    return to_detail(story)
