"""Qué clasificación explica cada texto guardado, y si sigue vigente.

Módulo liviano (sin LLM ni subprocesos) porque también lo usa el adaptador de
la API.

Cada explicación se escribe para UNA clasificación: la clase predicha, la
segunda si pesaba lo bastante para cubrirla, y la versión del modelo. Si el
artículo se reclasifica con otro modelo o cambia de clase, el texto guardado ya
no habla de lo que muestra la interfaz: la API deja de servirlo y
`scripts/explicar.py` lo vuelve a generar.
"""

from src.core.schema import ARTICLE_CLASSIFICATION_DONE
from src.db.models import Article


def classes_to_explain(
    label: str, probabilities: dict[str, float], second_min_probability: float
) -> tuple[str, str | None]:
    """La clase predicha y, si su probabilidad llega al umbral, la segunda."""
    others = sorted(
        ((probability, name) for name, probability in probabilities.items() if name != label),
        key=lambda item: (-item[0], item[1]),
    )
    if not others or others[0][0] < second_min_probability:
        return label, None
    return label, others[0][1]


def is_classified(article: Article) -> bool:
    return (
        article.classification_status == ARTICLE_CLASSIFICATION_DONE
        and bool(article.classification_label)
        and bool(article.classification_probabilities)
    )


def basis_for(article: Article, second_min_probability: float) -> dict | None:
    """La clasificación que explicaría un texto escrito hoy para el artículo."""
    if not is_classified(article):
        return None
    label, second = classes_to_explain(
        article.classification_label,
        article.classification_probabilities,
        second_min_probability,
    )
    return {
        "label": label,
        "second": second,
        "model_version": article.classification_model_version,
    }


def is_current(article: Article) -> bool:
    """True si el texto guardado explica la clasificación vigente del artículo.

    Se compara la clase y la versión del modelo, no el umbral de la segunda
    clase: si el umbral cambia en la configuración, las explicaciones ya
    escritas se siguen sirviendo y `scripts/explicar.py --force` las rehace.
    """
    basis = article.explanation_basis
    return (
        is_classified(article)
        and bool(article.explanation_text)
        and article.explanation_generated_at is not None
        and isinstance(basis, dict)
        and basis.get("label") == article.classification_label
        and basis.get("model_version") == article.classification_model_version
    )
