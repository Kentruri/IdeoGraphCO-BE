"""Explicación de la clasificación (src/explanation): prompt, validación, CLI, pipeline y API.

Ninguna prueba llama a un LLM: `claude -p` se reemplaza por respuestas fijas.
"""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

from src.adapters.story_adapter import EXPLANATION_DISCLAIMER
from src.config import get_settings
from src.explanation import codebook
from src.explanation.basis import classes_to_explain, is_current
from src.explanation.generator import (
    ClaudeCliExplainer,
    ExplainerUnavailable,
    Explanation,
    clean_explanation,
    copies_source,
    count_sentences,
    envelope_model,
    envelope_payload,
    parse_explanation,
    subprocess_env,
)
from src.explanation.pipeline import run_explanations, select_pending
from src.explanation.prompts import (
    EXPLANATION_SYSTEM_PROMPT,
    NO_SECOND,
    ExplanationRequest,
    explanation_user_prompt,
)
from tests.conftest import make_story

ARTICLE_TEXT = (
    "La derrota del partido de gobierno en las elecciones de mitad de mandato cambia el "
    "panorama para Colombia. Los analistas consultados advierten que la agenda comercial "
    "bilateral, la cooperación antidrogas y los aranceles podrían revisarse en el nuevo "
    "Congreso. El presidente estadounidense insistió en que sus decisiones seguirán firmes "
    "y que nadie le dirá cómo negociar con sus aliados del hemisferio."
)

PROBABILITIES = {
    "personalismo": 0.2705,
    "institucionalismo": 0.0186,
    "populismo": 0.0017,
    "doctrinarismo": 0.0052,
    "soberanismo": 0.0631,
    "globalismo": 0.595,
    "conservadurismo": 0.0358,
    "progresismo": 0.0101,
}

VALID_TWO_CLASSES = (
    "La nota presenta el resultado electoral en Estados Unidos como un asunto que pesa "
    "sobre la relación comercial y la cooperación de Colombia con ese país, un marco que "
    "corresponde al globalismo. El texto da por sentado que los acuerdos bilaterales y "
    "los aranceles son decisivos para la economía colombiana. La figura del presidente "
    "estadounidense ocupa buena parte del relato, con sus decisiones como eje de la "
    "historia, y eso se acerca al personalismo, aunque la nota no lo exalta."
)

VALID_ONE_CLASS = (
    "La nota presenta el resultado electoral en Estados Unidos como un asunto que pesa "
    "sobre la relación comercial y la cooperación de Colombia con ese país, un marco que "
    "corresponde al globalismo. El texto da por sentado que los acuerdos bilaterales y "
    "los aranceles son decisivos para la economía colombiana. La mayor parte del relato "
    "recoge análisis sobre la agenda que se revisaría en el nuevo Congreso."
)


def _request(second: str | None = "personalismo", text: str = ARTICLE_TEXT) -> ExplanationRequest:
    return ExplanationRequest(
        title="Trump perdió el Congreso y Colombia podría terminar pagando su derrota",
        text=text,
        probabilities=PROBABILITIES,
        label="globalismo",
        second=second,
    )


def _payload(text: str = VALID_TWO_CLASSES, predicted: str = "alto", second: str = "medio"):
    return {"explanation": text, "support_predicted": predicted, "support_second": second}


# ---------------------------------------------------------------------------
# Codebook y prompts
# ---------------------------------------------------------------------------

_ML_CODEBOOK = (
    Path(
        os.environ.get(
            "IDEOGRAPHCO_ML_REPO", Path(__file__).resolve().parents[2] / "IdeoGraphCO"
        )
    )
    / "src"
    / "agents"
    / "silver"
    / "codebook.py"
)


