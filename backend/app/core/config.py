"""FONCIER+ — Configuration centralisée (FIX-03)"""
import os
from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    gemini_api_key: str | None = None
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False
    )

    DATABASE_URL: str = "postgresql+asyncpg://foncier:foncier@localhost:5432/foncier_dev"
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET: str = ""          # FIX-03 : vide par défaut, validator bloque au boot
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_EXPIRE_MINUTES: int = 120
    JWT_REFRESH_EXPIRE_DAYS: int = 7
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minio"
    MINIO_SECRET_KEY: str = "minio"
    SENTRY_DSN: str = ""
    ENV: str = "development"
    DEBUG: bool = False

    @field_validator("JWT_SECRET")
    @classmethod
    def check_jwt_secret(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "JWT_SECRET est vide — générer avec : openssl rand -hex 32"
            )
        if len(v) < 32:
            raise ValueError(
                f"JWT_SECRET trop court ({len(v)} chars) — minimum 32 requis"
            )
        return v


    @property
    def jwt_algorithm_actif(self) -> str:
        """Algorithme JWT actif (pour les logs de démarrage)."""
        return self.JWT_ALGORITHM

    @property
    def redis_blacklist_actif(self) -> bool:
        """True si Redis est configuré pour la blacklist JWT."""
        return bool(self.REDIS_URL and self.REDIS_URL != "redis://localhost:6379/0")

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()

# Alias global pour la compatibilité des imports
settings = get_settings()
