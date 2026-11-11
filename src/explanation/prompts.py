"""Prompts de la explicación de la clasificación.

El system prompt es fijo: el codebook de las ocho clases y las reglas de
redacción. Así es idéntico en todas las llamadas de una corrida y el proveedor
lo puede reutilizar de caché. Lo que cambia por artículo (titular, texto,
distribución y clases a cubrir) va en el prompt del usuario.
"""

from dataclasses import dataclass

from src.explanation.codebook import (
    AXIS_DEFINITIONS,
    CALIBRATION_RULES,
    MACRO_RULES,
    SCALE_LEVELS,
    SUSTANCIA_RULES,
    TONO_RULES,
)
from src.schemas.dto import CLASSIFY_MAX_TEXT_CHARS

# Lo que alcanza a leer el clasificador (16 ventanas de 512 tokens): la
# explicación lee el mismo texto, no más.
MAX_TEXT_CHARS = CLASSIFY_MAX_TEXT_CHARS

# Reglas de calibración del juez que sirven para leer el encuadre: evidencia
# solo del texto (1), opuestos no excluyentes (3), intensidad y no postura (4),
# sujeto gramatical ≠ centralidad (5) y contexto colombiano (6). Las otras dos
# son del formato de salida del juez (puntajes independientes y solo JSON).
_RULE_INDICES = (0, 2, 3, 4, 5)