@pytest.mark.skipif(not _ML_CODEBOOK.is_file(), reason="repositorio de ML no disponible")
def test_codebook_matches_training_repo():
    spec = importlib.util.spec_from_file_location("ml_codebook", _ML_CODEBOOK)
    ml_codebook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ml_codebook)
    for name in (
        "SCALE_LEVELS",
        "AXIS_DEFINITIONS",
        "CALIBRATION_RULES",
        "SUSTANCIA_RULES",
        "TONO_RULES",
        "MACRO_RULES",
    ):
        assert getattr(codebook, name) == getattr(ml_codebook, name), (
            f"src/explanation/codebook.py::{name} se desvió del codebook de IdeoGraphCO; "
            "vuelve a copiarlo"
        )


def test_system_prompt_has_the_eight_classes_and_the_rules():
    for name in codebook.AXIS_DEFINITIONS:
        assert f"### {name}\n" in EXPLANATION_SYSTEM_PROMPT
    prompt = " ".join(EXPLANATION_SYSTEM_PROMPT.split())
    assert "no conoces su razonamiento" in prompt
    assert "Nunca uses la palabra «confianza»" in prompt
    assert "dilo con claridad en vez de forzar una justificación" in prompt
    # El JSON de salida del juez no aplica aquí.
    assert "Responde SOLO con un objeto JSON válido" not in prompt


def test_classes_to_explain_covers_the_second_class_from_the_threshold():
    assert classes_to_explain("globalismo", PROBABILITIES, 0.20) == ("globalismo", "personalismo")
    assert classes_to_explain("globalismo", PROBABILITIES, 0.2705) == (
        "globalismo",
        "personalismo",
    )
    assert classes_to_explain("globalismo", PROBABILITIES, 0.30) == ("globalismo", None)


def test_user_prompt_asks_for_the_second_class_only_when_it_weighs():
    with_second = explanation_user_prompt(_request())
    assert "explica las dos" in with_second
    assert "- globalismo: 59,5 %\n- personalismo: 27,1 %" in with_second
    assert ARTICLE_TEXT in with_second

    without = explanation_user_prompt(_request(second=None))
    assert "explica solo globalismo" in without
    assert f'"{NO_SECOND}"' in without

    retried = explanation_user_prompt(_request(), feedback="usó la palabra «confianza»")
    assert "no cumplió una regla: usó la palabra «confianza»" in retried


# ---------------------------------------------------------------------------
# Validación de la respuesta
# ---------------------------------------------------------------------------


def test_parse_explanation_accepts_a_valid_paragraph_and_cleans_dashes():
    text = VALID_TWO_CLASSES.replace("bilaterales y", "bilaterales — y")
    explanation, problem = parse_explanation(_payload(text), _request(), "claude-test")
    assert problem is None
    assert "—" not in explanation.text
    assert explanation.support == {"globalismo": "alto", "personalismo": "medio"}
    assert explanation.model == "claude-test"


def test_parse_explanation_ignores_the_second_support_when_not_requested():
    explanation, problem = parse_explanation(
        _payload(VALID_ONE_CLASS, second=NO_SECOND), _request(second=None), "m"
    )
    assert problem is None
    assert explanation.support == {"globalismo": "alto"}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (None, "JSON"),
        ({"explanation": 3}, "JSON"),
        (_payload("La nota es corta y habla de globalismo y personalismo."), "palabras"),
        (_payload(VALID_TWO_CLASSES + " Con alta confianza."), "confianza"),
        (_payload(VALID_TWO_CLASSES.replace("globalismo.", "globalismo (59,5 %).")), "porcentajes"),
        (
            _payload(VALID_TWO_CLASSES.replace("La nota presenta", "El modelo detectó que la nota presenta")),
            "razonamientos",
        ),
        (_payload(VALID_TWO_CLASSES.replace("personalismo", "liderazgo")), "«personalismo»"),
        (_payload(VALID_TWO_CLASSES + " La nota es tendenciosa."), "medio o a los periodistas"),
        (
            _payload(
                VALID_TWO_CLASSES.replace(
                    "La figura",
                    "Cita que «nadie le dirá cómo negociar con sus aliados del hemisferio» y la figura",
                )
            ),
            "cita textual",
        ),
        (_payload(predicted="mucho"), "alto, medio o bajo"),
        (_payload(second=NO_SECOND), "alto, medio o bajo"),
    ],
)
def test_parse_explanation_rejects_what_breaks_the_rules(payload, expected):
    explanation, problem = parse_explanation(payload, _request(), "m")
    assert explanation is None
    assert expected in problem


