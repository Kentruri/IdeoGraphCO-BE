"""Aplicación FastAPI (solo lectura: la escritura ocurre en el cron).

Las rutas POST son de la mesa de redacción: /api/v1/classify clasifica un texto
sin guardarlo; /api/v1/explain lo clasifica y transmite su explicación en vivo
(SSE), también sin guardar nada; /api/v1/explanation-reviews guarda la ficha de
revisión de esa explicación. Las dos últimas solo con DESK_EXPLANATION_MODE.

Arranque local:
    uvicorn src.api.app:app --reload --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.deps import DESK_EXPLANATIONS_DISABLED_DETAIL, DeskExplanationsDisabled
from src.api.routes import classify, explain, health, reviews, stories
from src.config import APP_VERSION, get_settings
from src.db.session import init_db
from src.schemas.dto import FeatureDisabledErrorDTO


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
    app.include_router(explain.router)
    app.include_router(reviews.router)

    @app.exception_handler(DeskExplanationsDisabled)
    async def desk_explanations_disabled(_request: Request, _exc: DeskExplanationsDisabled):
        error = FeatureDisabledErrorDTO(detail=DESK_EXPLANATIONS_DISABLED_DETAIL)
        return JSONResponse(status_code=503, content=error.model_dump())

    return app


app = create_app()
