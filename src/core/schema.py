"""Constantes compartidas del dominio.

IDEOLOGY_CLASSES debe mantenerse alineada con `src.core.schema` del repo
IdeoGraphCO (pipeline ML) y con `src/types/ideology.ts` del frontend.
"""

IDEOLOGY_CLASSES: list[str] = [
    "personalismo",
    "institucionalismo",
    "populismo",
    "doctrinarismo",
    "soberanismo",
    "globalismo",
    "conservadurismo",
    "progresismo",
]

SOURCE_CATEGORIES: list[str] = [
    "nacional",
    "independiente",
    "regional",
    "institucional",
    "judicial",
    "opinion",
    "gremial",
]

# Estados del ciclo de vida
ARTICLE_CLASSIFICATION_PENDING = "pending"
ARTICLE_CLASSIFICATION_DONE = "classified"
ARTICLE_CLASSIFICATION_FAILED = "failed"

STORY_STATUS_INGESTED = "ingested"    # aún sin clasificación del representativo
STORY_STATUS_PUBLISHED = "published"  # visible en la API pública

# Palabras por minuto para reading_time (mismo criterio que el FE)
WORDS_PER_MINUTE = 200

# Longitud del snippet público (el body completo nunca se expone)
SNIPPET_MAX_CHARS = 320
