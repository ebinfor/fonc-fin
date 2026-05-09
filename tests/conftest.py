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
@pytest_asyncio.fixture(scope="session")
async def api_client():
    """Client httpx async réutilisé sur toute la session."""
    async with httpx.AsyncClient(base_url=API_BASE, timeout=30) as client:
        yield client


# ─── Tokens JWT par rôle ─────────────────────────────────────────
ROLES_TEST = [
    "ADMIN", "MINISTRE_URBANISME",
    "ADMIN_CADASTRE", "DIRECTEUR_CADASTRE", "CHEF_SERVICE_CADASTRE",
    "GEOMETRE", "TOPOGRAPHE", "SECRETARIAT_CADASTRE",
    "DIRECTEUR_URBANISME", "CHEF_URBANISME", "AGENT_URBANISME",
    "ADMIN_COMMUNE", "MAIRE", "AGENT_COMMUNE",
    "CHEF_CCFM", "AGENT_CCFM", "NOTAIRE",
    "BANQ_DIRECTEUR", "BANQ_AGENT",
    "JUGE_FONCIER", "GREFFIER", "HUISSIER",
    "DIRECTEUR_DOMAINE", "AGENT_DOMAINE",
    "EDITEUR_JO", "RESPONSABLE_BGU",
    "AUDITEUR", "ARCHIVISTE_ANNF", "RESPONSABLE_ANNF",
]

@pytest_asyncio.fixture(scope="session")
async def tokens(api_client):
    """Crée un utilisateur par rôle et retourne {role: jwt_token}."""
    result = {}

    # Créer d'abord l'admin
    await api_client.post("/admin/users", json={
        "email": "admin@test.foncier.ne",
        "password": "Admin@Test2026!",
        "role": "ADMIN",
    })
    r = await api_client.post("/auth/login", json={
        "email": "admin@test.foncier.ne",
        "password": "Admin@Test2026!",
    })
    admin_token = r.json().get("access_token", "")
    result["ADMIN"] = admin_token

    # Créer les autres rôles avec le token admin
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    for role in ROLES_TEST:
        if role == "ADMIN":
            continue
        email = f"{role.lower()}@test.foncier.ne"
        await api_client.post("/admin/users", headers=admin_headers, json={
            "email": email, "password": "Test@2026!", "role": role,
        })
        r = await api_client.post("/auth/login", json={
            "email": email, "password": "Test@2026!",
        })
        result[role] = r.json().get("access_token", "")
    return result


def auth(tokens: dict, role: str) -> dict:
    """Helper : retourne les headers d'authentification pour un rôle."""
    return {"Authorization": f"Bearer {tokens.get(role, '')}"}


# ─── Fixture parcelle de test ─────────────────────────────────────
@pytest_asyncio.fixture
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
