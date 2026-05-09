"""FONCIER+ — Session async SQLAlchemy (FIX-08 : export engine explicite)"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine
)
from sqlalchemy.orm import DeclarativeBase
from app.core.config import get_settings

settings = get_settings()

# Exporté explicitement pour admin.py health check
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    echo=settings.DEBUG,
)

async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    """Base unique pour tous les modèles SQLAlchemy.
    Importée par tous les fichiers app/models/*.py
    """
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — une session par requête HTTP."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context():
    """Context manager pour les scripts hors FastAPI (seed, migration, tests)."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

# Alias pour le MonitoringEngine
get_db_session = get_db_context
