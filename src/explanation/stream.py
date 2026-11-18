"""Explicación en vivo para la mesa de redacción: `claude -p` en streaming.

Mismo LLM, mismos prompts y misma validación que la explicación por lotes
(`generator.py`), pero con `--output-format stream-json --include-partial-messages`.
Con `--json-schema`, `claude -p` entrega la salida estructurada como una llamada
a la herramienta `StructuredOutput`, y su JSON llega a trozos
(`input_json_delta`). De ese JSON parcial se decodifica, a medida que llega, el
valor de "explanation": es lo que la mesa muestra mientras se escribe.

Al terminar, el evento `result` trae la salida completa (`structured_output`),
que pasa por `parse_explanation` igual que en lotes. Si rompe una regla, el
borrador se descarta (evento Retry) y se pide otra vez una sola vez, diciendo
qué regla falló.

Cada intento es un proceso de `claude -p`. Si quien consume el stream lo cierra
o lo cancela (el cliente cortó la conexión), el proceso se mata para que no
siga gastando cuota de la suscripción.
"""

import asyncio
import json
import logging
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import anyio

from src.config import Settings
from src.explanation.generator import (
    ExplainerUnavailable,
    Explanation,
    claude_command,
    envelope_model,
    envelope_payload,
    parse_explanation,
    raise_if_unavailable,
    subprocess_env,
)
from src.explanation.prompts import ExplanationRequest, explanation_user_prompt

logger = logging.getLogger(__name__)

STRUCTURED_OUTPUT_TOOL = "StructuredOutput"
EXPLANATION_FIELD = "explanation"
# Una línea de stream-json puede traer la respuesta completa (evento `result`);
# el límite por defecto de asyncio (64 KiB) se queda corto con holgura.
LINE_LIMIT = 1024 * 1024


# ---------------------------------------------------------------------------
# Eventos que produce el stream
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Status:
    """El LLM empezó a razonar ("thinking") o a escribir la respuesta ("writing")."""

    phase: str


@dataclass(frozen=True)
class Delta:
    """Caracteres nuevos del párrafo."""

    text: str


@dataclass(frozen=True)
class Retry:
    """El borrador mostrado se descarta: viene otro intento desde el principio."""

    reason: str


@dataclass(frozen=True)
class Done:
    """La explicación final, ya validada con `parse_explanation`."""

    explanation: Explanation


@dataclass(frozen=True)
class Failed:
    """No hubo explicación válida (reglas rotas dos veces, fallo o tiempo agotado)."""

    reason: str


StreamEvent = Status | Delta | Retry | Done | Failed


class StreamExplainer(Protocol):
    model: str

    def stream(self, request: ExplanationRequest) -> AsyncIterator[StreamEvent]:
        """Eventos de una explicación; lanza ExplainerUnavailable si no hay servicio."""
        ...


# ---------------------------------------------------------------------------
# JSON parcial
# ---------------------------------------------------------------------------

