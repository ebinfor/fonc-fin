import os
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine
)
from sqlalchemy.orm import declarative_base

# 1. Connexion publique sécurisée Railway
DATABASE_URL = "postgresql+asyncpg://postgres:VOFGKWzLrAYzjOYtdVlYoKrmhAFwGiDY@hopper.proxy.rlwy.net:21510/railway"

# 2. Création du moteur asynchrone
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20
)

# 3. Fabrique de sessions
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# 4. Modèle déclaratif ORM
Base = declarative_base()

# 5. 🌟 LA SOLUTION UNIVERSELLE : Un vrai Context Manager utilisable partout
@asynccontextmanager
async def database_session_scope():
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

# Dépendance pour FastAPI (Générateur asynchrone standard pour les routes)
import contextlib
# ... tes autres imports (engine, AsyncSessionLocal, etc.) ...

@contextlib.asynccontextmanager
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()