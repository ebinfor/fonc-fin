import pytest_asyncio
import httpx
import asyncio

# Import lazy du FastAPI app : l'initialisation du schéma SQLite doit
# s'exécuter avant toute importation de modèles legacy qui alourdirait Base.metadata.

"""
FONCIER+ — conftest.py pour tests E2E et d'intégration
Fournit les fixtures essentielles : event_loop, db session, HTTP clients.
"""
import asyncio
import pytest
import pytest_asyncio
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

# ─── Event loop scope session ─────────────────────────────────────
@pytest.fixture(scope="session")
def event_loop():
    """Event loop unique pour toute la session de tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ─── Base URL ─────────────────────────────────────────────────────
API_BASE = "http://localhost:8000/v1"


# ─── Client HTTP scope session ────────────────────────────────────

# Initialisation forcée du schéma pour SQLite en mémoire
try:
    from app.core.database import Base, engine
except ImportError:
    try:
        from backend.app.core.database import Base, engine
    except ImportError:
        Base = None
        engine = None

@pytest_asyncio.fixture(scope="session", autouse=True)
async def init_database_schema():
    # [Vos imports de modules de conftest restent ici...]
    
    if Base is not None and engine is not None:
        async with engine.begin() as conn:
            from sqlalchemy import text
            
            # 1. Nettoyage complet
            print("🧼 [TEST SETUP] Destruction et recréation du schéma public Postgres...")
            await conn.execute(text("DROP SCHEMA public CASCADE;"))
            await conn.execute(text("CREATE SCHEMA public;"))
            await conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
            
            # 2. Réactivation des extensions
            print("🌍 [TEST SETUP] Réactivation propre de PostGIS et UUID...")
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"))
            
            # 🔥 LE PATCH DE SECOURS : On pré-crée la table 'users' demandée par la migration 003
            print("👤 [TEST SETUP] Création de la table 'users' de secours pour valider les FK...")
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS users (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    email VARCHAR(255) UNIQUE,
                    is_active BOOLEAN DEFAULT TRUE
                );
            """))
            
            # 3. Exécution d'Alembic
            def run_alembic_upgrade(sync_conn):
                from alembic.config import Config
                from alembic import command
                import os
                
                ini_path = "alembic.ini" if os.path.exists("alembic.ini") else "backend/alembic.ini"
                cfg = Config(ini_path)
                cfg.attributes['connection'] = sync_conn
                command.upgrade(cfg, "head")
            
            print("🚀 [TEST SETUP] Application des migrations Alembic (001 à 018)...")
            await conn.run_sync(run_alembic_upgrade)
            print("✅ [TEST SETUP] Schéma FONCIER+ reconstruit avec succès !")
            
    yield

# ─── Tokens JWT par rôle ─────────────────────────────────────────
ROLES_TEST = [
    "ADMIN", "MINISTRE_URBANISME",
    "ADMIN_CADASTRE", "DIRECTEUR_CADASTRE", "CHEF_SERVICE_CADASTRE",
    "GEOMETRE", "TOPOGRAPHE", "SECRETARIAT_CADASTRE",
    "DIRECTEUR_URBANISME", "CHEF_URBANISME", "AGENT_URBANISME",
    "ADMIN_COMMUNE", "MAIRE", "AGENT_COMMUNE",
    "CHEF_CCFM", "AGENT_CCFM", "NOTAIRE",
    "BANQ_DIRECTEUR", "BANQ_AGENT", "JUGE_FONCIER",
    "GREFFIER", "HUISSIER", "DIRECTEUR_DOMAINE",
    "AGENT_DOMAINE", "EDITEUR_JO", "RESPONSABLE_BGU",
    "AUDITEUR", "ARCHIVISTE_ANNF", "RESPONSABLE_ANNF"
]

@pytest_asyncio.fixture(scope="session", autouse=True)
async def init_database_schema():
    # [Vos imports de modules restent identiques ici...]
    try:
        import app.models.auth
        import app.models.ccfm
    except ImportError:
        pass

    if Base is not None and engine is not None:
        async with engine.begin() as conn:
            from sqlalchemy import text
            
            # 1. 🧼 NUKE DU SCHÉMA PUBLIC (Méthode radicale et propre)
            # Le CASCADE force Postgres à sauter toutes les contraintes et dépendances
            print("🧼 [TEST SETUP] Destruction et recréation du schéma public Postgres...")
            await conn.execute(text("DROP SCHEMA public CASCADE;"))
            await conn.execute(text("CREATE SCHEMA public;"))
            await conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
            
            # 2. Réactivation propre des extensions nationales requises
            print("🌍 [TEST SETUP] Réactivation propre de PostGIS et UUID...")
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"))
            
            # 3. Exécution d'Alembic
            def run_alembic_upgrade(sync_conn):
                from alembic.config import Config
                from alembic import command
                import os
                
                ini_path = "alembic.ini" if os.path.exists("alembic.ini") else "backend/alembic.ini"
                cfg = Config(ini_path)
                cfg.attributes['connection'] = sync_conn
                command.upgrade(cfg, "head")
            
            print("🚀 [TEST SETUP] Application des migrations Alembic (001 à 018)...")
            await conn.run_sync(run_alembic_upgrade)
            print("✅ [TEST SETUP] Schéma FONCIER+ reconstruit à neuf sans conflits !")
            
    yield