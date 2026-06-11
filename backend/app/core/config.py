import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "FONCIER+"
    API_V1_STR: str = "/v1"
    
    # 🌟 URL Publique TCP Railway validée pour SQLAlchemy + Asyncpg
    DATABASE_URL: str = "postgresql+asyncpg://postgres:VOFGKWzLrAYzjOYtdVlYoKrmhAFwGiDY@hopper.proxy.rlwy.net:21510/railway"
    
    ENV: str = "production"
    DEBUG: bool = False
    SENTRY_DSN: str | None = None
    jwt_algorithm_actif: bool = True
    redis_blacklist_actif: bool = True

    @property
    def is_production(self) -> bool:
        return True

    class Config:
        case_sensitive = True

settings = Settings()

def get_settings() -> Settings:
    return settings