def test_parse_explanation_rejects_sentences_copied_from_the_note():
    copied = VALID_TWO_CLASSES.replace(
        "El texto da por sentado",
        "Los analistas consultados advierten que la agenda comercial bilateral, la cooperación "
        "antidrogas y los aranceles podrían revisarse. El texto da por sentado",
    )
    explanation, problem = parse_explanation(_payload(copied), _request(), "m")
    assert explanation is None and "copió" in problem


def test_copies_source_allows_short_shared_phrases():
    assert not copies_source("El presidente estadounidense insistió en sus decisiones", ARTICLE_TEXT)
    assert copies_source(
        "dice que el presidente estadounidense insistió en que sus decisiones seguirán firmes "
        "y que nadie le dirá",
        ARTICLE_TEXT,
    )


def test_count_sentences_skips_abbreviations():
    assert count_sentences("La nota habla de EE. UU. y de Colombia. Luego cita a J. M. Pérez.") == 2
    assert count_sentences("Una. Dos. Tres.") == 3


def test_clean_explanation_is_one_paragraph():
    assert clean_explanation("  Uno —dos\n\ntres –cuatro ,  ") == "Uno, dos tres, cuatro"


# ---------------------------------------------------------------------------
# Proveedor: claude -p (sin llamarlo)
# ---------------------------------------------------------------------------


def _envelope(payload: dict | None = None, *, result: str | None = None, **extra) -> str:
    data = {
        "type": "result",
        "is_error": False,
        "result": result if result is not None else json.dumps(payload or _payload()),
        "total_cost_usd": 0.01,
        "modelUsage": {
            "claude-opus-5-5": {"outputTokens": 300},
            "claude-haiku-5-5": {"outputTokens": 12},
        },
    }
    if payload is not None and result is None:
        data["structured_output"] = payload
    data.update(extra)
    return json.dumps(data)


class _FakeRun:
    """Reemplazo de subprocess.run: devuelve las salidas en orden y guarda las llamadas."""

    def __init__(self, *outputs: tuple[int, str]):
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    def __call__(self, command, **kwargs):
        self.calls.append({"command": command, **kwargs})
        returncode, stdout = self.outputs.pop(0)
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")


def test_cli_explainer_runs_claude_without_tools_and_reads_structured_output(monkeypatch):
    fake = _FakeRun((0, _envelope(_payload())))
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setenv("CLAUDECODE", "1")
    explainer = ClaudeCliExplainer("opus", claude_bin="/usr/local/bin/claude")

    explanation = explainer.explain(_request())

    assert explanation is not None
    assert explanation.model == "claude-opus-5-5"
    call = fake.calls[0]
    command = call["command"]
    assert command[:2] == ["/usr/local/bin/claude", "-p"]
    assert command[command.index("--model") + 1] == "opus"
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--system-prompt") + 1] == EXPLANATION_SYSTEM_PROMPT
    assert command[command.index("--output-format") + 1] == "json"
    assert {"--safe-mode", "--no-session-persistence", "--json-schema"} <= set(command)
    # El texto va por stdin, no en los argumentos.
    assert ARTICLE_TEXT in call["input"] and ARTICLE_TEXT not in " ".join(command)
    assert "CLAUDECODE" not in call["env"]
    assert Path(call["cwd"]).is_dir() and not any(Path(call["cwd"]).iterdir())
    assert explainer.calls == 1 and explainer.cost_usd == pytest.approx(0.01)


