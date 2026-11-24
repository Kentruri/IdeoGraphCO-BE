"""CLI de las explicaciones de la clasificación.

Un LLM (`claude -p`, con la suscripción de Claude del usuario) redacta para
cada noticia clasificada un párrafo que dice qué elementos del texto
corresponden a su clase predicha y, si la segunda pesa (≥ 20 %), también a
esa. Se guarda una vez por clasificación: volver a correr el comando solo hace
las que faltan (las nuevas y las reclasificadas). Ejemplos:

    python scripts/explicar.py --dry-run               # cuántas faltan; no llama al LLM
    python scripts/explicar.py --slug <slug> --show    # una noticia, mostrando el texto
    python scripts/explicar.py --limit 3 --show        # piloto con las 3 primeras
    python scripts/explicar.py --concurrency 3         # todas las que faltan
    python scripts/explicar.py --model sonnet          # otro modelo de Claude
    python scripts/explicar.py --force                 # rehacer también las vigentes

Cada llamada gasta cuota de la suscripción: al final se informa cuántas se
hicieron y su costo equivalente a precio de API.
"""

import argparse
import logging
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from src.config import get_settings  # noqa: E402
from src.db.models import Story  # noqa: E402
from src.db.session import get_session_factory, init_db  # noqa: E402
from src.explanation.generator import get_explainer  # noqa: E402
from src.explanation.pipeline import run_explanations  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("debe ser un entero ≥ 1")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicaciones de la clasificación con un LLM")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo cuenta las que faltan; no llama al LLM")
    parser.add_argument("--limit", type=_positive_int, default=None,
                        help="Máximo de artículos en esta corrida")
    parser.add_argument("--slug", action="append", default=None,
                        help="Solo la noticia de esta story (se puede repetir)")
    parser.add_argument("--force", action="store_true",
                        help="Rehacer también las explicaciones vigentes")
    parser.add_argument("--model", default=None,
                        help="Modelo para `claude -p --model` (por defecto EXPLANATION_MODEL)")
    parser.add_argument("--concurrency", type=_positive_int, default=2,
                        help="Llamadas al LLM en paralelo (por defecto 2)")
    parser.add_argument("--show", action="store_true", help="Imprime cada explicación")
    args = parser.parse_args(argv)

    settings = get_settings()
    init_db()
    session = get_session_factory()()
    explainer = get_explainer(settings, model=args.model)
    try:
        article_ids = None
        if args.slug:
            stories = session.scalars(select(Story).where(Story.slug.in_(args.slug))).all()
            missing = set(args.slug) - {story.slug for story in stories}
            if missing:
                print(f"No existen esas stories: {', '.join(sorted(missing))}")
                return 1
            article_ids = [story.representative_article_id for story in stories]

        done = 0

        def report(article) -> None:
            nonlocal done
            done += 1
            classes = " + ".join(
                name for name in (article.explanation_basis["label"],
                                  article.explanation_basis.get("second")) if name
            )
            support = ", ".join(f"{k}={v}" for k, v in article.explanation_support.items())
            print(f"[{done}] {article.story.slug} · {classes} · apoyo {support}")
            if args.show:
                print(textwrap.indent(textwrap.fill(article.explanation_text, 96), "    "))

        stats = run_explanations(
            session,
            explainer,
            settings,
            limit=args.limit,
            force=args.force,
            dry_run=args.dry_run,
            article_ids=article_ids,
            concurrency=args.concurrency,
            on_saved=report,
        )
    finally:
        session.close()

    if args.dry_run:
        would = stats.pending if args.limit is None else min(args.limit, stats.pending)
        print(f"\n[dry-run] Faltan {stats.pending}; esta corrida haría {would}. "
              f"Modelo: {explainer.model}.")
        return 0

    print(f"\nResultado: {stats.summary()}")
    print(f"Llamadas a `claude -p --model {explainer.model}`: {explainer.calls} "
          f"(costo equivalente a precio de API: {explainer.cost_usd:.2f} USD)")
    if stats.aborted:
        print(f"Detenido: {stats.aborted}")
    elif stats.failures:
        print(f"{stats.failures} artículos quedaron sin explicación; vuelve a correr el comando.")
    return 1 if (stats.aborted or stats.failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
