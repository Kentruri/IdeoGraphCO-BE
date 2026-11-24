"""Reclasificación por cambio de modelo (scripts/reclasificar.py) con el clasificador mock.

Las stories de `make_story` quedan clasificadas con la versión "test-1.0" y
la clase "populismo"; el mock responde con "mock-0.1.0", así que todo lo
sembrado está desactualizado frente al modelo "servido".
"""

import importlib.util
from pathlib import Path

import httpx
from sqlalchemy import select

from src.classifier.client import (
    ClassificationResult,
    HttpClassifierClient,
    MockClassifierClient,
    NullClassifierClient,
)
from src.core.schema import ARTICLE_CLASSIFICATION_DONE, ARTICLE_CLASSIFICATION_PENDING
from src.db.models import Article
from src.ingestion.reclassify import count_outdated, run_reclassify
from tests.conftest import make_story

MOCK_VERSION = MockClassifierClient.MODEL_VERSION
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "reclasificar.py"


class SpyClassifier(MockClassifierClient):
    """El mock, contando llamadas; puede fallar o cambiar de versión a pedido."""

    def __init__(self, fail_titles=(), switch_version_after=None):
        self.calls: list[str] = []
        self._fail_titles = set(fail_titles)
        self._switch_after = switch_version_after

    def classify(self, title, text):
        self.calls.append(title)
        if title in self._fail_titles:
            return None
        result = super().classify(title, text)
        if self._switch_after is not None and len(self.calls) > self._switch_after:
            return ClassificationResult(result.label, result.probabilities, "otro-modelo")
        return result


def _articles(session) -> list[Article]:
    session.expire_all()
    return session.scalars(select(Article).order_by(Article.id)).all()


