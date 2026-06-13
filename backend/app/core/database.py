import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.core.config import settings

# 1. Extraction et nettoyage de l'URL (Priorité absolue à l'environnement système de Railway)
raw_db_url = os.getenv("DATABASE_URL", settings.DATABASE_URL)

if raw_db_url.startswith("postgres://"):
    final_db_url = raw_db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif raw_db_url.startswith("postgresql://"):
    final_db_url = raw_db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
else:
    final_db_url = raw_db_url

# 2. Création de l'engine avec l'URL dynamique et robuste
print(f"\n🚨🚨🚨 [DIAGNOSTIC] FONCIER+ TENTE DE SE CONNECTER À : {final_db_url.split('@')[-1] if '@' in final_db_url else final_db_url}\n", flush=True)
engine = create_async_engine(
    final_db_url, 
    echo=False, 
    future=True,
    pool_pre_ping=True
)

# 3. Maintien des sessions et exports requis (Workflow & Endpoints)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()
async_session_factory = AsyncSessionLocal  # Conservé pour le moteur de workflow