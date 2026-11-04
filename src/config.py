"""Configuración central del backend (variables de entorno).

Local: SQLite + clasificador mock, sin claves.
Producción: Supabase Postgres + Gemini + clasificador HTTP (IdeoGraphCO).
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./data/ideographco.db"

    # Filtro de politicidad (Gemini) de la ingesta y de la mesa de redacción.
    # Sin key, el filtro se desactiva: la mesa clasifica todo texto.
    gemini_api_key: str = ""
    filter_model: str = "gemini-2.5-flash-lite"
    filter_escalate_model: str = "gemini-2.5-flash"
    filter_escalate_threshold: float = 0.7
    # Segundos entre llamadas al filtro (free tier Gemini ≈ 15 RPM).
    filter_rate_limit_seconds: float = 4.5
    # Tope por llamada a Gemini cuando la mesa de redacción (POST /classify)
    # pasa el texto por el filtro. Con escalado son hasta dos llamadas.
    classify_filter_timeout_seconds: float = 10.0

    # Clasificador ideológico externo (repo IdeoGraphCO).
    classifier_mode: Literal["mock", "http", "off"] = "mock"
    classifier_api_url: str = ""
    classifier_timeout_seconds: float = 30.0

    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
