import os

# Dans ta classe Settings(BaseSettings):
_raw_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:VOFGKWzLrAYzjOYtdVlYoKrmhAFwGiDY@hopper.proxy.rlwy.net:21510/railway")

# Si Railway injecte une URL classique commençant par postgres://, on la convertit à la volée pour asyncpg
if _raw_url.startswith("postgres://"):
    DATABASE_URL: str = _raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
else:
    DATABASE_URL: str = _raw_url