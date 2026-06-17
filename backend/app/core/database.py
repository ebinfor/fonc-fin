import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.core.config import settings

# 🚀 Sécurité absolue sur l'URL de connexion
db_url = str(settings.DATABASE_URL)

if db_url.startswith("postgres://"):
    final_db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif db_url.startswith("postgresql://"):
    final_db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
else:
    final_db_url = db_url

# Création du moteur asynchrone SQLAlchemy
engine = create_async_engine(
    final_db_url, 
    echo=False, 
    future=True,
    pool_pre_ping=True
)

# Configuration de la fabrique de sessions
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()
async_session_factory = AsyncSessionLocal

# 🎯 La fonction indispensable requise par FastAPI et les endpoints
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
from contextlib import asynccontextmanager

@asynccontextmanager
async def database_session_scope():
    """Gestionnaire de contexte asynchrone pour les tâches de fond (Lifespan).
    Permet de créer une session isolée, de commit automatiquement ou de rollback en cas d'erreur.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()