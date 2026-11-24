"""Explicación en vivo de la mesa de redacción: JSON parcial, `claude -p` en streaming y API.

Ninguna prueba llama a un LLM. El proveedor corre contra un `claude` falso
(`tests/fake_claude.py`) que escribe líneas de stream-json: unas armadas aquí con
la forma que entrega `claude -p --json-schema --include-partial-messages`, y otras
grabadas de una corrida real con Haiku 5.5 (tests/fixtures), sin datos de sesión.
"""

import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

import pytest

from src.adapters.story_adapter import EXPLANATION_DISCLAIMER
from src.api.app import app
from src.api.routes import explain as explain_route
from src.api.routes.classify import get_classifier_client, get_domain_filter
from src.api.routes.explain import get_stream_explainer
from src.classifier.client import NullClassifierClient
from src.classifier.domain_filter import DomainVerdict
from src.config import get_settings
from src.db.models import ExplanationReview
from src.explanation.generator import ExplainerUnavailable, Explanation, clean_explanation
from src.explanation.pipeline import run_explanations
from src.explanation.prompts import (
    EXPLANATION_PROMPT_VERSION,
    EXPLANATION_SYSTEM_PROMPT,
    prompt_fingerprint,
)
from src.explanation.stream import (
    ClaudeCliStreamExplainer,
    Delta,
    Done,
    Failed,
    JsonFieldStream,
    Retry,
    Status,
)
from tests.conftest import make_story
from tests.test_explanation import (
    ARTICLE_TEXT,
    VALID_ONE_CLASS,
    VALID_TWO_CLASSES,
    FakeExplainer,
    _payload,
    _request,
)

FIXTURES = Path(__file__).parent / "fixtures"
# Corrida real de `claude -p` (Haiku 5.5) con estos prompts: su párrafo copia una
# frase de 15 palabras de la nota, así que parse_explanation lo rechaza.
REAL_COPIED_SENTENCE = FIXTURES / "claude_stream_haiku_copied_sentence.jsonl"

# Huella de cada versión de los prompts. Si esta prueba falla, cambió el system
# prompt, el codebook, la plantilla o el esquema: sube EXPLANATION_PROMPT_VERSION
# en src/explanation/prompts.py y agrega aquí la huella nueva.
PROMPT_FINGERPRINTS = {
    "1": "f57900a518fdea67444135ab112390d916d6b04a9c726d91d0e018ebe6ffa028",
}

TEXTO_MESA = (
    "El Congreso aprobó en último debate la reforma pensional tras meses de discusión "
    "entre el Gobierno y los partidos de oposición."
)


# ---------------------------------------------------------------------------
# Versión de los prompts
# ---------------------------------------------------------------------------


def test_prompt_version_matches_its_fingerprint():
    assert PROMPT_FINGERPRINTS.get(EXPLANATION_PROMPT_VERSION) == prompt_fingerprint(), (
        "Cambió el prompt de la explicación: sube EXPLANATION_PROMPT_VERSION y agrega su huella"
    )


def test_an_explanation_saved_before_the_prompt_version_serves_null(client, db_session):
    story = make_story(db_session, 1)
    run_explanations(db_session, FakeExplainer(), get_settings())
    article = story.articles[0]
    article.explanation_basis = {
        key: value for key, value in article.explanation_basis.items() if key != "prompt_version"
    }
    db_session.commit()

    data = client.get(f"/api/v1/stories/{story.slug}").json()

    assert data["explanation"]["prompt_version"] is None


# ---------------------------------------------------------------------------
# JSON parcial
# ---------------------------------------------------------------------------

TRICKY = {
    "explanation": 'Dijo "basta" y\nsiguió: á, ñ, 😀, \\ / \t fin.',
    "support_predicted": "alto",
    "support_second": "ninguno",
}


@pytest.mark.parametrize("ensure_ascii", [True, False])
def test_field_stream_decodes_the_value_one_character_at_a_time(ensure_ascii):
    raw = json.dumps(TRICKY, ensure_ascii=ensure_ascii)
    field = JsonFieldStream("explanation")
    decoded = "".join(field.feed(char) for char in raw)
    assert decoded == TRICKY["explanation"]
    assert field.complete