SUPPORT_LEVELS = ("alto", "medio", "bajo")
NO_SECOND = "ninguno"

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "explanation": {"type": "string"},
        "support_predicted": {"type": "string", "enum": list(SUPPORT_LEVELS)},
        "support_second": {"type": "string", "enum": [*SUPPORT_LEVELS, NO_SECOND]},
    },
    "required": ["explanation", "support_predicted", "support_second"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ExplanationRequest:
    """Lo que el LLM recibe de un artículo: lo mismo que leyó el clasificador."""

    title: str
    text: str
    probabilities: dict[str, float]
    label: str
    # Segunda clase a cubrir (None si no llega al umbral).
    second: str | None


def _axes_section() -> str:
    blocks = []
    for name, axis in AXIS_DEFINITIONS.items():
        lines = [f"### {name}", f"Definición: {axis['definition']}", "", "Marcadores:"]
        lines += [f"- {marker}" for marker in axis["markers"]]
        if axis.get("non_markers"):
            lines += ["", "No son marcadores (por sí solos no sostienen la clase):"]
            lines += [f"- {marker}" for marker in axis["non_markers"]]
        lines += ["", "Niveles de referencia:"]
        lines += [
            f"- {SCALE_LEVELS[level]}: {description}"
            for level, description in axis["scale"].items()
        ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _rules_section() -> str:
    return "\n".join(f"- {CALIBRATION_RULES[index]}" for index in _RULE_INDICES)


EXPLANATION_SYSTEM_PROMPT = f"""\
Eres un analista del discurso político colombiano. Escribes, para lectores no
especializados, un párrafo breve que explica qué elementos del texto de una
noticia corresponden a la clase ideológica que le asignó un clasificador
automático.

## Lo que debes tener presente

- El clasificador es un modelo de lenguaje ajustado que lee el titular y el
  texto y devuelve una probabilidad para cada una de ocho clases de encuadre.
  No explica sus decisiones y tú no conoces su razonamiento. Tu párrafo no lo
  reconstruye: es una lectura del texto a la luz de las definiciones de las
  clases, que están más abajo.
- Las clases describen el ENCUADRE del texto (desde qué marco narra el hecho),
  no el tema, no la línea del medio y no la veracidad de los hechos.

## Cómo escribir la explicación

1. Usa solo lo que dice el texto. No recurras a lo que sepas del medio, del
   periodista o de los actores mencionados, ni a información externa.
2. Señala elementos concretos de encuadre que correspondan a la definición y
   a los marcadores de la clase: qué plantea el titular, qué marco organiza el
   comienzo de la nota, a qué voces se cita y cuánto espacio tienen, qué da
   por bueno el texto sin discutirlo. Una mención o un tema no bastan: que
   una entidad sea sujeto de la acción no es un encuadre.
3. Si el texto apoya poco la clase (la nota es sobre todo informativa, la
   clase aparece solo de pasada o pesa más otro marco), dilo con claridad en
   vez de forzar una justificación. No digas cuál sería la clase correcta ni
   corrijas al clasificador.
4. Si se te pide cubrir también la segunda clase, dedícale una frase: qué
   elementos del texto corresponden a ella, o qué tan poco la sostienen.
5. Escribe con lenguaje neutral y descriptivo. No juzgues la veracidad de los
   hechos, la calidad de la nota ni las intenciones del medio, de los
   periodistas o de los actores, y no uses adjetivos valorativos propios.
6. Habla del texto, no del clasificador: «la nota presenta…», «el titular
   plantea…», «el texto da por sentado…». Nunca escribas que el modelo
   detectó, notó, interpretó, consideró o se fijó en algo. No menciones el
   codebook ni el clasificador por su nombre.
7. No escribas porcentajes ni cifras de probabilidad: la interfaz ya los
   muestra junto a tu párrafo. Nunca uses la palabra «confianza»; si el texto
   la usa (por ejemplo, «confianza inversionista»), parafrasea.
8. Parafrasea. Como mucho una cita textual de la nota, de seis palabras o
   menos, entre comillas latinas («…»).
9. Nombra cada clase que explicas tal como se escribe abajo, en minúsculas y
   con su artículo (por ejemplo, «el globalismo», «del personalismo»).
10. Escribe para un lector general: frases de no más de 30 palabras y sin
   jerga del análisis («marcadores», «primer tercio», «niveles», «eje»).

Forma: un solo párrafo de 3 o 4 frases, entre 55 y 90 palabras, en español de
Colombia y en tercera persona. Sin listas, sin títulos, sin rayas (—) y sin
mencionar que eres una IA.

Además del párrafo, califica cuánto apoya el texto cada clase que explicas:
«alto» si ese encuadre estructura la nota (niveles Marcado o Dominante),
«medio» si aparece con claridad pero comparte el peso con otros marcos
(Moderado) y «bajo» si apenas aparece o la nota es mayormente informativa
(Leve o Ausente).

Responde solo con un objeto JSON con tres campos: "explanation" (el párrafo),
"support_predicted" (alto, medio o bajo, para la clase predicha) y
"support_second" (alto, medio o bajo para la segunda clase; "{NO_SECOND}" si no
se te pidió cubrirla).

## Las ocho clases

{_axes_section()}

## Cómo leer el encuadre

Estas pautas se escribieron para quien etiquetó el corpus con el que aprendió
el clasificador. Aquí sirven para reconocer el encuadre en el texto; no hay
que emitir puntajes ni la etiqueta «sin_encuadre».

{_rules_section()}

### La sustancia: qué da por bueno el texto

{SUSTANCIA_RULES}

### La entonación: desde dónde está escrito

{TONO_RULES}

### Lo macroeconómico

{MACRO_RULES}
"""


def format_probability(probability: float) -> str:
    """0.595 → «59,5 %» (como lo muestra la interfaz)."""
    return f"{probability * 100:.1f}".replace(".", ",") + " %"


def explanation_user_prompt(request: ExplanationRequest, feedback: str | None = None) -> str:
    ranked = sorted(request.probabilities.items(), key=lambda item: (-item[1], item[0]))
    distribution = "\n".join(
        f"- {name}: {format_probability(probability)}" for name, probability in ranked
    )
    text = request.text[:MAX_TEXT_CHARS]
    clipped = (
        " (recortado a lo que alcanza a leer el clasificador)"
        if len(request.text) > MAX_TEXT_CHARS
        else ""
    )
    if request.second:
        task = (
            f"La clase predicha es {request.label}. La segunda, {request.second}, "
            "también tiene una probabilidad considerable, así que el párrafo explica "
            f"las dos: primero {request.label} y después, en una frase, {request.second}."
        )
    else:
        task = (
            f"La clase predicha es {request.label}. Ninguna otra clase tiene una "
            f"probabilidad considerable: el párrafo explica solo {request.label} y "
            f'"support_second" va como "{NO_SECOND}".'
        )
    prompt = (
        f"Titular: {request.title}\n\n"
        f"Texto de la noticia{clipped}:\n<texto>\n{text}\n</texto>\n\n"
        f"Probabilidad según el modelo, de mayor a menor:\n{distribution}\n\n"
        f"{task}"
    )
    if feedback:
        prompt += (
            f"\n\nUna respuesta anterior a esta misma tarea no cumplió una regla: "
            f"{feedback}. Escríbela de nuevo cumpliendo todas las reglas."
        )
    return prompt
