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
    # Importation explicite et ordonnée de tous les modules de l'application
    # pour garantir la résolution des contraintes de clés étrangères (FK)
    try:
        import app.models.auth
        import app.models.utilisateurs
        import app.models.parcellaire
        import app.models.droits_fonciers
        import app.models.workflows
        # Importation des modules spécifiques au Registre National (RNAF) et domaines
        import app.models.rnaf
        import app.models.domaines
        import app.models.urbanisme
    except ImportError as e:
        print(f"⚠️ Note d'importation des sous-modules : {e}")

    if Base is not None and engine is not None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield


# Injection de l'override de sécurité pour court-circuiter AttributeError sur SECRET_KEY
@pytest.fixture(scope="session")
def setup_security_overrides():
    try:
        from app.main import app
        from app.core.security import get_current_user
    except ImportError:
        try:
            from backend.app.main import app
            from backend.app.core.security import get_current_user
        except ImportError:
            return

    async def mock_get_current_user(authorization: str = None):
        # On extrait le rôle directement depuis notre token factice MOCK_TOKEN_
        if authorization and "Bearer MOCK_TOKEN_" in authorization:
            role = authorization.replace("Bearer MOCK_TOKEN_", "").strip()
            class MockUser:
                def __init__(self, r):
                    self.id = 12345  # ID fictif pour passer les affectations de logs/moteur
                    self.role = r
                    self.email = f"{r.lower()}@test.foncier.ne"
                    self.is_active = True
            return MockUser(role)
        return None

    app.dependency_overrides[get_current_user] = mock_get_current_user

@pytest_asyncio.fixture(scope="session")
async def api_client(setup_security_overrides):
    """Client httpx async connecté directement à FastAPI en mémoire."""
    try:
        from app.main import app
    except ImportError:
        from backend.app.main import app

    async with httpx.AsyncClient(app=app, base_url=API_BASE, timeout=30) as client:
        yield client


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

@pytest_asyncio.fixture(scope="session")
async def tokens():
    """Génère des jetons nominatifs directs pour court-circuiter l'authentification."""
    return {role: f"MOCK_TOKEN_{role}" for role in ROLES_TEST}


async def parcelle_test(api_client, tokens):
    """
    Crée une parcelle minimale pour les tests.
    Nécessite un îlot de test pré-existant (à créer dans conftest de setup).
    """
    # Pour les tests, on recherche un îlot existant en base
    r = await api_client.get("/cadastre/parcelles/",
        headers=auth(tokens, "GEOMETRE"))
    parcelles = r.json()
    if parcelles:
        return parcelles[0]  # Réutiliser une parcelle existante

    # Sinon créer (nécessite un îlot_id valide en base de test)
    r = await api_client.post("/cadastre/parcelles/",
        headers=auth(tokens, "GEOMETRE"),
        json={
            "ilot_id": "00000000-0000-0000-0000-000000000001",  # îlot de test seedé
            "surface_m2": 500.0,
            "geom_wkt": "POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))",
            "motif": "Parcelle de test E2E pour la suite de tests automatisés",
        })
    assert r.status_code == 201, f"Création parcelle test échouée: {r.text}"
    return r.json()