"""Pruebas del limpiador de texto de la ingesta.

`src/ingestion/cleaner.py` es una copia exacta de `src/scraper/cleaner.py` del
repositorio de ML (IdeoGraphCO): el modelo se entrenó con texto limpiado por
esa versión, así que la ingesta debe limpiar igual. Las pruebas de reglas se
portaron de `IdeoGraphCO/tests/test_dedup_and_quality.py`.
"""

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.classifier.client import MockClassifierClient
from src.config import get_settings
from src.db.models import Article
from src.ingestion import parser as parser_mod
from src.ingestion import pipeline as pipeline_mod
from src.ingestion.cleaner import clean_article_text

POLITICAL_TEXT = (
    "El Congreso de la República aprobó en segundo debate la reforma al sistema "
    "de salud, tras una sesión que se extendió por más de nueve horas. "
    "La ponencia mayoritaria defendió el nuevo esquema de aseguramiento público. "
    "\n\n"
    "El ministro del ramo celebró la votación y anunció que el Gobierno radicará "
    "los decretos reglamentarios antes de finalizar la legislatura en curso. "
    "La oposición, por su parte, anunció demandas ante la Corte Constitucional. "
    "\n\n"
    "Analistas consultados señalaron que el trámite aún debe superar la "
    "conciliación entre las cámaras, donde el Gobierno no tiene mayorías claras. "
    "El debate continuará la próxima semana con la discusión del articulado."
) * 3

# Texto con el que una SEGUNDA pasada del limpiador recorta el cuerpo. En la
# primera, la línea "Entrevista" cae más allá de la ventana de 300 caracteres
# del marcador de inicio (la alejan los pies de foto) y el lede se conserva;
# los pies de foto se borran después. En la segunda, "Entrevista" ya cae dentro
# de la ventana, se toma como marcador y se come el lede.
NON_IDEMPOTENT_TEXT = (
    "El Gobierno anunció que radicará la reforma laboral la próxima semana.\n"
    + "".join(
        f"Foto: Archivo particular de la Presidencia de la República {i}\n"
        for i in range(6)
    )
    + "Entrevista\n"
    + POLITICAL_TEXT
)


# ---------------------------------------------------------------------------
# Reglas del limpiador (portadas del repositorio de ML)
# ---------------------------------------------------------------------------


def test_cleaner_new_rules():
    dirty = (
        POLITICAL_TEXT
        + "\nSiga a EL PAÍS en Google Discover y no se pierda las últimas noticias"
        + "\nEscucha este artículo\nAudio generado con IA de Google"
        + "\nTemas recomendados:\nNoticias Destacadas"
        + "\n* Pulzo.com se escribe con Z"
        + "\nVer el trino: pic.twitter.com/oEuAb5UPiU"
        + "\nEscriba a denuncias@medio.com para más información."
    )
    cleaned = clean_article_text(dirty)
    for garbage in (
        "Google Discover", "Escucha este artículo", "generado con IA",
        "Temas recomendados", "Noticias Destacadas", "se escribe con Z",
        "pic.twitter.com", "@medio.com",
    ):
        assert garbage not in cleaned, garbage
    assert "Congreso" in cleaned


def test_cleaner_template_artifacts():
    """Reproductor de audio, etiqueta de sección, "Compartir:", firma
    institucional, CTAs con emoji y separadores tipográficos."""
    dirty = (
        POLITICAL_TEXT
        + "\nCompartir:"
        + "\n0:00\n/"
        + "\nPolítica"
        + "\nMinisterio del Interior"
        + "\n***"
        + "\n↩︎ -"
        + "\nExclusivo suscriptores"
        + "\n👉 Lea más sobre el Congreso y otras noticias del mundo político."
        + "\n✉️ Si tiene interés en más temas políticos escríbanos al correo."
    )
    cleaned = clean_article_text(dirty)
    for garbage in (
        "Compartir:", "0:00", "Ministerio del Interior", "***", "↩︎",
        "Exclusivo suscriptores", "👉", "✉️",
    ):
        assert garbage not in cleaned, garbage
    assert "Congreso" in cleaned


def test_cleaner_preserves_legitimate_content():
    """Los patrones nuevos NO deben tocar contenido editorial legítimo."""
    nota = "* El nombre de las mujeres gestantes fue cambiado para proteger su identidad."
    assert "mujeres gestantes" in clean_article_text(nota)

    credito = "*** Este proyecto se realizó con el apoyo de JournalismFund Europe."
    assert "JournalismFund" in clean_article_text(credito)

    prosa = (
        "El Ministerio del Interior radicó el proyecto de ley ante el Congreso "
        "de la República en la sesión de ayer."
    )
    assert "radicó el proyecto" in clean_article_text(prosa)

    cita = "El senador escribió: 🔴 Rechazo total a la reforma tributaria del Gobierno."
    assert "Rechazo total" in clean_article_text(cita)

    hora = "La plenaria se instaló a las 10:30 de la mañana con quórum decisorio."
    assert "quórum decisorio" in clean_article_text(hora)


