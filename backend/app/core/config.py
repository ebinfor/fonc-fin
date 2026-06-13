import os
import sys

# 1. Récupération et conversion immédiate de l'URL pour TOUT le système
_raw_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/foncier")

if _raw_url.startswith("postgres://"):
    _raw_url = _raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _raw_url.startswith("postgresql://"):
    _raw_url = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# On force l'environnement système à adopter l'URL convertie
os.environ["DATABASE_URL"] = _raw_url

# 2. Importation sécurisée de Pydantic (compatible V1 et V2)
try:
    from pydantic_settings import BaseSettings
except ImportError:
    from pydantic import BaseSettings

# 3. Définition de la configuration globale de l'application
class Settings(BaseSettings):
    PROJECT_NAME: str = "FONCIER+"
    DATABASE_URL: str = _raw_url
    JWT_SECRET: str = os.getenv("JWT_SECRET", "fb3a7c82d901b4e5f6e7890123456789a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6")

    class Config:
        env_file = ".env"
        extra = "ignore"

# Instanciation globale requise par les endpoints (auth, monitoring, etc.)
settings = Settings()