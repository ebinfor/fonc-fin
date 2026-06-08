"""FONCIER+ — Session async SQLAlchemy (FIX-08 : export engine explicite)"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool
from app.core.config import get_settings

settings = get_settings()

# Exporté explicitement pour admin.py health check
is_sqlite_memory = settings.DATABASE_URL.startswith("sqlite") and ":memory:" in settings.DATABASE_URL
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False} if is_sqlite_memory else {},
    poolclass=StaticPool if is_sqlite_memory else None,
)

# --- INTERCEPTEUR MAGIQUE POUR SQUEEZER POSTGIS SUR SQLITE ---
@event.listens_for(engine.sync_engine, "before_cursor_execute")
def cancel_extension(conn, cursor, statement, parameters, context, executing_style):
    if "EXTENSION" in statement or "postgis" in statement:
        # On remplace l'instruction PostgreSQL par un SELECT inoffensif pour SQLite
        cursor.execute("SELECT 1;")

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