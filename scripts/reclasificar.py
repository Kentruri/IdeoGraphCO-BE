"""CLI de reclasificación — para cuando cambia el modelo servido.

Pregunta al clasificador (CLASSIFIER_MODE) qué versión sirve y vuelve a
clasificar los artículos guardados con otra versión. Se puede cortar y
volver a correr: retoma donde quedó.

Ejemplos:
    python scripts/reclasificar.py --dry-run            # solo cuenta
    python scripts/reclasificar.py                      # reclasifica todo, lotes de 50
    python scripts/reclasificar.py --batch-size 20 --limit 200
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.classifier.client import get_classifier  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.db.session import get_session_factory, init_db  # noqa: E402
from src.ingestion.reclassify import DEFAULT_BATCH_SIZE, run_reclassify  # noqa: E402

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
    parser = argparse.ArgumentParser(
        description="Reclasifica los artículos guardados con otra versión del modelo"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo cuenta lo que se reclasificaría; no llama al modelo")
    parser.add_argument("--batch-size", type=_positive_int, default=DEFAULT_BATCH_SIZE,
                        help=f"Artículos por lote (se guarda al cerrar cada uno; default {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--limit", type=_positive_int, default=None,
                        help="Máximo de artículos en esta corrida (el resto queda para la siguiente)")
    args = parser.parse_args(argv)

    settings = get_settings()
    init_db()
    classifier = get_classifier(settings)

    session = get_session_factory()()
    try:
        stats = run_reclassify(
            session,
            classifier,
            batch_size=args.batch_size,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    finally:
        session.close()

    if stats.target_version is None:
        print(f"\nNo se reclasificó nada: {stats.aborted}")
        return 1

    print(f"\nModelo servido: {stats.target_version} (CLASSIFIER_MODE={settings.classifier_mode})")
    detail = f" ({stats.versions_summary()})" if stats.outdated else ""
    print(f"Clasificados con otra versión: {stats.outdated}{detail}")
    if stats.pending:
        print(f"Sin clasificar (pending, los reintenta la ingesta): {stats.pending}")

    if args.dry_run:
        would = stats.outdated if args.limit is None else min(args.limit, stats.outdated)
        print(f"[dry-run] Se reclasificarían {would} artículos; no se cambió nada.")
        return 0

    print(f"Resultado: {stats.summary()}")
    if stats.reclassified:
        share = stats.label_changed / stats.reclassified
        print(f"Cambiaron de clase {stats.label_changed} de {stats.reclassified} ({share:.1%})")
    for (old, new), count in stats.transitions.most_common():
        print(f"  {old or 'sin clase'} → {new}: {count}")
    if stats.aborted:
        print(f"Detenido: {stats.aborted}")
    elif stats.failures:
        print(f"{stats.failures} artículos conservan la clasificación anterior por fallos "
              "del clasificador; vuelve a correr el comando.")

    return 1 if (stats.aborted or stats.failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
