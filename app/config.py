"""Application settings, loaded from environment / .env.

Provider credentials live here and nowhere else. Nothing in this module is
serialised to the frontend; the ``/api/v1/providers`` endpoint exposes only
provider names, availability and cost class.
"""

from __future__ import annotations

import functools
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---------------------------------------------------------
    app_name: str = "VIN Decoder"
    environment: str = "development"
    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    # --- Database ------------------------------------------------------------
    database_url: str = ""
    db_echo: bool = False

    # --- Caching -------------------------------------------------------------
    cache_enabled: bool = True
    cache_ttl_hours: int = 720

    # --- Providers -----------------------------------------------------------
    nhtsa_enabled: bool = True
    nhtsa_base_url: str = "https://vpic.nhtsa.dot.gov/api/vehicles"
    nhtsa_timeout_seconds: float = 15.0

    spec_catalog_enabled: bool = True

    autodev_enabled: bool = False
    autodev_api_key: str = ""
    autodev_base_url: str = "https://auto.dev/api"
    autodev_timeout_seconds: float = 15.0
    autodev_cost_per_call: float = 0.02

    # --- Cost policy ---------------------------------------------------------
    prefer_free_providers: bool = True
    max_commercial_calls_per_day: int = 250

    # --- Limits --------------------------------------------------------------
    max_vins_per_request: int = Field(default=100, ge=1, le=1000)
    decode_concurrency: int = Field(default=8, ge=1, le=32)

    # --- Rate limiting -------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def resolved_database_url(self) -> str:
        """PostgreSQL when configured, otherwise a local SQLite file.

        The SQLite fallback exists so the app is runnable with zero
        infrastructure. Schema, ORM models and repository code are identical
        for both backends.
        """
        if self.database_url.strip():
            return self.database_url.strip()
        return f"sqlite:///{(BASE_DIR / 'vin_decoder.sqlite3').as_posix()}"

    @property
    def using_sqlite(self) -> bool:
        return self.resolved_database_url.startswith("sqlite")

    @property
    def autodev_configured(self) -> bool:
        return bool(self.autodev_enabled and self.autodev_api_key.strip())


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()
