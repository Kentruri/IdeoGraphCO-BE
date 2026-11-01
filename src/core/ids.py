"""IDs y slugs estables y deterministas.

`article_id` replica el criterio del repo IdeoGraphCO (pipeline ML): misma
URL → mismo ID siempre, lo que permite cruzar artículos entre el corpus de
la tesis y esta base de datos.
"""

import hashlib
import re
import unicodedata
import uuid

ID_LENGTH = 16


def article_id(url: str) -> str:
    """ID determinista de 16 chars hex desde la URL del artículo."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:ID_LENGTH]


def new_story_id() -> str:
    return f"st_{uuid.uuid4().hex[:20]}"


def slugify(title: str, unique_suffix: str) -> str:
    """Slug URL-safe: minúsculas sin tildes + sufijo único corto.

    El sufijo (derivado del id del artículo representativo) garantiza
    unicidad aunque dos historias compartan titular.
    """
    normalized = unicodedata.normalize("NFKD", title)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    base = ascii_text[:180].rstrip("-") or "noticia"
    return f"{base}-{unique_suffix[:8]}"