_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class JsonFieldStream:
    """Decodifica, a medida que llega, un campo string de primer nivel de un JSON.

    `feed` recibe los trozos tal como llegan, aunque corten una clave, un escape
    o un `\\uXXXX` por la mitad, y devuelve solo los caracteres nuevos del valor
    del campo. Los demás campos, y los objetos o listas anidados, se recorren sin
    emitir nada.
    """

    def __init__(self, field: str):
        self.field = field
        self.complete = False
        self._depth = 0
        self._expect_key = False
        self._in_string = False
        self._string_is_key = False
        self._capturing = False
        self._key_chars: list[str] = []
        self._last_key: str | None = None
        # None: sin escape; "": justo después de la barra; "uXXX": un \u a medias.
        self._escape: str | None = None
        self._high_surrogate: int | None = None

    def feed(self, chunk: str) -> str:
        out: list[str] = []
        for char in chunk:
            if self._in_string:
                self._string_char(char, out)
            elif char == '"':
                self._start_string()
            elif char in "{[":
                self._depth += 1
                self._expect_key = char == "{" and self._depth == 1
            elif char in "}]":
                self._depth -= 1
            elif self._depth == 1 and char == ",":
                self._expect_key = True
            elif self._depth == 1 and char == ":":
                self._expect_key = False
        return "".join(out)

    def _start_string(self) -> None:
        self._in_string = True
        self._string_is_key = self._depth == 1 and self._expect_key
        self._capturing = (
            self._depth == 1
            and not self._string_is_key
            and not self.complete
            and self._last_key == self.field
        )
        self._key_chars = []

    def _string_char(self, char: str, out: list[str]) -> None:
        if self._escape is not None:
            self._escape_char(char, out)
        elif char == "\\":
            self._escape = ""
        elif char == '"':
            self._in_string = False
            if self._string_is_key:
                self._last_key = "".join(self._key_chars)
                self._expect_key = False
            elif self._capturing:
                self._capturing = False
                self.complete = True
        else:
            self._emit(char, out)

    def _escape_char(self, char: str, out: list[str]) -> None:
        if self._escape == "":
            if char == "u":
                self._escape = "u"
                return
            self._escape = None
            self._emit(_SIMPLE_ESCAPES.get(char, char), out)
            return
        self._escape += char
        if len(self._escape) < 5:  # "u" y cuatro dígitos
            return
        digits, self._escape = self._escape[1:], None
        try:
            code = int(digits, 16)
        except ValueError:
            return
        if 0xD800 <= code <= 0xDBFF:
            # Primera mitad de un par sustituto (emojis): espera la segunda.
            self._high_surrogate = code
            return
        if 0xDC00 <= code <= 0xDFFF:
            if self._high_surrogate is None:
                return
            code = 0x10000 + ((self._high_surrogate - 0xD800) << 10) + (code - 0xDC00)
        self._emit(chr(code), out)

    def _emit(self, char: str, out: list[str]) -> None:
        # Una mitad de par sustituto sin pareja no se emite.
        self._high_surrogate = None
        if self._string_is_key:
            self._key_chars.append(char)
        elif self._capturing:
            out.append(char)


# ---------------------------------------------------------------------------
# Proveedor: claude -p en streaming
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Finished:
    """Fin de un intento: el evento `result` de claude -p, o por qué no llegó."""

    envelope: dict | None
    failure: str | None = None


@lru_cache
def _workdir() -> str:
    # Carpeta vacía y compartida: el modelo no tiene herramientas, así que nunca
    # escribe en ella; solo evita que `claude` lea un CLAUDE.md del proyecto.
    return tempfile.mkdtemp(prefix="ideographco-explicacion-vivo-")