def test_cleaner_related_articles_carousel():
    """Titular y firma de OTRA nota al inicio, "Cargando...", lede duplicado
    y firmas de notas relacionadas al final (contextoganadero)."""
    cuerpo = (
        "De acuerdo con el Dane, la exportación de carne creció en enero. "
        "El informe de Comercio Exterior detalla las cifras del sector."
    )
    dirty = (
        "Una nueva era en trazabilidad: Sinigán V6 entra en operación\n"
        "PorPedro Fonseca-16 de Abril 2026\n"
        "Cargando...\n"
        "Por - 04 de Marzo 2014\n"
        f"{cuerpo}\n"
        f"{cuerpo}\n"
        "Noticias Relacionadas\n"
        "PorMelanny Orozco-26 de Marzo 2026\n"
        "PorAngie Barbosa-25 de Marzo 2026\n"
    )
    cleaned = clean_article_text(dirty, source_name="contextoganadero")

    assert cleaned.startswith("De acuerdo con el Dane")
    assert "Sinigán V6" not in cleaned
    assert "Cargando" not in cleaned
    assert "Noticias Relacionadas" not in cleaned
    assert "Melanny Orozco" not in cleaned
    assert cleaned.count("De acuerdo con el Dane") == 1


def test_cleaner_subscription_footer_block():
    """Bloque de suscripción/comentarios al cierre (ambitojuridico)."""
    dirty = (
        "El Ministerio del Trabajo expidió el Decreto 992 de 2026, que reglamenta "
        "las medidas contra el acoso laboral en las entidades públicas.\n"
        "Gracias por leernos. Si le gusta estar informado, suscríbase.\n"
        "¡Bienvenido a nuestra sección de comentarios!\n"
        "Para unirte a la conversación, necesitas estar suscrito.\n"
        "Siga nuestro nuevo canal de WhatsApp.\n"
    )
    cleaned = clean_article_text(dirty, source_name="ambitojuridico")

    assert "Decreto 992" in cleaned
    for junk in ("Gracias por leernos", "sección de comentarios",
                 "estar suscrito", "WhatsApp"):
        assert junk not in cleaned, junk


def test_cleaner_keeps_prose_starting_with_por():
    """'Por otra parte' / 'Por eso' son prosa, no firmas."""
    for prosa in (
        "Por otra parte, la reglamentación establece que los empleadores deberán "
        "adoptar protocolos de prevención del acoso laboral.",
        "Por eso el Congreso aplazó el debate hasta la próxima legislatura.",
        "El decreto se firmó el 16 de abril de 2026 según la Presidencia.",
    ):
        assert clean_article_text(prosa).strip() == prosa, prosa[:40]


def test_cleaner_start_marker_as_first_line():
    """"Noticia" como PRIMERA línea también es marcador de inicio (El Tiempo)."""
    cleaned = clean_article_text("Noticia\n" + POLITICAL_TEXT)
    assert cleaned.startswith("El Congreso de la República")


def test_cleaner_is_not_idempotent():
    """Documenta por qué el limpiador debe correr UNA sola vez por artículo."""
    once = clean_article_text(NON_IDEMPOTENT_TEXT)
    twice = clean_article_text(once)
    assert "radicará la reforma laboral" in once
    assert "radicará la reforma laboral" not in twice


# ---------------------------------------------------------------------------
# La ingesta limpia UNA sola vez por artículo
# ---------------------------------------------------------------------------


def test_ingest_cleans_each_article_exactly_once(db_session, monkeypatch):
    source = "eltiempo"
    url = "https://www.eltiempo.com/politica/congreso/nota-de-prueba-123"
    authors = ["Redacción Política"]

    calls: list[str] = []

    def counting_clean(text, **kwargs):
        calls.append(text)
        return clean_article_text(text, **kwargs)

    metadata = SimpleNamespace(
        author="; ".join(authors),
        title="El Gobierno radicará la reforma laboral",
        description="Resumen",
        image=None,
        date="2026-11-01",
    )
    monkeypatch.setattr(parser_mod, "clean_article_text", counting_clean)
    monkeypatch.setattr(parser_mod.trafilatura, "fetch_url", lambda *a, **k: "<html/>")
    monkeypatch.setattr(parser_mod.trafilatura, "extract", lambda *a, **k: NON_IDEMPOTENT_TEXT)
    monkeypatch.setattr(parser_mod.trafilatura, "extract_metadata", lambda *a, **k: metadata)
    monkeypatch.setattr(
        pipeline_mod, "discover_candidate_urls",
        lambda *a, **k: [{"url": url, "rss_published_at": None}],
    )
    monkeypatch.setattr(pipeline_mod, "is_url_allowed", lambda _url: True)
    monkeypatch.setattr(pipeline_mod, "adaptive_sleep", lambda *_a: None)

    stats = pipeline_mod.run_ingest(
        db_session, get_settings(), MockClassifierClient(),
        source_names=[source], use_filter=False,
    )

    assert stats.errors == []
    assert calls == [NON_IDEMPOTENT_TEXT], "el limpiador debe correr una sola vez"

    expected = clean_article_text(NON_IDEMPOTENT_TEXT, source_name=source, authors=authors)
    article = db_session.query(Article).one()
    assert article.text == expected
    assert article.content_hash == hashlib.md5(expected.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Paridad con el repositorio de ML (solo si está disponible en local)
# ---------------------------------------------------------------------------

_ML_REPO = Path(
    os.environ.get(
        "IDEOGRAPHCO_ML_REPO",
        Path(__file__).resolve().parents[2] / "IdeoGraphCO",
    )
)
_ML_CLEANER = _ML_REPO / "src" / "scraper" / "cleaner.py"


@pytest.mark.skipif(not _ML_CLEANER.is_file(), reason="repositorio de ML no disponible")
def test_cleaner_matches_training_repo():
    be_cleaner = Path(__file__).resolve().parents[1] / "src" / "ingestion" / "cleaner.py"
    assert be_cleaner.read_text(encoding="utf-8") == _ML_CLEANER.read_text(encoding="utf-8"), (
        "src/ingestion/cleaner.py se desvió de IdeoGraphCO/src/scraper/cleaner.py; "
        "el modelo se entrenó con texto limpiado por la versión de ML"
    )
