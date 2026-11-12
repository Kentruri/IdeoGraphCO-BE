"""CLI de ingesta — lo que corre el cron de GitHub Actions.

Ejemplos:
    python scripts/ingest.py                                  # todas las fuentes, 5 c/u
    python scripts/ingest.py --sources eltiempo lasillavacia  # fuentes puntuales
    python scripts/ingest.py --categories nacional regional --max-per-source 3
    python scripts/ingest.py --no-filter                      # sin filtro LLM (pruebas)
    python scripts/ingest.py --no-triangulation               # sin buscar/agrupar después
    python scripts/ingest.py --no-explanations                # sin explicar las nuevas

Al terminar, la triangulación corre sola (src/triangulation): busca otras
coberturas de las noticias recientes, agrupa el mismo hecho y, con ≥3 medios
y GEMINI_API_KEY, redacta la versión triangulada. Después, con
EXPLANATION_MODE=claude_cli, se escribe la explicación de cada noticia nueva
(src/explanation); con el valor por defecto (off) quedan para
scripts/explicar.py.
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
    parser.add_argument(
        "--no-triangulation", action="store_true", help="No triangular al terminar la ingesta"
    )
    parser.add_argument(
        "--no-explanations",
        action="store_true",
        help="No escribir las explicaciones al terminar (aunque EXPLANATION_MODE las pida)",
    )
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
        if not args.no_triangulation:
            from src.triangulation.pipeline import run_triangulation

            try:
                tri_stats, _groups = run_triangulation(session, settings, classifier, search=True)
                print(f"\nTriangulación: {tri_stats.summary()}")
            except Exception:  # noqa: BLE001 — la triangulación no tumba la ingesta
                session.rollback()
                logging.getLogger(__name__).exception("La triangulación falló")
        # Después de triangular: así también se explican las coberturas que
        # trajo la búsqueda dirigida.
        if not args.no_explanations and settings.explanation_mode != "off":
            from src.explanation.generator import get_explainer
            from src.explanation.pipeline import run_explanations

            try:
                explanation_stats = run_explanations(session, get_explainer(settings), settings)
                print(f"\nExplicaciones: {explanation_stats.summary()}")
            except Exception:  # noqa: BLE001 — las explicaciones no tumban la ingesta
                session.rollback()
                logging.getLogger(__name__).exception("Las explicaciones fallaron")
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
