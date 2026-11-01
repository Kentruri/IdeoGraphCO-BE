"""CLI de ingesta — lo que corre el cron de GitHub Actions.

Ejemplos:
    python scripts/ingest.py                                  # todas las fuentes, 5 c/u
    python scripts/ingest.py --sources eltiempo lasillavacia  # fuentes puntuales
    python scripts/ingest.py --categories nacional regional --max-per-source 3
    python scripts/ingest.py --no-filter                      # sin filtro LLM (pruebas)
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
from src.ingestion.pipeline import run_ingest  # noqa: E402
from src.ingestion.sources import SOURCES  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingesta de noticias políticas colombianas")
    parser.add_argument("--sources", nargs="*", choices=sorted(SOURCES.keys()), default=None)
    parser.add_argument("--categories", nargs="*", default=None)
    parser.add_argument("--max-per-source", type=int, default=5)
    parser.add_argument("--no-filter", action="store_true", help="Desactiva el filtro LLM")
    args = parser.parse_args()

    settings = get_settings()
    init_db()
    classifier = get_classifier(settings)

    session = get_session_factory()()
    try:
        stats = run_ingest(
            session,
            settings,
            classifier,
            source_names=args.sources,
            categories=args.categories,
            max_per_source=args.max_per_source,
            use_filter=not args.no_filter,
        )
    finally:
        session.close()

    print(f"\nResultado: {stats.summary()}")
    if stats.errors:
        print("Errores:")
        for error in stats.errors:
            print(f"  - {error}")

    # El run del cron falla solo si NADA se pudo hacer (todo error)
    return 1 if (stats.saved == 0 and stats.errors and stats.discovered == 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
