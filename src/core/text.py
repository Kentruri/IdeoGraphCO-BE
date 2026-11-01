"""Normalización de texto para búsqueda.

La búsqueda debe ser insensible a tildes y mayúsculas, y comportarse IGUAL
en SQLite (dev) y Postgres (prod). Ni lower() de SQLite ni el de Postgres
pliegan acentos, así que normalizamos NOSOTROS al ingerir (columna
articles.search_text) y normalizamos el término de búsqueda con la misma
función al consultar.
"""

import unicodedata


def normalize_for_search(text: str) -> str:
    """minúsculas + sin tildes + espacios colapsados."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return " ".join(stripped.split())


def escape_like(needle: str, escape_char: str = "\\") -> str:
    """Escapa los comodines de LIKE (% y _) en input de usuario."""
    return (
        needle.replace(escape_char, escape_char + escape_char)
        .replace("%", escape_char + "%")
        .replace("_", escape_char + "_")
    )
