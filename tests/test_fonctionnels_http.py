"""
FONCIER+ — Tests fonctionnels et HTTP (BUG-TEST-02 + BUG-TEST-03 fix)
Couvre :
  - RNAFWorkflowService (BUG-TEST-02)
  - PortailPublicService (BUG-TEST-02)
  - Tests HTTP sur les endpoints principaux (BUG-TEST-03)
"""
import pytest
import pytest_asyncio
import httpx
from unittest.mock import AsyncMock, MagicMock


# ═══════════════════════════════════════════════════════════════════
# BUG-TEST-02 — Tests fonctionnels RNAFWorkflowService
# ═══════════════════════════════════════════════════════════════════

class TestRNAFWorkflowService:
    """Tests sur la machine à états RNAF."""

    def test_transitions_dict_complet(self):
        """Toutes les transitions nécessaires sont définies."""
        from app.services.workflow_orchestrator import RNAFWorkflowService
        from app.models.workflows import StatutRNAF

        transitions = RNAFWorkflowService.TRANSITIONS
        assert StatutRNAF.BROUILLON in transitions
        assert StatutRNAF.PUBLIE in transitions
        # PUBLIE doit permettre SUSPENDU et ARCHIVE
        assert StatutRNAF.SUSPENDU in transitions[StatutRNAF.PUBLIE]
        assert StatutRNAF.ARCHIVE in transitions[StatutRNAF.PUBLIE]
        # ANNULE et ARCHIVE sont terminaux
        assert transitions[StatutRNAF.ANNULE] == []
        assert transitions[StatutRNAF.ARCHIVE] == []

    @pytest.mark.asyncio
    async def test_avancer_statut_transition_invalide(self):
        """Une transition non autorisée doit lever ValueError."""
        from app.services.workflow_orchestrator import RNAFWorkflowService
        from app.models.workflows import RNAFWorkflow, StatutRNAF

        # Créer un workflow mock au statut BROUILLON
        mock_wf = MagicMock(spec=RNAFWorkflow)
        mock_wf.id = "test-id"
        mock_wf.statut = StatutRNAF.BROUILLON
        mock_wf.publication_jo_id = None

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_wf
        mock_db.execute.return_value = mock_result

        # BROUILLON → PUBLIE n'est pas une transition valide
        with pytest.raises(ValueError, match="non autorisée"):
            await RNAFWorkflowService.avancer_statut(
                db=mock_db,
                rnaf_workflow_id="test-id",
                nouveau_statut=StatutRNAF.PUBLIE,
                acteur_id="actor-id",
            )

    @pytest.mark.asyncio
    async def test_avancer_statut_publie_sans_jo_leve_erreur(self):
        """Passer à PUBLIE sans publication_jo_id doit lever ValueError."""
        from app.services.workflow_orchestrator import RNAFWorkflowService
        from app.models.workflows import RNAFWorkflow, StatutRNAF

        mock_wf = MagicMock(spec=RNAFWorkflow)
        mock_wf.statut = StatutRNAF.SCELLE
        mock_wf.publication_jo_id = None

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_wf
        mock_db.execute.return_value = mock_result

        with pytest.raises(ValueError, match="Journal Officiel"):
            await RNAFWorkflowService.avancer_statut(
                db=mock_db,
                rnaf_workflow_id="test-id",
                nouveau_statut=StatutRNAF.PUBLIE,
                acteur_id="actor-id",
                publication_jo_id=None,  # manquant
            )


class TestPortailPublicService:
    """Tests fonctionnels du portail public."""

    @pytest.mark.asyncio
    async def test_get_statut_parcelle_inexistant(self):
        """Une parcelle inexistante retourne None."""
        from app.services.workflow_orchestrator import PortailPublicService
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.first.return_value = None
        mock_db.execute.return_value = mock_result

        result = await PortailPublicService.get_statut_parcelle(mock_db, "NICAD-INEXISTANT")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_historique_retourne_liste(self):
        """L'historique retourne une liste (vide si aucun événement)."""
        from app.services.workflow_orchestrator import PortailPublicService
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        result = await PortailPublicService.get_historique_statuts(mock_db, "01-01-AR0001-001-A1")
        assert isinstance(result, list)


# ═══════════════════════════════════════════════════════════════════
# BUG-TEST-03 — Tests HTTP sur les endpoints principaux
# Nécessitent un serveur en cours d'exécution sur localhost:8000
# ═══════════════════════════════════════════════════════════════════

API = "http://localhost:8000/v1"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_http_health():
    """GET /health doit retourner 200 sans auth."""
    async with httpx.AsyncClient() as client:
        r = await client.get("http://localhost:8000/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_http_login_invalide():
    """POST /auth/login avec mauvais mot de passe → 401."""
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{API}/auth/login", json={
            "email": "inexistant@foncier.ne", "password": "mauvais"
        })
    assert r.status_code == 401


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_http_portail_public_sans_auth():
    """GET /verify/ccfm/{nicad} doit être accessible sans token."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{API}/verify/ccfm/01-01-AR0001-001-A1")
    # 200 (trouvé) ou 404 (introuvable) — les deux sont OK, pas 401 ni 403
    assert r.status_code in (200, 404)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_http_cadastre_sans_token():
    """GET /cadastre/parcelles/ sans token → 401/403."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{API}/cadastre/parcelles/")
    assert r.status_code in (401, 403)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_http_admin_stats_sans_token():
    """GET /admin/stats sans token → 401."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{API}/admin/stats")
    assert r.status_code in (401, 403)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_http_login_admin_et_stats(admin_token: str):
    """Login admin puis accès aux stats → 200."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{API}/admin/stats",
            headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    data = r.json()
    assert "nb_parcelles" in data
    assert "utilisateurs_actifs" in data


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_http_ccfm_sans_nus_inexistant():
    """GET /ccfm/demandes/NUS-INEXISTANT → 404."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{API}/ccfm/demandes/NUS-INEXISTANT",
            headers={"Authorization": "Bearer fake-token"})
    assert r.status_code in (401, 403, 404)


# Fixture pour token admin (utilisée dans les tests e2e)
@pytest_asyncio.fixture(scope="module")
async def admin_token():
    """Obtient un token admin depuis l'API."""
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{API}/auth/login", json={
            "email": "admin@foncier.gov.ne",
            "password": "Admin@2026!"
        })
    if r.status_code == 200:
        return r.json()["access_token"]
    pytest.skip("Serveur non disponible ou admin non seedé")
