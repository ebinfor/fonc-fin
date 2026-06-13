import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.core.config import settings

# 🚀 Sécurité absolue : On extrait et on convertit l'URL à la milliseconde près du branchement
db_url = str(settings.DATABASE_URL)

if db_url.startswith("postgres://"):
    final_db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif db_url.startswith("postgresql://"):
    final_db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
else:
    final_db_url = db_url

# Création de l'engine asynchrone avec l'URL garantie rétablie
engine = create_async_engine(
    final_db_url, 
    echo=False, 
    future=True,
    pool_pre_ping=True
)

AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()
async_session_factory = AsyncSessionLocal