def _load_script():
    spec = importlib.util.spec_from_file_location("reclasificar", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dry_run_counts_without_classifying(db_session):
    for i in range(3):
        make_story(db_session, i, extra_sources=1)  # 6 artículos en test-1.0
    current = make_story(db_session, 10)
    current.articles[0].classification_model_version = MOCK_VERSION  # ya al día
    make_story(db_session, 11, classified=False)  # pending
    db_session.commit()

    spy = SpyClassifier()
    stats = run_reclassify(db_session, spy, dry_run=True)

    assert spy.calls == []
    assert stats.target_version == MOCK_VERSION
    assert stats.outdated_by_version == {"test-1.0": 6}
    assert stats.pending == 1
    assert {a.classification_model_version for a in _articles(db_session)} == {
        "test-1.0", MOCK_VERSION, None
    }


def test_reclassifies_outdated_and_counts_label_changes(db_session):
    for i in range(4):
        make_story(db_session, i, extra_sources=1)
    expected = {
        a.id: MockClassifierClient().classify(a.title, a.text) for a in _articles(db_session)
    }

    stats = run_reclassify(db_session, MockClassifierClient(), batch_size=3)

    assert stats.reclassified == 8
    assert stats.batches == 3  # 3 + 3 + 2
    assert stats.remaining == 0
    assert stats.failures == 0 and stats.aborted is None
    changed = sum(1 for r in expected.values() if r.label != "populismo")
    assert stats.label_changed == changed
    assert sum(stats.transitions.values()) == changed
    assert all(old == "populismo" for old, _ in stats.transitions)
    for article in _articles(db_session):
        assert article.classification_status == ARTICLE_CLASSIFICATION_DONE
        assert article.classification_model_version == MOCK_VERSION
        assert article.classification_label == expected[article.id].label
        assert article.classification_probabilities == expected[article.id].probabilities


def test_second_run_has_nothing_to_do(db_session):
    for i in range(3):
        make_story(db_session, i)
    run_reclassify(db_session, MockClassifierClient())

    spy = SpyClassifier()
    stats = run_reclassify(db_session, spy)
    assert spy.calls == []
    assert stats.outdated == 0 and stats.reclassified == 0


def test_limit_leaves_the_rest_for_the_next_run(db_session):
    for i in range(7):
        make_story(db_session, i)

    spy = SpyClassifier()
    first = run_reclassify(db_session, spy, batch_size=2, limit=3)
    assert first.reclassified == 3
    assert first.batches == 2  # 2 + 1, sin pasarse del límite
    assert first.remaining == 4
    assert count_outdated(db_session, MOCK_VERSION) == {"test-1.0": 4}

    second = run_reclassify(db_session, spy, batch_size=2)
    assert second.reclassified == 4 and second.remaining == 0
    assert len(spy.calls) == 7 and len(set(spy.calls)) == 7  # nadie se clasificó dos veces


def test_failures_keep_old_classification_and_story_stays_visible(db_session, client):
    stories = [make_story(db_session, i) for i in range(3)]
    failing = stories[1]

    stats = run_reclassify(db_session, SpyClassifier(fail_titles={failing.title}))

    assert stats.failures == 1 and stats.reclassified == 2 and stats.remaining == 1
    assert stats.aborted is None
    db_session.expire_all()
    kept = failing.articles[0]
    assert kept.classification_status == ARTICLE_CLASSIFICATION_DONE
    assert kept.classification_label == "populismo"
    assert kept.classification_model_version == "test-1.0"
    # La story del que falló sigue publicada con su clasificación anterior
    detail = client.get(f"/api/v1/stories/{failing.slug}").json()
    assert detail["classification"]["model_version"] == "test-1.0"
    assert client.get("/api/v1/stories").json()["total_items"] == 3

    # La siguiente corrida retoma solo el que falló
    spy = SpyClassifier()
    retry = run_reclassify(db_session, spy)
    assert spy.calls == [failing.title]
    assert retry.reclassified == 1 and retry.remaining == 0


def test_consecutive_failures_abort_the_run(db_session):
    stories = [make_story(db_session, i) for i in range(6)]
    spy = SpyClassifier(fail_titles={s.title for s in stories})

    stats = run_reclassify(db_session, spy, max_consecutive_failures=3)

    assert len(spy.calls) == 3
    assert stats.aborted and "fallos seguidos" in stats.aborted
    assert stats.remaining == 6


def test_model_switch_mid_run_stops_without_saving_the_other_version(db_session):
    for i in range(5):
        make_story(db_session, i)

    stats = run_reclassify(db_session, SpyClassifier(switch_version_after=2), batch_size=10)

    assert stats.aborted and "otro-modelo" in stats.aborted
    assert stats.reclassified == 2
    versions = [a.classification_model_version for a in _articles(db_session)]
    assert "otro-modelo" not in versions
    assert versions.count(MOCK_VERSION) == 2 and versions.count("test-1.0") == 3


def test_classifier_without_version_changes_nothing(db_session):
    make_story(db_session, 0)
    stats = run_reclassify(db_session, NullClassifierClient())
    assert stats.target_version is None and stats.aborted
    assert _articles(db_session)[0].classification_model_version == "test-1.0"


def test_pending_articles_are_left_to_the_ingestion(db_session):
    make_story(db_session, 0, classified=False)
    spy = SpyClassifier()
    stats = run_reclassify(db_session, spy)
    assert spy.calls == [] and stats.pending == 1
    article = _articles(db_session)[0]
    assert article.classification_status == ARTICLE_CLASSIFICATION_PENDING


def test_published_story_shows_the_new_classification(db_session, client):
    story = make_story(db_session, 0, extra_sources=2)
    rep = story.articles[0]
    expected = MockClassifierClient().classify(rep.title, rep.text)

    run_reclassify(db_session, MockClassifierClient())

    item = client.get("/api/v1/stories").json()["items"][0]
    assert item["slug"] == story.slug
    assert item["classification"]["model_version"] == MOCK_VERSION
    assert item["classification"]["label"] == expected.label
    assert item["classification"]["probabilities"] == expected.probabilities

    detail = client.get(f"/api/v1/stories/{story.slug}").json()
    assert detail["classification"]["label"] == expected.label
    assert {s["classification"]["model_version"] for s in detail["sources"]} == {MOCK_VERSION}


def test_http_client_reads_version_from_health(monkeypatch):
    def fake_get(url, timeout=None):
        assert url == "http://clasificador.test/health"
        return httpx.Response(
            200,
            json={"status": "ok", "model_version": "beto-final"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    assert HttpClassifierClient("http://clasificador.test/").current_model_version() == "beto-final"


def test_http_client_version_is_none_when_service_is_down(monkeypatch):
    def fake_get(url, timeout=None):
        raise httpx.ConnectError("conexión rechazada")

    monkeypatch.setattr(httpx, "get", fake_get)
    assert HttpClassifierClient("http://clasificador.test").current_model_version() is None


def test_cli_dry_run_then_real_run(db_session, capsys):
    for i in range(3):
        make_story(db_session, i)
    script = _load_script()

    assert script.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"Modelo servido: {MOCK_VERSION}" in out
    assert "Clasificados con otra versión: 3 (test-1.0=3)" in out
    assert "[dry-run] Se reclasificarían 3 artículos" in out
    assert count_outdated(db_session, MOCK_VERSION) == {"test-1.0": 3}

    assert script.main(["--batch-size", "2"]) == 0
    out = capsys.readouterr().out
    assert "reclasificados=3" in out and "restantes=0" in out
    assert "Cambiaron de clase" in out
    assert count_outdated(db_session, MOCK_VERSION) == {}
