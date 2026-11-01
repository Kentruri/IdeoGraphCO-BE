"""Fixtures: BD SQLite temporal + app FastAPI + seeds.

El env se fija ANTES de importar src (get_settings está cacheado).
"""

import os
import tempfile
from datetime import UTC, datetime, timedelta

_TMP_DIR = tempfile.mkdtemp(prefix="ideographco-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR}/test.db"
os.environ["CLASSIFIER_MODE"] = "mock"
os.environ["GEMINI_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.app import app  # noqa: E402
from src.core.ids import article_id, new_story_id, slugify  # noqa: E402
from src.core.schema import (  # noqa: E402
    ARTICLE_CLASSIFICATION_DONE,
    ARTICLE_CLASSIFICATION_PENDING,
    IDEOLOGY_CLASSES,
    STORY_STATUS_INGESTED,
    STORY_STATUS_PUBLISHED,
)
from src.core.text import normalize_for_search  # noqa: E402
from src.db.models import Article, Story  # noqa: E402
from src.db.session import Base, get_engine, get_session_factory, init_db  # noqa: E402

BASE_DATE = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _uniform_probabilities(dominant: str) -> dict[str, float]:
    probabilities = {cls: 0.06 for cls in IDEOLOGY_CLASSES}
    probabilities[dominant] = round(1.0 - 0.06 * (len(IDEOLOGY_CLASSES) - 1), 4)
    return probabilities


def make_story(
    session,
    index: int,
    *,
    source_category: str = "nacional",
    source_name: str = "eltiempo",
    classified: bool = True,
    synthesized: bool = False,
    extra_sources: int = 0,
    title: str | None = None,
) -> Story:
    story_title = title or f"Noticia de prueba número {index}"
    url = f"https://example.com/{source_name}/{index}"
    art_id = article_id(url)
    published = BASE_DATE + timedelta(hours=index)

    story = Story(
        id=new_story_id(),
        slug=slugify(story_title, art_id),
        title=story_title,
        status=STORY_STATUS_PUBLISHED if classified else STORY_STATUS_INGESTED,
        published_at=published,
        representative_article_id=art_id,
        keywords=["prueba", "colombia"],
    )
    if synthesized:
        story.synthesized_title = f"Síntesis: {story_title}"
        story.synthesized_body = ["Párrafo uno.", "Párrafo dos."]
        story.synthesized_model = "gemini-test"
        story.synthesized_at = published

    def _make_article(suffix: str, art_url: str, offset_hours: int) -> Article:
        aid = article_id(art_url)
        article = Article(
            id=aid,
            story_id=story.id,
            url=art_url,
            source_name=source_name if suffix == "rep" else f"medio-{suffix}",
            source_category=source_category,
            title=story_title if suffix == "rep" else f"{story_title} ({suffix})",
            subtitle="Un subtítulo de prueba",
            author="Autora Prueba",
            search_text=normalize_for_search(
                f"{story_title} Un subtítulo de prueba {source_name}"
            ),
            text="palabra " * 300,
            snippet="Un snippet de prueba…",
            content_hash=f"hash-{aid}",
            reading_time_minutes=2,
            image_url=None,
            published_at=published + timedelta(hours=offset_hours),
            scraped_at=published,
        )
        if classified:
            article.classification_status = ARTICLE_CLASSIFICATION_DONE
            article.classification_label = "populismo"
            article.classification_probabilities = _uniform_probabilities("populismo")
            article.classification_model_version = "test-1.0"
        else:
            article.classification_status = ARTICLE_CLASSIFICATION_PENDING
        return article

    story.articles.append(_make_article("rep", url, 0))
    for extra in range(extra_sources):
        story.articles.append(
            _make_article(f"extra{extra}", f"{url}/alt/{extra}", extra + 1)
        )

    session.add(story)
    session.commit()
    return story


@pytest.fixture()
def db_session():
    init_db()
    session = get_session_factory()()
    yield session
    session.close()
    # Limpiar entre tests
    Base.metadata.drop_all(bind=get_engine())


@pytest.fixture()
def client(db_session):
    with TestClient(app) as test_client:
        yield test_client
