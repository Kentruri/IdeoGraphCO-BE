import httpx

from src.classifier.client import HttpClassifierClient, MockClassifierClient
from src.core.ids import article_id, slugify
from src.core.schema import IDEOLOGY_CLASSES
from src.core.text import escape_like, normalize_for_search
from src.ingestion.cleaner import clean_article_text
from src.ingestion.pipeline import make_snippet, reading_time_minutes


def test_article_id_deterministic():
    url = "https://www.eltiempo.com/politica/nota-1"
    assert article_id(url) == article_id(url)
    assert len(article_id(url)) == 16


def test_slugify():
    slug = slugify("La reforma pensional: ¿qué sigue?", "a1b2c3d4e5")
    assert slug == "la-reforma-pensional-que-sigue-a1b2c3d4"
    assert slugify("", "abc123") == "noticia-abc123"


def test_mock_classifier_valid_distribution():
    classifier = MockClassifierClient()
    result = classifier.classify("Título", "Texto de prueba sobre política " * 20)
    assert result is not None
    assert set(result.probabilities) == set(IDEOLOGY_CLASSES)
    assert abs(sum(result.probabilities.values()) - 1.0) < 1e-6
    assert result.label == max(result.probabilities, key=result.probabilities.get)
    # Determinista: mismo texto → misma salida
    again = classifier.classify("Título", "Texto de prueba sobre política " * 20)
    assert again.probabilities == result.probabilities


def test_make_snippet_word_boundary():
    text = "palabra " * 100
    snippet = make_snippet(text, max_chars=50)
    assert len(snippet) <= 51  # 50 + elipsis
    assert snippet.endswith("…")
    assert make_snippet("corto") == "corto"


def test_reading_time():
    assert reading_time_minutes("palabra " * 400) == 2
    assert reading_time_minutes("corto") == 1


def test_cleaner_removes_cta():
    text = (
        "El Congreso aprobó la reforma en segundo debate. " * 20
        + "\n\nLea también\n\nOtra noticia sobre farándula\n"
    )
    cleaned = clean_article_text(text)
    assert "Lea también" not in cleaned
    assert "Congreso" in cleaned


def test_cleaner_start_marker_mid_article_preserved():
    # Una línea suelta "Entrevista" en medio del cuerpo NO debe borrar
    # la primera mitad del artículo.
    body_start = "Primer párrafo con contenido importante del artículo. " * 10
    text = body_start + "\nEntrevista\n" + "Segundo bloque del artículo. " * 10
    cleaned = clean_article_text(text)
    assert "Primer párrafo" in cleaned


def test_cleaner_start_marker_at_top_removed():
    text = "El Tiempo\nNoticia\n" + "Cuerpo real del artículo político. " * 20
    cleaned = clean_article_text(text)
    assert not cleaned.startswith("El Tiempo")
    assert "Cuerpo real" in cleaned


def test_normalize_for_search():
    assert normalize_for_search("Última HORA  del Congreso") == "ultima hora del congreso"
    assert escape_like("50%_a") == "50\\%\\_a"


def test_http_classifier_rejects_invalid_label(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "label": "neutral",  # fuera de las 8 clases
                "probabilities": {cls: 0.125 for cls in IDEOLOGY_CLASSES},
                "model_version": "x",
            },
            request=request,
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = HttpClassifierClient("http://clasificador.test")
    assert client.classify("t", "texto") is None