class ClaudeCliStreamExplainer:
    """`claude -p` en streaming, con la llamada y la validación de `generator.py`."""

    def __init__(
        self,
        model: str,
        *,
        claude_bin: str | None = None,
        timeout_seconds: float = 300.0,
        effort: str | None = None,
        max_attempts: int = 2,
    ):
        self.model = model
        self._bin = claude_bin or shutil.which("claude")
        self._timeout = timeout_seconds
        self._effort = effort
        self._max_attempts = max(1, max_attempts)

    async def stream(self, request: ExplanationRequest) -> AsyncIterator[StreamEvent]:
        feedback = None
        for attempt in range(1, self._max_attempts + 1):
            finished = _Finished(None, "claude -p terminó sin respuesta")
            # aclosing: si cierran este stream a mitad de un intento, el intento
            # se cierra en el acto y mata su proceso (no espera al recolector).
            async with aclosing(
                self._attempt(explanation_user_prompt(request, feedback))
            ) as events:
                async for event in events:
                    if isinstance(event, _Finished):
                        finished = event
                    else:
                        yield event
            if finished.envelope is None:
                yield Failed(finished.failure or "claude -p terminó sin respuesta")
                return
            explanation, problem = parse_explanation(
                envelope_payload(finished.envelope),
                request,
                envelope_model(finished.envelope, self.model),
            )
            if explanation is not None:
                yield Done(explanation)
                return
            logger.warning(
                "Explicación en vivo rechazada (%s), intento %d de %d",
                problem,
                attempt,
                self._max_attempts,
            )
            feedback = problem
            if attempt < self._max_attempts:
                yield Retry(problem)
        yield Failed(f"la explicación no cumplió las reglas: {feedback}")

    async def _attempt(self, prompt: str) -> AsyncIterator[StreamEvent | _Finished]:
        command = claude_command(self._bin, self.model, effort=self._effort, stream=True)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=_workdir(),
                env=subprocess_env(),
                limit=LINE_LIMIT,
            )
        except OSError as error:
            raise ExplainerUnavailable(f"no se pudo ejecutar `claude`: {error}") from error

        stderr_task = asyncio.ensure_future(process.stderr.read())
        try:
            process.stdin.write(prompt.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()

            reader = _StreamJsonReader()
            envelope = None
            deadline = time.monotonic() + self._timeout
            while True:
                try:
                    line = await asyncio.wait_for(
                        process.stdout.readline(), timeout=max(0.0, deadline - time.monotonic())
                    )
                except TimeoutError:
                    logger.warning("claude -p en vivo superó %.0f s", self._timeout)
                    yield _Finished(None, f"claude -p superó {self._timeout:.0f} s")
                    return
                if not line:
                    break
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "result":
                    envelope = message
                    continue
                for event in reader.read(message):
                    yield event

            returncode = await process.wait()
            stderr = (await stderr_task).decode("utf-8", "replace")
            if returncode == 0 and envelope is not None and not envelope.get("is_error"):
                yield _Finished(envelope)
                return
            detail = " ".join(
                part for part in (str((envelope or {}).get("result", "")), stderr) if part
            )[:500]
            raise_if_unavailable(detail)
            logger.warning("claude -p en vivo falló (código %s): %s", returncode, detail[:200])
            yield _Finished(None, f"claude -p falló (código {returncode})")
        finally:
            await _stop(process, stderr_task)


class _StreamJsonReader:
    """Traduce los mensajes de stream-json de un intento a eventos de la mesa."""

    def __init__(self):
        self._thinking = False
        self._tool_index: int | None = None
        self._field: JsonFieldStream | None = None
        self._wrote = False

    def read(self, message: dict) -> list[StreamEvent]:
        if message.get("type") != "stream_event":
            return []
        event = message.get("event") or {}
        kind = event.get("type")
        if kind == "content_block_start":
            return self._block_start(event)
        if kind == "content_block_delta" and self._field is not None:
            delta = event.get("delta") or {}
            if delta.get("type") != "input_json_delta" or event.get("index") != self._tool_index:
                return []
            text = self._field.feed(delta.get("partial_json") or "")
            if text:
                self._wrote = True
                return [Delta(text)]
        return []

    def _block_start(self, event: dict) -> list[StreamEvent]:
        block = event.get("content_block") or {}
        if block.get("type") in ("thinking", "redacted_thinking"):
            if self._thinking:
                return []
            self._thinking = True
            return [Status("thinking")]
        if block.get("type") == "tool_use" and block.get("name") == STRUCTURED_OUTPUT_TOOL:
            events: list[StreamEvent] = []
            if self._wrote:
                # claude -p rechazó la llamada anterior (no cumplía el esquema) y el
                # modelo la repite: lo ya mostrado ya no vale.
                events.append(Retry("la respuesta no cumplía el esquema JSON"))
            self._tool_index = event.get("index")
            self._field = JsonFieldStream(EXPLANATION_FIELD)
            self._wrote = False
            events.append(Status("writing"))
            return events
        return []


async def _stop(process: asyncio.subprocess.Process, stderr_task: asyncio.Future) -> None:
    """Mata el proceso si sigue vivo y espera a que salga, aunque nos estén cancelando."""
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    stderr_task.cancel()
    with anyio.CancelScope(shield=True):
        await process.wait()


def get_stream_explainer(settings: Settings) -> ClaudeCliStreamExplainer:
    return ClaudeCliStreamExplainer(
        settings.desk_explanation_model or settings.explanation_model,
        claude_bin=settings.claude_bin or None,
        timeout_seconds=settings.explanation_timeout_seconds,
        effort=settings.explanation_effort or None,
    )
