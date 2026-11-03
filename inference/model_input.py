"""Composición canónica del texto que consume el modelo (copia exacta).

Copia literal de `src/core/text.py` del repo IdeoGraphCO (commit ca25f39),
la fuente única que usaron el jurado del Silver set y el entrenamiento:
titular sin el sufijo del medio + línea en blanco + cuerpo. Si el servicio
compusiera la entrada de otra forma, el modelo vería en producción algo
distinto de lo que aprendió, sin ningún error visible
(docs/traspaso-inferencia-BE.md, regla 2).

Ningún test puede cruzar la frontera entre repositorios: si cambia allá,
se vuelve a copiar aquí, sin editar.
"""

from __future__ import annotations

import re

# Cola tras un separador al final del titular: candidata a nombre del medio.
_TITLE_TAIL = re.compile(r"\s+[|–—·]\s+([^|–—·]{1,45})$|\s+-\s+([^-]{1,45})$")

# Palabras funcionales que un nombre de medio puede llevar en minúscula
# ("Concejo de Medellín", "Voz de América").
_MINOR_WORDS = {
    "de", "del", "la", "el", "los", "las", "y", "e", "en", "al", "a",
    "para", "por", "con", "the", "of",
}

_SENTENCE_END = (".", "?", "!", ":", ";", ",")


def strip_outlet_suffix(title: str) -> str:
    """Quita el nombre del medio del final del titular, si lo lleva.

    Solo corta cuando la cola parece un NOMBRE (todas sus palabras
    capitalizadas o funcionales) y no un fragmento de la frase: así
    "… | CONtexto Ganadero" y "… - Concejo de Medellín" se limpian, pero
    "La reforma pensional - qué sigue ahora" queda intacto.
    """
    title = (title or "").strip()
    match = _TITLE_TAIL.search(title)
    if not match:
        return title

    tail = (match.group(1) or match.group(2) or "").strip()
    if not tail or tail.endswith(_SENTENCE_END):
        return title

    words = [w for w in tail.split() if w]
    if not words:
        return title
    looks_like_name = all(
        word[0].isupper() or word.lower() in _MINOR_WORDS for word in words
    )
    if not looks_like_name:
        return title

    stripped = title[: match.start()].rstrip()
    # No dejar el titular vacío ni reducirlo a un resto inservible.
    return stripped if len(stripped) >= 10 else title


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip().lower()


def build_model_input(title: str | None, text: str) -> str:
    """Texto canónico: `titular limpio` + línea en blanco + cuerpo.

    Si el cuerpo ya arranca con el titular (trafilatura lo incluye en unos
    medios y en otros no — 10 de 47 artículos en la auditoría de ago-2026),
    esa primera línea se reemplaza por la versión limpia, para que la forma
    del input no dependa del CMS de la fuente.
    """
    body = (text or "").strip()
    clean_title = strip_outlet_suffix(title or "")
    if not clean_title:
        return body
    if not body:
        return clean_title

    first_line, _, rest = body.partition("\n")
    if _normalize(strip_outlet_suffix(first_line)) == _normalize(clean_title):
        body = rest.lstrip("\n")

    return f"{clean_title}\n\n{body}" if body else clean_title
