"""Redacción de la explicación con un LLM y validación de la respuesta.

El proveedor es `claude -p`, el modo no interactivo de Claude Code, con la
suscripción de Claude del usuario (como `IdeoGraphCO/scripts/filter_runner.py`).
Cada artículo es una llamada sin herramientas, con el system prompt propio de
`prompts.py` en lugar del de Claude Code y salida JSON.

La respuesta no se guarda tal cual: `parse_explanation` la limpia (sin rayas,
un solo párrafo) y la rechaza si rompe una regla que se puede comprobar con
código: largo, la palabra «confianza», porcentajes, razonamientos atribuidos al
clasificador, citas largas o frases copiadas de la nota, o si no nombra las
clases que debía explicar. Un rechazo se reintenta una vez diciéndole al LLM
qué regla falló; si vuelve a fallar, el artículo queda sin explicación y se
reintenta en la corrida siguiente. La neutralidad del tono no se puede comprobar
así: la cuidan el prompt y la revisión humana.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from typing import Protocol

from src.config import Settings
from src.explanation.prompts import (
    EXPLANATION_SYSTEM_PROMPT,
    RESPONSE_SCHEMA,
    SUPPORT_LEVELS,
    ExplanationRequest,
    explanation_user_prompt,
)

logger = logging.getLogger(__name__)

# Límites de forma. El prompt pide 3 o 4 frases y 55 a 90 palabras; aquí hay
# holgura porque el conteo de frases tropieza con abreviaturas («EE. UU.»).
MIN_WORDS = 40
MAX_WORDS = 125
MIN_SENTENCES = 2
MAX_SENTENCES = 5
# Citas de la nota: ninguna de más de 8 palabras y, entre todas, como mucho 16.
MAX_QUOTE_WORDS = 8
MAX_QUOTED_WORDS = 16
# 12 palabras seguidas iguales a las de la nota ya son copiar una frase (los
# nombres propios largos, como «la Cámara de Representantes de los Estados
# Unidos», tienen menos).
COPY_NGRAM_WORDS = 12

_DASHES = re.compile(r"\s*[—–]\s*")
_QUOTES = re.compile(r"«([^»]*)»|“([^”]*)”|\"([^\"]*)\"")
_WORD = re.compile(r"\w+")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+(?=[«¿¡\"“(]?[A-ZÁÉÍÓÚÑÜ])")
# Abreviaturas cuyo punto no cierra la frase.
_ABBREVIATIONS = ("EE. UU.", "EE.UU.", "p. ej.", "Sr.", "Sra.", "Dr.", "Dra.", "etc.", "núm.")
_INITIAL = re.compile(r"\b([A-ZÁÉÍÓÚÑ])\.(?=\s+[A-ZÁÉÍÓÚÑ])")

# Reglas del prompt que se pueden comprobar con código. Los temas de una nota
# pueden incluir palabras como «falsos positivos» o «propaganda», así que la
# lista se limita a lo que casi siempre es un juicio o un error de la respuesta.
_BANNED: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bconfianza\b", re.IGNORECASE), "usó la palabra «confianza»"),
    (re.compile(r"\d\s*%|\bpor\s?ciento\b", re.IGNORECASE), "escribió porcentajes"),
    (
        re.compile(
            r"\b(?:el|este|dicho)\s+(?:modelo|clasificador|sistema)\s+(?:\w+\s+){0,2}?"
            r"(?:detect|identific|not[óo]|interpret|consider|se\s+fij|vio\b|ve\b|entend|"
            r"razon|decid|cre[eyó]|percib|aprend|asoci|infiri|reconoc|capt)",
            re.IGNORECASE,
        ),
        "le atribuyó razonamientos al clasificador",
    ),
    (re.compile(r"\bBETO\b|\bcodebook\b", re.IGNORECASE), "nombró el clasificador o el codebook"),
    (
        re.compile(
            r"\b(?:como|soy)\s+(?:una?\s+)?(?:ia|inteligencia artificial|modelo de lenguaje)\b",
            re.IGNORECASE,
        ),
        "habló de sí misma como IA",
    ),
    (
        re.compile(
            r"\b(?:veracidad|ver[íi]dic\w*|falsedad|es\s+(?:falso|falsa|mentira)|mient[ea]n?)\b",
            re.IGNORECASE,
        ),
        "juzgó la veracidad de los hechos",
    ),
    (
        re.compile(
            r"\b(?:sesgad\w*|parcializad\w*|tendencios\w*|amarillis\w*|sensacionalis\w*)\b",
            re.IGNORECASE,
        ),
        "juzgó al medio o a los periodistas",
    ),
]

# Mensajes de `claude -p` que dicen que no hay más cuota o que falta el login:
# seguir llamando solo acumularía fallos (mismo criterio que filter_runner.py).
_USAGE_LIMIT = re.compile(
    r"(?:session|usage|weekly|hourly|daily|\d+\s*-?\s*hour)\s+limit|limit\s+reached"
    r"|hit\s+your\s+\w+\s+limit|out\s+of\s+(?:credits?|quota)|quota|credit\s+balance"
    r"|insufficient\s+credit",
    re.IGNORECASE,
)
_NOT_LOGGED_IN = re.compile(
    r"not\s+logged\s+in|/login|invalid\s+api\s+key|oauth\s+token|authentication_error",
    re.IGNORECASE,
)

# Variables que una sesión de Claude Code les pasa a sus procesos hijos. Si el
# script corre dentro de una (lanzado por un agente, por ejemplo), `claude -p`
# las heredaría: CLAUDE_CODE_SIMPLE le impide leer el login del usuario y
# ANTHROPIC_BASE_URL lo mandaría por el proxy de esa sesión. Se quitan para que
# la llamada sea la misma que desde una terminal. Fuera de una sesión no se toca
# nada del entorno.
_PARENT_SESSION_PREFIXES = ("CLAUDECODE", "CLAUDE_CODE_", "CLAUDE_AGENT_SDK_")
_PARENT_SESSION_KEYS = (
    "ANTHROPIC_BASE_URL",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "CLAUDE_PREVIEW_CLASSIFIER_FLOOR",
)


@dataclass(frozen=True)
class Explanation:
    text: str
    # Cuánto apoya el texto cada clase explicada, según el LLM: alto/medio/bajo.
    support: dict[str, str]
    # Id del LLM que la redactó (p. ej. claude-opus-5-5).
    model: str


class ExplainerUnavailable(RuntimeError):
    """El proveedor no puede atender más llamadas (cuota agotada, sin CLI o sin login)."""


class Explainer(Protocol):
    def explain(self, request: ExplanationRequest) -> Explanation | None:
        """La explicación validada, o None si falló este artículo."""
        ...


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------


def clean_explanation(text: str) -> str:
    """Un solo párrafo, sin rayas (regla de estilo del proyecto)."""
    cleaned = _DASHES.sub(", ", " ".join(text.split()))
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r",(?:\s*,)+", ",", cleaned)
    return cleaned.strip(" ,")


def count_sentences(text: str) -> int:
    masked = text
    for abbreviation in _ABBREVIATIONS:
        masked = masked.replace(abbreviation, abbreviation.replace(".", "·"))
    masked = _INITIAL.sub(r"\1·", masked)
    return len([part for part in _SENTENCE_BREAK.split(masked.strip()) if part.strip()])


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def copies_source(explanation: str, source: str, ngram: int = COPY_NGRAM_WORDS) -> bool:
    """True si la explicación repite `ngram` palabras seguidas del texto de la nota."""
    words = _words(explanation)
    if len(words) < ngram:
        return False
    haystack = f" {' '.join(_words(source))} "
    return any(
        f" {' '.join(words[start:start + ngram])} " in haystack
        for start in range(len(words) - ngram + 1)
    )


def _quote_problem(text: str, request: ExplanationRequest) -> str | None:
    class_names = {request.label, request.second}
    total = 0
    for match in _QUOTES.finditer(text):
        quoted = next(group for group in match.groups() if group is not None).strip()
        if quoted.lower() in class_names:
            continue
        size = len(_words(quoted))
        if size > MAX_QUOTE_WORDS:
            return f"una cita textual tiene {size} palabras (máximo {MAX_QUOTE_WORDS})"
        total += size
    if total > MAX_QUOTED_WORDS:
        return f"cita demasiado texto de la nota ({total} palabras entre comillas)"
    return None


def parse_explanation(
    payload: dict | None, request: ExplanationRequest, model: str
) -> tuple[Explanation | None, str | None]:
    """(explicación, None) si la respuesta cumple las reglas; (None, motivo) si no."""
    if not isinstance(payload, dict) or not isinstance(payload.get("explanation"), str):
        return None, "no respondió con el JSON pedido"
    text = clean_explanation(payload["explanation"])

    words = len(text.split())
    if not MIN_WORDS <= words <= MAX_WORDS:
        return None, f"el párrafo tiene {words} palabras (se piden entre 55 y 90)"
    sentences = count_sentences(text)
    if not MIN_SENTENCES <= sentences <= MAX_SENTENCES:
        return None, f"el párrafo tiene {sentences} frases (se piden 3 o 4)"
    for pattern, problem in _BANNED:
        if pattern.search(text):
            return None, problem
    quote_problem = _quote_problem(text, request)
    if quote_problem:
        return None, quote_problem
    if copies_source(text, request.text):
        return None, f"copió una frase de la nota ({COPY_NGRAM_WORDS} palabras seguidas o más)"
    lowered = text.lower()
    for name in (request.label, request.second):
        if name and name not in lowered:
            return None, f"no nombró la clase «{name}»"

    support = {request.label: payload.get("support_predicted")}
    if request.second:
        support[request.second] = payload.get("support_second")
    if any(level not in SUPPORT_LEVELS for level in support.values()):
        return None, "no calificó el apoyo de cada clase con alto, medio o bajo"
    return Explanation(text=text, support=support, model=model), None


# ---------------------------------------------------------------------------
# Proveedor: claude -p
# ---------------------------------------------------------------------------


def subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    if "CLAUDECODE" in env:
        for key in list(env):
            if key.startswith(_PARENT_SESSION_PREFIXES) or key in _PARENT_SESSION_KEYS:
                env.pop(key)
    # Sin esto, cada `claude -p` hace además una llamada corta a Haiku que no
    # aporta nada a la explicación (p. ej., un título para la sesión).
    env["DISABLE_NON_ESSENTIAL_MODEL_CALLS"] = "1"
    return env


def _json_object(text: str) -> dict | None:
    text = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def envelope_payload(envelope: dict) -> dict | None:
    """La respuesta del modelo dentro del JSON de `claude -p --output-format json`."""
    structured = envelope.get("structured_output")
    if isinstance(structured, dict):
        return structured
    result = envelope.get("result")
    return _json_object(result) if isinstance(result, str) else None


def envelope_model(envelope: dict, fallback: str) -> str:
    """El modelo que respondió: el de más tokens de salida en `modelUsage`."""
    usage = envelope.get("modelUsage")
    if isinstance(usage, dict) and usage:

        def output_tokens(item: tuple[str, object]) -> float:
            stats = item[1]
            return stats.get("outputTokens", 0) if isinstance(stats, dict) else 0

        return max(usage.items(), key=output_tokens)[0]
    return fallback


class ClaudeCliExplainer:
    """`claude -p` sin herramientas, con system prompt propio y salida JSON.

    `--safe-mode` apaga CLAUDE.md, hooks, MCP y skills del usuario, y la
    llamada corre en una carpeta temporal vacía: el modelo solo ve el prompt.
    """

    def __init__(
        self,
        model: str,
        *,
        claude_bin: str | None = None,
        timeout_seconds: float = 300.0,
        effort: str | None = None,
        max_attempts: int = 2,
        use_json_schema: bool = True,
    ):
        self.model = model
        self._bin = claude_bin or shutil.which("claude")
        self._timeout = timeout_seconds
        self._effort = effort
        self._max_attempts = max(1, max_attempts)
        self._use_json_schema = use_json_schema
        self._workdir = tempfile.mkdtemp(prefix="ideographco-explicacion-")
        self._lock = threading.Lock()
        self.calls = 0
        self.cost_usd = 0.0

    def command(self) -> list[str]:
        if not self._bin:
            raise ExplainerUnavailable("no se encontró el binario `claude` (define CLAUDE_BIN)")
        command = [
            self._bin,
            "-p",
            "--model", self.model,
            "--system-prompt", EXPLANATION_SYSTEM_PROMPT,
            "--tools", "",
            "--output-format", "json",
            "--no-session-persistence",
            "--safe-mode",
        ]
        if self._use_json_schema:
            command += ["--json-schema", json.dumps(RESPONSE_SCHEMA)]
        if self._effort:
            command += ["--effort", self._effort]
        return command

    def _call(self, prompt: str) -> dict | None:
        """Una llamada. None si falló solo esta; ExplainerUnavailable si no hay servicio."""
        try:
            completed = subprocess.run(
                self.command(),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                cwd=self._workdir,
                env=subprocess_env(),
            )
        except FileNotFoundError as error:
            raise ExplainerUnavailable(f"no se pudo ejecutar `claude`: {error}") from error
        except subprocess.TimeoutExpired:
            logger.warning("claude -p superó %.0f s", self._timeout)
            return None
        with self._lock:
            self.calls += 1
        output = completed.stdout or ""
        envelope = _json_object(output) if output.strip() else None
        if envelope is not None:
            with self._lock:
                self.cost_usd += float(envelope.get("total_cost_usd") or 0.0)
        failed = completed.returncode != 0 or envelope is None or envelope.get("is_error")
        if not failed:
            return envelope
        message = " ".join(
            part
            for part in (
                str(envelope.get("result", "")) if envelope else "",
                completed.stderr or "",
                "" if envelope else output,
            )
            if part
        )[:500]
        if _USAGE_LIMIT.search(message):
            raise ExplainerUnavailable(f"límite de uso de Claude: {message[:200]}")
        if _NOT_LOGGED_IN.search(message):
            raise ExplainerUnavailable(f"`claude` sin sesión iniciada: {message[:200]}")
        logger.warning("claude -p falló (código %s): %s", completed.returncode, message[:200])
        return None

    def explain(self, request: ExplanationRequest) -> Explanation | None:
        feedback = None
        for attempt in range(1, self._max_attempts + 1):
            envelope = self._call(explanation_user_prompt(request, feedback))
            if envelope is None:
                return None
            explanation, problem = parse_explanation(
                envelope_payload(envelope), request, envelope_model(envelope, self.model)
            )
            if explanation is not None:
                return explanation
            logger.warning(
                "Explicación rechazada (%s), intento %d de %d", problem, attempt, self._max_attempts
            )
            feedback = problem
        return None


def get_explainer(settings: Settings, model: str | None = None) -> ClaudeCliExplainer:
    return ClaudeCliExplainer(
        model or settings.explanation_model,
        claude_bin=settings.claude_bin or None,
        timeout_seconds=settings.explanation_timeout_seconds,
        effort=settings.explanation_effort or None,
    )