def test_cli_explainer_retries_once_telling_what_failed(monkeypatch):
    bad = _payload(VALID_TWO_CLASSES + " Con alta confianza.")
    fake = _FakeRun((0, _envelope(bad)), (0, _envelope(_payload())))
    monkeypatch.setattr(subprocess, "run", fake)

    explanation = ClaudeCliExplainer("opus", claude_bin="claude").explain(_request())

    assert explanation is not None
    assert len(fake.calls) == 2
    assert "no cumplió una regla: usó la palabra «confianza»" in fake.calls[1]["input"]


def test_cli_explainer_gives_up_after_two_invalid_answers(monkeypatch):
    bad = _payload(VALID_TWO_CLASSES + " Con alta confianza.")
    fake = _FakeRun((0, _envelope(bad)), (0, _envelope(bad)))
    monkeypatch.setattr(subprocess, "run", fake)
    assert ClaudeCliExplainer("opus", claude_bin="claude").explain(_request()) is None


def test_cli_explainer_reads_json_from_the_result_text(monkeypatch):
    fenced = "```json\n" + json.dumps(_payload()) + "\n```"
    monkeypatch.setattr(subprocess, "run", _FakeRun((0, _envelope(result=fenced))))
    assert ClaudeCliExplainer("opus", claude_bin="claude").explain(_request()) is not None


def test_cli_explainer_stops_on_usage_limit(monkeypatch):
    limit = _envelope(result="Claude usage limit reached. Your limit will reset at 5pm", is_error=True)
    monkeypatch.setattr(subprocess, "run", _FakeRun((1, limit)))
    with pytest.raises(ExplainerUnavailable, match="límite de uso"):
        ClaudeCliExplainer("opus", claude_bin="claude").explain(_request())


def test_cli_explainer_without_binary_is_unavailable():
    with pytest.raises(ExplainerUnavailable):
        ClaudeCliExplainer("opus", claude_bin="/no/existe/claude").explain(_request())


def test_cli_explainer_timeout_fails_only_that_article(monkeypatch):
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    assert ClaudeCliExplainer("opus", claude_bin="claude").explain(_request()) is None


def test_envelope_helpers():
    assert envelope_model({"modelUsage": {}}, "opus") == "opus"
    assert envelope_model(json.loads(_envelope(_payload())), "opus") == "claude-opus-5-5"
    assert envelope_payload({"result": "no es json"}) is None


