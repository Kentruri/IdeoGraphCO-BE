"""CLI de triangulación: busca coberturas, agrupa el mismo hecho y redacta la síntesis.

`scripts/ingest.py` la corre sola al terminar cada ingesta (cron incluido);
este CLI sirve para correrla aparte. Ejemplos:
    python scripts/triangular.py --dry-run              # coincidencias y grupos, sin tocar la BD
    python scripts/triangular.py --no-search            # solo con lo que ya está en la BD
    python scripts/triangular.py --no-synthesis         # buscar y agrupar, sin LLM
    python scripts/triangular.py --limit 5              # sintetizar como mucho 5 stories

Sin GEMINI_API_KEY busca y agrupa, pero no filtra lo descargado ni sintetiza.
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
from src.ingestion.sources import source_display_name  # noqa: E402
from src.triangulation.pipeline import load_pool, run_triangulation, utcnow  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Triangulación de noticias")
    parser.add_argument("--days", type=float, default=7, help="Antigüedad máxima (0 = todo)")
    parser.add_argument("--threshold", type=float, default=0.25, help="Coseno mínimo")
    parser.add_argument("--window-hours", type=float, default=48)
    parser.add_argument("--max-sources", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None, help="Máximo de síntesis")
    parser.add_argument("--no-synthesis", action="store_true")
    parser.add_argument("--no-search", action="store_true", help="No buscar coberturas nuevas")
    parser.add_argument("--max-queries", type=int, default=60, help="Noticias a buscar por corrida")
    parser.add_argument("--dry-run", action="store_true", help="Solo muestra los grupos")
    args = parser.parse_args()

    settings = get_settings()
    init_db()
    session = get_session_factory()()
    try:
        stats, groups = run_triangulation(
            session,
            settings,
            get_classifier(settings),
            days=args.days or None,
            threshold=args.threshold,
            window_hours=args.window_hours,
            max_sources=args.max_sources,
            do_synthesis=not args.no_synthesis,
            dry_run=args.dry_run,
            limit=args.limit,
            search=not args.no_search,
            max_queries=args.max_queries,
        )
        if args.dry_run:
            from datetime import timedelta

            since = utcnow() - timedelta(days=args.days) if args.days else None
            titles = {article.id: article for article in load_pool(session, since)}
            for group in groups:
                print(f"\n[{len(group.article_ids)} medios · cohesión {group.cohesion}]")
                for article_id in group.article_ids:
                    article = titles[article_id]
                    print(f"  {source_display_name(article.source_name):<22} {article.title[:90]}")
    finally:
        session.close()

    print(f"\nResultado: {stats.summary()}")
    return 0 if not stats.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