@pytest.mark.parametrize("seed", range(20))
def test_field_stream_survives_any_split(seed):
    raw = json.dumps(TRICKY, ensure_ascii=seed % 2 == 0)
    rng = random.Random(seed)
    cuts = sorted(rng.sample(range(1, len(raw)), k=12))
    chunks = [raw[start:end] for start, end in zip([0, *cuts], [*cuts, len(raw)], strict=True)]
    field = JsonFieldStream("explanation")
    assert "".join(field.feed(chunk) for chunk in chunks) == TRICKY["explanation"]


def test_field_stream_only_reads_the_top_level_field():
    raw = json.dumps(
        {
            "meta": {"explanation": "anidado"},
            "lista": ["explanation", {"explanation": "tampoco"}],
            "otro": "explanation",
            "explanation": "este sí",
            "despues": "nada",
        }
    )
    field = JsonFieldStream("explanation")
    assert field.feed(raw) == "este sí"


def test_field_stream_with_a_key_cut_in_half():
    field = JsonFieldStream("explanation")
    assert [field.feed(chunk) for chunk in ['{"expla', 'nation": "Ho', "la", '"}']] == [
        "",
        "Ho",
        "la",
        "",
    ]


def test_field_stream_without_the_field():
    field = JsonFieldStream("explanation")
    assert field.feed('{"support_predicted": "alto"}') == ""
    assert not field.complete


# ---------------------------------------------------------------------------
# Proveedor: claude -p en streaming, contra un claude falso
# ---------------------------------------------------------------------------


def _event(event: dict) -> dict:
    return {"type": "stream_event", "event": event}


def stream_lines(payloads, *, chunk: int = 7, ensure_ascii: bool = False) -> list[dict]:
    """Lo que escribe `claude -p` en stream-json: razona y llama a StructuredOutput.

    Con más de un payload, el modelo repite la llamada a la herramienta (como
    cuando `claude -p` rechaza una que no cumple el esquema). El evento `result`
    trae el último.
    """
    if isinstance(payloads, dict):
        payloads = [payloads]
    lines: list[dict] = [
        {"type": "system", "subtype": "init"},
        _event({"type": "message_start", "message": {"model": "claude-opus-5-5"}}),
        _event(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            }
        ),
        _event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": ""},
            }
        ),
        _event({"type": "content_block_stop", "index": 0}),
    ]
    for payload in payloads:
        raw = json.dumps(payload, ensure_ascii=ensure_ascii)
        lines.append(
            _event(
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_test",
                        "name": "StructuredOutput",
                        "input": {},
                    },
                }
            )
        )
        lines += [
            _event(
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": raw[start : start + chunk],
                    },
                }
            )
            for start in range(0, len(raw), chunk)
        ]
        lines.append(_event({"type": "content_block_stop", "index": 1}))
    lines.append(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": json.dumps(payloads[-1]),
            "structured_output": payloads[-1],
            "total_cost_usd": 0.01,
            "modelUsage": {"claude-opus-5-5": {"outputTokens": 300}},
        }
    )
    return lines


@pytest.fixture()
def fake_claude(tmp_path, monkeypatch):
    """Instala un `claude` falso. install(*intentos) devuelve la ruta del binario y el log."""
    binary = tmp_path / "claude"
    binary.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{Path(__file__).parent / "fake_claude.py"}" "$@"\n'
    )
    binary.chmod(0o755)
    log = tmp_path / "calls.jsonl"
    plan = tmp_path / "plan.json"

    def install(*attempts: dict) -> tuple[str, Path]:
        plan.write_text(json.dumps({"log": str(log), "attempts": list(attempts)}))
        monkeypatch.setenv("FAKE_CLAUDE_PLAN", str(plan))
        return str(binary), log

    return install


