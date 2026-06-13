import os

# Dans ta classe Settings(BaseSettings):
_raw_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/foncier")

# Si Railway injecte une URL classique, on la convertit à la volée pour asyncpg
if _raw_url.startswith("postgres://"):
    DATABASE_URL: str = _raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _raw_url.startswith("postgresql://"):
    DATABASE_URL: str = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
else:
    DATABASE_URL: str = _raw_url