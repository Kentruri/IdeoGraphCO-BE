"""Aplicación FastAPI (solo lectura: la escritura ocurre en el cron).

La única ruta POST, /api/v1/classify, clasifica un texto sin guardarlo.

Arranque local:
    uvicorn src.api.app:app --reload --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import classify, health, stories
from src.config import APP_VERSION, get_settings
from src.db.session import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="IdeoGraphCO API",
        version=APP_VERSION,
        description=(
            "API de lectura de noticias políticas colombianas con su "
            "clasificación ideológica (8 clases)."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(stories.router)
    app.include_router(classify.router)
    return app


app = create_app()
