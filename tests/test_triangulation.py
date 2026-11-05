"""Triangulación: agrupamiento, validación de la síntesis y corrida completa."""

import json
from datetime import UTC, datetime, timedelta

from src.classifier.client import MockClassifierClient
from src.config import get_settings
from src.core.ids import article_id, new_story_id, slugify
from src.core.schema import ARTICLE_CLASSIFICATION_DONE, STORY_STATUS_PUBLISHED
from src.core.text import normalize_for_search
from src.db.models import Article, Story
from src.triangulation.clustering import ArticleView, cluster_articles
from src.triangulation.pipeline import run_triangulation
from src.triangulation.synthesis import parse_synthesis
from tests.fake_gemini import fake_gemini_client

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)

ATENTADO = (
    "Una volqueta con explosivos estalló junto a la subestación de Policía de El Estrecho, "
    "en El Patía, Cauca, sobre la vía Panamericana. Un joven murió y dos mujeres resultaron "
    "heridas. La Fuerza Pública sospecha del frente Carlos Patiño."
)
REFORMA = (
    "La Cámara de Representantes aprobó en primer debate la reforma pensional con cambios "
    "en el pilar contributivo, mientras la oposición anunció una ponencia negativa en el "
    "Senado y Fedesarrollo pidió revisar el costo fiscal."
)


def view(i: str, source: str, text: str, hours: float = 0, story: str | None = None) -> ArticleView:
    return ArticleView(
        id=i,
        story_id=story or f"st-{i}",
        source_name=source,
        published_at=NOW + timedelta(hours=hours),
        title=text[:80],
        subtitle=None,
        text=text,
    )


# --- Agrupamiento ---------------------------------------------------------


def test_cluster_groups_same_event_from_different_outlets():
    groups = cluster_articles(
        [
            view("a", "elcolombiano", ATENTADO),
            view("b", "proclamadelcauca", ATENTADO.replace("joven", "barbero"), 1),
            view("c", "lasillavacia", ATENTADO + " El presidente condenó el ataque.", 3),
            view("d", "eltiempo", REFORMA, 2),
        ]
    )
    assert len(groups) == 1
    assert set(groups[0].article_ids) == {"a", "b", "c"}
    # Orden cronológico: el primero en reportar va al frente.
    assert groups[0].article_ids[0] == "a"


def test_cluster_never_joins_two_articles_of_the_same_outlet():
    groups = cluster_articles(
        [view("a", "eltiempo", ATENTADO), view("b", "eltiempo", ATENTADO, 1)]
    )
    assert groups == []


def test_cluster_respects_the_time_window():
    groups = cluster_articles(
        [view("a", "eltiempo", ATENTADO), view("b", "semana", ATENTADO, 24 * 10)],
        window_hours=48,
    )
    assert groups == []


def test_cluster_keeps_existing_groups_together():
    groups = cluster_articles(
        [
            view("a", "eltiempo", ATENTADO, story="st-x"),
            view("b", "semana", REFORMA, story="st-x"),
        ]
    )
    assert len(groups) == 1 and set(groups[0].article_ids) == {"a", "b"}


# --- Validación de la síntesis ---------------------------------------------