def _calls(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def collect(explainer, request, *, stop=None) -> list:
    """Corre el stream hasta el final, o hasta que `stop(evento)` diga que se cierre."""

    async def run():
        events = []
        stream = explainer.stream(request)
        try:
            async for event in stream:
                events.append(event)
                if stop is not None and stop(event):
                    break
        finally:
            await stream.aclose()
        return events

    return asyncio.run(run())


def _deltas(events) -> str:
    return "".join(event.text for event in events if isinstance(event, Delta))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_stream_sends_the_paragraph_as_it_arrives_and_validates_it(fake_claude):
    binary, log = fake_claude({"lines": stream_lines(_payload())})
    explainer = ClaudeCliStreamExplainer("claude-opus-5-5", claude_bin=binary)

    events = collect(explainer, _request())

    assert events[:2] == [Status("thinking"), Status("writing")]
    assert _deltas(events) == VALID_TWO_CLASSES
    assert sum(isinstance(event, Delta) for event in events) > 10
    done = events[-1]
    assert isinstance(done, Done)
    assert done.explanation.text == clean_explanation(VALID_TWO_CLASSES)
    assert done.explanation.support == {"globalismo": "alto", "personalismo": "medio"}
    assert done.explanation.model == "claude-opus-5-5"
    assert done.explanation.prompt_version == EXPLANATION_PROMPT_VERSION

    (call,) = _calls(log)
    argv = call["argv"]
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--system-prompt") + 1] == EXPLANATION_SYSTEM_PROMPT
    assert {"--verbose", "--include-partial-messages", "--json-schema", "--safe-mode"} <= set(argv)
    assert ARTICLE_TEXT in call["stdin"] and ARTICLE_TEXT not in " ".join(argv)
    assert not _alive(call["pid"])


def test_stream_decodes_escaped_unicode_too(fake_claude):
    binary, _ = fake_claude({"lines": stream_lines(_payload(), chunk=3, ensure_ascii=True)})
    events = collect(ClaudeCliStreamExplainer("opus", claude_bin=binary), _request())
    assert _deltas(events) == VALID_TWO_CLASSES and isinstance(events[-1], Done)


def test_a_real_run_that_copies_the_note_is_retried_with_the_rule(fake_claude):
    real = [json.loads(line) for line in REAL_COPIED_SENTENCE.read_text().splitlines()]
    real_text = next(line for line in real if line["type"] == "result")["structured_output"]
    binary, log = fake_claude(
        {"lines_file": str(REAL_COPIED_SENTENCE)}, {"lines": stream_lines(_payload())}
    )

    events = collect(ClaudeCliStreamExplainer("claude-haiku-5-5", claude_bin=binary), _request())

    retry_at = next(i for i, event in enumerate(events) if isinstance(event, Retry))
    # Lo que se mostró en vivo es exactamente el párrafo que entregó claude -p.
    assert _deltas(events[:retry_at]) == real_text["explanation"]
    assert "copió una frase de la nota" in events[retry_at].reason
    assert events[retry_at + 1] == Status("thinking")
    assert _deltas(events[retry_at:]) == VALID_TWO_CLASSES
    assert isinstance(events[-1], Done)
    first, second = _calls(log)
    assert "no cumplió una regla" not in first["stdin"]
    assert "no cumplió una regla: copió una frase de la nota" in second["stdin"]


def test_two_invalid_drafts_end_in_a_failure(fake_claude):
    bad = stream_lines(_payload(VALID_TWO_CLASSES + " Con alta confianza."))
    binary, log = fake_claude({"lines": bad})

    events = collect(ClaudeCliStreamExplainer("opus", claude_bin=binary), _request())

    assert sum(isinstance(event, Retry) for event in events) == 1
    assert isinstance(events[-1], Failed) and "confianza" in events[-1].reason
    assert len(_calls(log)) == 2


def test_a_repeated_tool_call_drops_the_first_draft(fake_claude):
    rejected = _payload("Borrador que claude -p rechazó por el esquema.")
    binary, _ = fake_claude({"lines": stream_lines([rejected, _payload()])})

    events = collect(ClaudeCliStreamExplainer("opus", claude_bin=binary), _request())

    retry_at = next(i for i, event in enumerate(events) if isinstance(event, Retry))
    assert _deltas(events[:retry_at]) == rejected["explanation"]
    assert events[retry_at].reason == "la respuesta no cumplía el esquema JSON"
    assert _deltas(events[retry_at:]) == VALID_TWO_CLASSES
    assert isinstance(events[-1], Done)


def test_usage_limit_makes_the_explainer_unavailable(fake_claude):
    limit = {
        "type": "result",
        "is_error": True,
        "result": "Claude usage limit reached. Your limit will reset at 5pm",
    }
    binary, _ = fake_claude({"lines": [limit], "exit": 1})
    with pytest.raises(ExplainerUnavailable, match="límite de uso"):
        collect(ClaudeCliStreamExplainer("opus", claude_bin=binary), _request())


def test_a_crash_fails_only_this_explanation(fake_claude):
    binary, _ = fake_claude({"lines": [], "exit": 2, "stderr": "boom"})
    events = collect(ClaudeCliStreamExplainer("opus", claude_bin=binary), _request())
    assert events == [Failed("claude -p falló (código 2)")]


def test_without_binary_the_explainer_is_unavailable():
    with pytest.raises(ExplainerUnavailable):
        collect(ClaudeCliStreamExplainer("opus", claude_bin="/no/existe/claude"), _request())


def test_closing_the_stream_kills_claude(fake_claude):
    lines = stream_lines(_payload())
    binary, log = fake_claude({"lines": lines, "hang_after": 4})
    started = time.monotonic()

    events = collect(
        ClaudeCliStreamExplainer("opus", claude_bin=binary),
        _request(),
        stop=lambda event: event == Status("thinking"),
    )

    # El falso se queda 60 s "razonando": cerrar el stream no lo espera, lo mata.
    assert time.monotonic() - started < 10
    assert events == [Status("thinking")]
    (call,) = _calls(log)
    assert not _alive(call["pid"])


def test_a_timeout_kills_claude_and_fails(fake_claude):
    binary, log = fake_claude({"lines": stream_lines(_payload()), "hang_after": 4})
    explainer = ClaudeCliStreamExplainer("opus", claude_bin=binary, timeout_seconds=0.5)

    started = time.monotonic()
    events = collect(explainer, _request())

    assert time.monotonic() - started < 10
    assert events[0] == Status("thinking")
    assert isinstance(events[-1], Failed) and "superó" in events[-1].reason
    (call,) = _calls(log)
    assert not _alive(call["pid"])


# ---------------------------------------------------------------------------
# API: POST /api/v1/explain (SSE), /health y POST /api/v1/explanation-reviews
# ---------------------------------------------------------------------------


class FakeStreamExplainer:
    model = "fake-llm"

    def __init__(self, events=None, error: Exception | None = None):
        self.events = list(events or [])
        self.error = error
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        for event in self.events:
            yield event


def _explanation(text: str = VALID_TWO_CLASSES, support=None) -> Explanation:
    return Explanation(
        text=text,
        support=support or {"populismo": "medio"},
        model="fake-llm",
        prompt_version=EXPLANATION_PROMPT_VERSION,
    )


@pytest.fixture()
def desk_on():
    settings = get_settings().model_copy(update={"desk_explanation_mode": "claude_cli"})
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture()
def explainer_with():
    def install(fake: FakeStreamExplainer) -> FakeStreamExplainer:
        app.dependency_overrides[get_stream_explainer] = lambda: fake
        return fake

    yield install
    app.dependency_overrides.pop(get_stream_explainer, None)


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """[(evento, datos)] de un cuerpo text/event-stream; ignora los comentarios."""
    events = []
    for block in body.strip().split("\n\n"):
        name, data = "message", []
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data.append(line[len("data: ") :])
        if data:
            events.append((name, json.loads("\n".join(data))))
    return events


def _explain(client, text: str = TEXTO_MESA, title: str = "Reforma aprobada"):
    response = client.post("/api/v1/explain", json={"title": title, "text": text})
    return response, parse_sse(response.text) if response.status_code == 200 else []


def test_everything_is_off_by_default(client):
    response = client.post("/api/v1/explain", json={"text": TEXTO_MESA})
    assert response.status_code == 503
    assert response.json() == {
        "detail": "La explicación en vivo no está activa en este servidor (DESK_EXPLANATION_MODE=off).",
        "code": "explanations_disabled",
    }
    assert client.post("/api/v1/explanation-reviews", json={}).status_code == 503
    assert client.get("/api/v1/health").json()["desk_explanations"] is False


def test_health_reports_the_live_explanation(client, desk_on):
    assert client.get("/api/v1/health").json()["desk_explanations"] is True


def test_explain_streams_classification_then_the_explanation(client, desk_on, explainer_with):
    fake = explainer_with(
        FakeStreamExplainer(
            [Status("thinking"), Status("writing"), Delta("La nota "), Delta("presenta"), None]
        )
    )
    # El último evento depende de la clase que devuelva el mock: se arma aquí.
    response = client.post(
        "/api/v1/classify", json={"title": "Reforma aprobada", "text": TEXTO_MESA}
    )
    label = response.json()["label"]
    fake.events[-1] = Done(_explanation(VALID_ONE_CLASS, {label: "medio"}))

    response, events = _explain(client)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [name for name, _ in events] == [
        "classification",
        "status",
        "status",
        "delta",
        "delta",
        "explanation",
    ]
    classification = events[0][1]
    assert classification["label"] == label
    assert classification["model_version"] == "mock-0.1.0"
    assert classification["political_filter"] == "disabled"
    assert [data for name, data in events if name == "status"] == [
        {"phase": "thinking"},
        {"phase": "writing"},
    ]
    assert "".join(data["text"] for name, data in events if name == "delta") == "La nota presenta"
    final = events[-1][1]
    assert final["text"] == VALID_ONE_CLASS
    assert final["classes"][0] == label
    assert final["support"] == {label: "medio"}
    assert final["model"] == "fake-llm"
    assert final["prompt_version"] == EXPLANATION_PROMPT_VERSION
    assert final["disclaimer"] == EXPLANATION_DISCLAIMER

    (request,) = fake.requests
    assert request.label == label and request.title == "Reforma aprobada"
    assert request.text == TEXTO_MESA
    assert request.probabilities == classification["probabilities"]


def test_explain_without_title_says_so_to_the_llm(client, desk_on, explainer_with):
    fake = explainer_with(FakeStreamExplainer([Failed("x")]))
    _explain(client, title="")
    assert fake.requests[0].title == "(sin titular)"


def test_explain_passes_retries_and_failures_through(client, desk_on, explainer_with):
    explainer_with(
        FakeStreamExplainer(
            [Delta("Borrador"), Retry("usó la palabra «confianza»"), Failed("reglas")]
        )
    )

    _, events = _explain(client)

    assert [name for name, _ in events] == ["classification", "delta", "retry", "error"]
    assert events[2][1] == {"reason": "usó la palabra «confianza»"}
    assert events[3][1]["code"] == "explanation_failed"
    assert events[3][1]["reason"] == "reglas"


def test_explain_reports_an_unavailable_explainer(client, desk_on, explainer_with):
    explainer_with(FakeStreamExplainer(error=ExplainerUnavailable("límite de uso de Claude")))

    _, events = _explain(client)

    assert [name for name, _ in events] == ["classification", "error"]
    assert events[1][1]["code"] == "explainer_unavailable"


def test_explain_without_classifier_does_not_call_the_llm(client, desk_on, explainer_with):
    fake = explainer_with(FakeStreamExplainer([Failed("no debería llegar aquí")]))
    app.dependency_overrides[get_classifier_client] = NullClassifierClient
    try:
        _, events = _explain(client)
    finally:
        app.dependency_overrides.pop(get_classifier_client, None)

    assert events == [
        (
            "error",
            {
                "code": "classifier_unavailable",
                "detail": "El clasificador no está disponible en este momento.",
                "category": None,
                "reason": None,
            },
        )
    ]
    assert fake.requests == []


class _RejectingFilter:
    enabled = True

    def check(self, text):
        return DomainVerdict(
            category="nonpolitical_article",
            confidence=0.9,
            reason="Receta de cocina",
            escalated=False,
        )


def test_explain_respects_the_political_filter(client, desk_on, explainer_with):
    fake = explainer_with(FakeStreamExplainer([Failed("no debería llegar aquí")]))
    app.dependency_overrides[get_domain_filter] = _RejectingFilter
    try:
        _, events = _explain(client)
    finally:
        app.dependency_overrides.pop(get_domain_filter, None)

    ((name, data),) = events
    assert name == "error" and data["code"] == "not_political"
    assert data["category"] == "nonpolitical_article" and data["reason"] == "Receta de cocina"
    assert fake.requests == []


def test_one_live_explanation_at_a_time(client, desk_on, explainer_with):
    fake = explainer_with(FakeStreamExplainer([Failed("no debería llegar aquí")]))
    assert explain_route._busy.acquire(blocking=False)
    try:
        _, events = _explain(client)
    finally:
        explain_route._busy.release()

    assert [(name, data["code"]) for name, data in events] == [("error", "busy")]
    assert fake.requests == []
    # Al terminar un stream, el siguiente sí arranca.
    _, events = _explain(client)
    assert events[0][0] == "classification"


def test_explain_validates_the_body(client, desk_on):
    assert client.post("/api/v1/explain", json={"text": "muy corto"}).status_code == 422


def _review(**overrides) -> dict:
    probabilities = {
        "personalismo": 0.2705,
        "institucionalismo": 0.0186,
        "populismo": 0.0017,
        "doctrinarismo": 0.0052,
        "soberanismo": 0.0631,
        "globalismo": 0.595,
        "conservadurismo": 0.0358,
        "progresismo": 0.0101,
    }
    body = {
        "title": "  Trump perdió el Congreso  ",
        "text": ARTICLE_TEXT,
        "classification": {
            "label": "globalismo",
            "probabilities": probabilities,
            "model_version": "beto-cribado-s42",
        },
        "explanation": {
            "text": VALID_TWO_CLASSES,
            "classes": ["globalismo", "personalismo"],
            "support": {"globalismo": "medio", "personalismo": "bajo"},
            "model": "claude-opus-5-5",
            "prompt_version": EXPLANATION_PROMPT_VERSION,
        },
        "review": {
            "support": {"globalismo": "alto", "personalismo": "bajo"},
            "revised_text": "   ",
            "fits_text": "en_parte",
            "neutral": True,
            "notes": "  El personalismo es leve.  ",
        },
    }
    for path, value in overrides.items():
        target = body
        *parents, key = path.split(".")
        for parent in parents:
            target = target[parent]
        target[key] = value
    return body


def test_a_review_is_stored_with_both_readings(client, db_session, desk_on):
    response = client.post("/api/v1/explanation-reviews", json=_review())

    assert response.status_code == 201
    data = response.json()
    assert len(data["id"]) == 32 and data["created_at"].endswith("Z")
    review = db_session.get(ExplanationReview, data["id"])
    assert review.title == "Trump perdió el Congreso"
    assert review.text == ARTICLE_TEXT
    assert review.classification_label == "globalismo"
    assert review.classification_model_version == "beto-cribado-s42"
    assert review.explanation_support == {"globalismo": "medio", "personalismo": "bajo"}
    assert review.explanation_prompt_version == EXPLANATION_PROMPT_VERSION
    assert review.reviewer_support == {"globalismo": "alto", "personalismo": "bajo"}
    assert review.revised_text is None
    assert review.fits_text == "en_parte" and review.neutral is True
    assert review.notes == "El personalismo es leve."


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"explanation.classes": ["personalismo", "globalismo"]}, "la clase predicha"),
        ({"explanation.classes": ["globalismo", "globalismo"]}, "repite"),
        ({"explanation.classes": ["globalismo", "fascismo"]}, "desconocida"),
        ({"explanation.classes": []}, "at least 1"),
        ({"explanation.support": {"globalismo": "medio"}}, "explanation.support"),
        ({"review.support": {"globalismo": "alto"}}, "review.support"),
        ({"review.support": {"globalismo": "altísimo", "personalismo": "bajo"}}, "alto"),
        ({"review.fits_text": "quizá"}, "si"),
        ({"review.notes": "x" * 501}, "500"),
        ({"review.revised_text": "x" * 2001}, "2000"),
        ({"classification.probabilities": {"globalismo": 1.0}}, "8 clases"),
        ({"classification.probabilities.globalismo": 0.9}, "sumar 1"),
        ({"text": "muy corto"}, "40"),
    ],
)
def test_a_review_that_breaks_a_rule_is_rejected(client, desk_on, overrides, message):
    response = client.post("/api/v1/explanation-reviews", json=_review(**overrides))
    assert response.status_code == 422
    assert message in json.dumps(response.json(), ensure_ascii=False)