def test_subprocess_env_drops_only_the_parent_session(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/config")
    env = subprocess_env()
    assert not {"CLAUDECODE", "CLAUDE_CODE_SIMPLE", "ANTHROPIC_BASE_URL"} & set(env)
    assert env["CLAUDE_CONFIG_DIR"] == "/tmp/config" and "PATH" in env

    monkeypatch.delenv("CLAUDECODE")
    assert subprocess_env()["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:1"


# ---------------------------------------------------------------------------
# Pipeline y API (con un LLM falso)
# ---------------------------------------------------------------------------


class FakeExplainer:
    def __init__(self, result="ok"):
        self.result = result
        self.requests: list[ExplanationRequest] = []

    def explain(self, request: ExplanationRequest) -> Explanation | None:
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        if self.result is None:
            return None
        classes = " y ".join(name for name in (request.label, request.second) if name)
        support = {request.label: "medio"}
        if request.second:
            support[request.second] = "bajo"
        return Explanation(text=f"Texto de prueba sobre {classes}.", support=support, model="fake-llm")


def _with_second_class(session, story):
    article = story.articles[0]
    article.classification_probabilities = {
        **article.classification_probabilities,
        "populismo": 0.55,
        "progresismo": 0.30,
        "personalismo": 0.0,
    }
    session.commit()
    return article


def test_run_explanations_fills_the_pending_and_is_idempotent(db_session):
    first = make_story(db_session, 1)
    _with_second_class(db_session, first)
    make_story(db_session, 2)
    explainer = FakeExplainer()

    stats = run_explanations(db_session, explainer, get_settings())

    assert (stats.pending, stats.explained, stats.with_second, stats.failures) == (2, 2, 1, 0)
    article = first.articles[0]
    assert article.explanation_text == "Texto de prueba sobre populismo y progresismo."
    assert article.explanation_basis == {
        "label": "populismo",
        "second": "progresismo",
        "model_version": "test-1.0",
    }
    assert article.explanation_support == {"populismo": "medio", "progresismo": "bajo"}
    assert article.explanation_model == "fake-llm" and is_current(article)
    # El LLM recibe lo mismo que leyó el clasificador: titular y texto.
    assert explainer.requests[0].title and explainer.requests[0].text.startswith("palabra")

    again = FakeExplainer()
    stats = run_explanations(db_session, again, get_settings())
    assert (stats.pending, stats.explained) == (0, 0) and again.requests == []


def test_run_explanations_puts_the_visible_articles_first(db_session):
    story = make_story(db_session, 1, extra_sources=1)
    pending = select_pending(db_session)
    assert [article.id for article in pending][0] == story.representative_article_id


def test_dry_run_and_limit(db_session):
    for index in range(3):
        make_story(db_session, index)
    explainer = FakeExplainer()
    stats = run_explanations(db_session, explainer, get_settings(), dry_run=True)
    assert stats.pending == 3 and explainer.requests == []
    stats = run_explanations(db_session, explainer, get_settings(), limit=2, concurrency=2)
    assert stats.explained == 2 and len(select_pending(db_session)) == 1


def test_a_failure_leaves_the_article_without_explanation(db_session):
    story = make_story(db_session, 1)
    stats = run_explanations(db_session, FakeExplainer(result=None), get_settings())
    assert stats.failures == 1 and story.articles[0].explanation_text is None


def test_the_run_stops_when_the_provider_is_unavailable(db_session):
    for index in range(4):
        make_story(db_session, index)
    explainer = FakeExplainer(result=ExplainerUnavailable("límite de uso"))
    stats = run_explanations(db_session, explainer, get_settings())
    assert stats.aborted == "límite de uso" and stats.explained == 0
    assert len(explainer.requests) < 4


def test_api_detail_serves_the_explanation_with_its_disclaimer(client, db_session):
    story = make_story(db_session, 1)
    assert client.get(f"/api/v1/stories/{story.slug}").json()["explanation"] is None

    run_explanations(db_session, FakeExplainer(), get_settings())
    data = client.get(f"/api/v1/stories/{story.slug}").json()

    assert data["explanation"]["text"] == "Texto de prueba sobre populismo."
    assert data["explanation"]["classes"] == ["populismo"]
    assert data["explanation"]["model"] == "fake-llm"
    assert data["explanation"]["disclaimer"] == EXPLANATION_DISCLAIMER
    assert "por IA a partir de la clase que asignó el modelo" in EXPLANATION_DISCLAIMER
    assert data["explanation"]["generated_at"].endswith("Z")
    # El listado no la trae: solo el detalle.
    item = client.get("/api/v1/stories").json()["items"][0]
    assert "explanation" not in item


def test_a_reclassified_article_stops_serving_its_old_explanation(client, db_session):
    story = make_story(db_session, 1)
    run_explanations(db_session, FakeExplainer(), get_settings())
    article = story.articles[0]
    article.classification_model_version = "test-2.0"
    article.classification_label = "progresismo"
    article.classification_probabilities = {
        **article.classification_probabilities,
        "populismo": 0.06,
        "progresismo": 0.58,
    }
    db_session.commit()

    assert client.get(f"/api/v1/stories/{story.slug}").json()["explanation"] is None
    assert [a.id for a in select_pending(db_session)] == [article.id]

    run_explanations(db_session, FakeExplainer(), get_settings())
    data = client.get(f"/api/v1/stories/{story.slug}").json()
    assert data["explanation"]["classes"] == ["progresismo"]