def synthesis_json(sources: list[int], words: int = 320, title: str = "Atentado en El Patía") -> str:
    paragraph = " ".join(["palabra"] * (words // 4))
    return json.dumps(
        {"same_event_sources": sources, "title": title, "paragraphs": [paragraph] * 4}
    )


def test_parse_synthesis_accepts_a_complete_answer_and_removes_dashes():
    result = parse_synthesis(
        synthesis_json([1, 2, 3], title="Atentado — El Patía"), "gemini-test", 3, 3
    )
    assert result is not None
    assert result.title == "Atentado, El Patía"
    assert result.used_sources == [0, 1, 2]


def test_parse_synthesis_rejects_too_few_confirmed_sources():
    assert parse_synthesis(synthesis_json([1, 3]), "gemini-test", 3, 3) is None


def test_parse_synthesis_rejects_short_or_broken_answers():
    assert parse_synthesis(synthesis_json([1, 2, 3], words=40), "gemini-test", 3, 3) is None
    assert parse_synthesis("no es json", "gemini-test", 3, 3) is None
    assert parse_synthesis(None, "gemini-test", 3, 3) is None


# --- Corrida completa -------------------------------------------------------


def seed_article(session, index: int, source: str, text: str, hours: float) -> Article:
    url = f"https://example.com/{source}/{index}"
    aid = article_id(url)
    title = text[:70]
    published = datetime.now(UTC) - timedelta(hours=10) + timedelta(hours=hours)
    story = Story(
        id=new_story_id(),
        slug=slugify(title, aid),
        title=title,
        status=STORY_STATUS_PUBLISHED,
        published_at=published,
        representative_article_id=aid,
    )
    article = Article(
        id=aid,
        story_id=story.id,
        url=url,
        source_name=source,
        source_category="nacional",
        title=title,
        subtitle=None,
        author=None,
        search_text=normalize_for_search(title),
        text=text,
        snippet=text[:320],
        content_hash=f"hash-{aid}",
        reading_time_minutes=1,
        published_at=published,
        classification_status=ARTICLE_CLASSIFICATION_DONE,
        classification_label="conservadurismo",
        classification_probabilities={"conservadurismo": 0.9, "progresismo": 0.1},
        classification_model_version="test-1.0",
    )
    story.articles.append(article)
    session.add(story)
    session.commit()
    return article


def test_run_triangulation_merges_and_synthesizes(db_session, client):
    first = seed_article(db_session, 1, "elcolombiano", ATENTADO, 0)
    seed_article(db_session, 2, "proclamadelcauca", ATENTADO.replace("joven", "barbero"), 1)
    seed_article(db_session, 3, "lasillavacia", ATENTADO + " El presidente condenó el ataque.", 2)
    seed_article(db_session, 4, "eltiempo", REFORMA, 1)

    stats, groups = run_triangulation(
        db_session,
        get_settings(),
        MockClassifierClient(),
        gemini_client=fake_gemini_client(synthesis_json([1, 2, 3])),
    )
    assert stats.groups == 1 and stats.stories_merged == 1 and stats.synthesized == 1

    story = db_session.get(Story, first.story_id)
    assert len(story.articles) == 3
    assert story.representative_article_id == first.id
    assert story.synthesized_body and story.synthesized_classification["label"]

    detail = client.get(f"/api/v1/stories/{story.slug}").json()
    assert detail["sources_count"] == 3
    assert detail["synthesized"]["classification"]["label"]
    assert "3 medios" in detail["synthesized"]["disclaimer"]
    # La noticia clasificada sigue siendo la original.
    assert detail["classification"]["label"] == "conservadurismo"
    assert client.get("/api/v1/stories").json()["total_items"] == 2


def test_run_triangulation_returns_unconfirmed_sources_to_their_own_story(db_session):
    first = seed_article(db_session, 1, "elcolombiano", ATENTADO, 0)
    seed_article(db_session, 2, "proclamadelcauca", ATENTADO.replace("joven", "barbero"), 1)
    seed_article(db_session, 3, "lasillavacia", ATENTADO + " El presidente condenó.", 2)
    seed_article(db_session, 4, "semana", ATENTADO + " Hubo otro ataque en Yotoco.", 3)

    stats, _ = run_triangulation(
        db_session,
        get_settings(),
        MockClassifierClient(),
        gemini_client=fake_gemini_client(synthesis_json([1, 2, 3])),
    )
    assert stats.synthesized == 1 and stats.detached == 1
    story = db_session.get(Story, first.story_id)
    assert len(story.articles) == 3
    assert db_session.query(Story).count() == 2


def test_run_triangulation_does_not_merge_a_pair(db_session):
    seed_article(db_session, 1, "elcolombiano", ATENTADO, 0)
    seed_article(db_session, 2, "proclamadelcauca", ATENTADO, 1)

    stats, _ = run_triangulation(db_session, get_settings(), MockClassifierClient())
    assert stats.groups == 0 and stats.stories_merged == 0
    assert db_session.query(Story).count() == 2


def test_run_triangulation_without_key_groups_but_does_not_synthesize(db_session):
    seed_article(db_session, 1, "elcolombiano", ATENTADO, 0)
    seed_article(db_session, 2, "proclamadelcauca", ATENTADO, 1)
    seed_article(db_session, 3, "lasillavacia", ATENTADO, 2)

    stats, _ = run_triangulation(db_session, get_settings(), MockClassifierClient())
    assert stats.stories_merged == 1 and stats.synthesized == 0


# --- Búsqueda dirigida -------------------------------------------------------

from src.triangulation.search import Candidate, StoryQuery, match_coverage, slug_words  # noqa: E402


def test_slug_words_reads_the_headline_from_the_url():
    url = "https://www.elcolombiano.com/colombia/atentado-con-explosivos-en-el-patia-cauca-AB12345"
    assert slug_words(url) == "atentado con explosivos en el patia cauca ab12345"
    assert slug_words("https://www.eltiempo.com/politica/") == ""


def candidate(url: str, source: str, title: str, hours: float | None = 1) -> Candidate:
    return Candidate(
        url=url,
        source_name=source,
        category="nacional",
        title=title,
        published_at=None if hours is None else NOW + timedelta(hours=hours),
    )


def test_match_coverage_finds_other_outlets_of_the_same_event():
    query = StoryQuery(
        story_id="st-1",
        text="Atentado con volqueta bomba contra la subestación de Policía de El Estrecho, Patía",
        published_at=NOW,
        outlets=frozenset({"elcolombiano"}),
    )
    matches = match_coverage(
        [query],
        [
            candidate("https://a/1", "lasillavacia", "Volqueta bomba en El Estrecho: atentado contra la Policía en Patía"),
            candidate("https://a/2", "elcolombiano", "Atentado en El Estrecho, Patía, contra la Policía"),
            candidate("https://a/3", "semana", "Senado aprobó la reforma pensional en último debate"),
            candidate("https://a/4", "eltiempo", "Atentado contra la Policía en El Estrecho, Patía", hours=24 * 10),
        ],
        threshold=0.2,
    )
    urls = [found.url for _story, found, _score in matches]
    # Otro medio, mismo hecho: sí. Mismo medio, otro tema o fuera de la ventana: no.
    assert urls == ["https://a/1"]


def test_existing_triangulated_story_keeps_its_slug_when_an_earlier_outlet_joins(db_session):
    first = seed_article(db_session, 1, "elcolombiano", ATENTADO, 1)
    seed_article(db_session, 2, "proclamadelcauca", ATENTADO.replace("joven", "barbero"), 2)
    seed_article(db_session, 3, "lasillavacia", ATENTADO + " El presidente condenó.", 3)
    run_triangulation(
        db_session,
        get_settings(),
        MockClassifierClient(),
        gemini_client=fake_gemini_client(synthesis_json([1, 2, 3])),
    )
    story = db_session.get(Story, first.story_id)
    slug = story.slug

    # Llega un cuarto medio que reportó antes que todos.
    earlier = seed_article(db_session, 4, "semana", ATENTADO + " Primer reporte.", 0)
    run_triangulation(
        db_session,
        get_settings(),
        MockClassifierClient(),
        gemini_client=fake_gemini_client(synthesis_json([1, 2, 3, 4])),
    )
    db_session.refresh(story)
    assert story.slug == slug
    assert len(story.articles) == 4
    assert story.representative_article_id == earlier.id
    assert story.synthesized_body  # se rehízo con las cuatro fuentes
