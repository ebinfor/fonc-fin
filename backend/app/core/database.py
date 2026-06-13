import contextlib
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.core.config import get_settings

settings = get_settings()

# Diagnostic (temporaire) : afficher l'hôte et le port extraits de DATABASE_URL
# imprimé avec print() pour visibilité immédiate dans les logs Railway.
try:
    from urllib.parse import urlparse
    db_url = getattr(settings, 'DATABASE_URL', None)
    if db_url:
        p = urlparse(db_url)
        host = p.hostname or '<none>'
        port = p.port or ('5432' if p.scheme and 'postgres' in p.scheme else '<none>')
        print(f"DEBUG DB HOST: {host} PORT: {port}")
except Exception:
    # Ne pas faire échouer l'initialisation si le diagnostic échoue
    pass

# Moteur et usines natifs
engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

# ─── EXPORTS REQUIS PAR TES ENDPOINTS (NE PAS SUPPRIMER) ───
Base = declarative_base()
async_session_factory = AsyncSessionLocal  # Alias pour le moteur de workflow

# Dépendance standard FastAPI pour l'injection (get_db)
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# Gestionnaire de contexte pour le bloc lifespan
@contextlib.asynccontextmanager
async def database_session_scope():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()