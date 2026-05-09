"""
FONCIER+ ── Tests du Moteur de Simulation (6 couches)

Couverture :
  Types         OPERATION_TYPES, DOCS_REQUIS, NIVEAUX_VALIDATION
  Helpers       _domaine_pour_op, _recommandation
  Contexte      SimulationContext construction
  Résultat      SimulationResult.as_dict(), autorisee, propriétés
  Moteur        SimulationEngine.simuler() sans DB (statique)
  Moteur        SimulationEngine.simuler() avec DB mockée
  Couches       S1-S6 logique unitaire via mocks
  Batch         Simulation multiple sans DB
"""
import asyncio
import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

try:
    from app.services.simulation_engine import (
        SimulationEngine, SimulationContext, SimulationResult,
        SimulationStatut, ImpactNiveau, ImpactSimule, EtapeSimulation,
        OPERATION_TYPES, DOCS_REQUIS, NIVEAUX_VALIDATION,
        _domaine_pour_op, _recommandation,
        _s1_preconditions, _s3_droits_fonciers,
    )
    HAS_SIM = True
except ImportError as e:
    HAS_SIM = False
    print(f"[SKIP] {e}")

pytestmark = pytest.mark.skipif(not HAS_SIM, reason="simulation_engine non disponible")


# ════════════════════════════════════════════════════════════════════
# Constantes et helpers
# ════════════════════════════════════════════════════════════════════

class TestConstants:

    def test_operation_types_12(self):
        assert len(OPERATION_TYPES) == 12

    def test_tous_ops_dans_docs_requis(self):
        for op in OPERATION_TYPES:
            assert op in DOCS_REQUIS, f"{op} absent de DOCS_REQUIS"

    def test_tous_ops_dans_niveaux_validation(self):
        for op in OPERATION_TYPES:
            assert op in NIVEAUX_VALIDATION, f"{op} absent de NIVEAUX_VALIDATION"

    def test_expropriation_niveau_4(self):
        assert NIVEAUX_VALIDATION["EXPROPRIATION"] == 4

    def test_mutation_requiert_ccfm(self):
        assert "CCFM" in DOCS_REQUIS["MUTATION"]

    def test_mainlevee_pas_ccfm(self):
        # MAINLEVEE ne nécessite pas de CCFM
        assert "CCFM" not in DOCS_REQUIS["MAINLEVEE"]

    def test_domaine_mutation(self):
        assert _domaine_pour_op("MUTATION") == "MUTATION"

    def test_domaine_mise_en_gele(self):
        assert _domaine_pour_op("MISE_EN_GELE") == "JUSTICE"

    def test_domaine_inconnu(self):
        assert _domaine_pour_op("INCONNU") == "GENERAL"

    def test_recommandation_aucun(self):
        r = _recommandation(0, 0)
        assert "peut être soumise" in r

    def test_recommandation_avec_avert(self):
        r = _recommandation(0, 3)
        assert "avertissement" in r.lower()

    def test_recommandation_bloquant(self):
        r = _recommandation(2, 1)
        assert "bloquée" in r.lower() or "bloquant" in r.lower()


# ════════════════════════════════════════════════════════════════════
# SimulationContext
# ════════════════════════════════════════════════════════════════════

class TestSimulationContext:

    def test_creation_basique(self):
        ctx = SimulationContext(
            operation_type="MUTATION",
            parcelle_ids=["uuid-1"],
            user_id="user-1",
            region="NI-01",
        )
        assert ctx.operation_type == "MUTATION"
        assert ctx.parcelle_ids == ["uuid-1"]
        assert ctx.region == "NI-01"
        assert ctx.contexte_extra == {}

    def test_creation_avec_titulaire(self):
        ctx = SimulationContext(
            operation_type="MUTATION",
            parcelle_ids=["uuid-1"],
            user_id="user-1",
            region="NI-01",
            nouveau_titulaire_id="tit-1",
        )
        assert ctx.nouveau_titulaire_id == "tit-1"

    def test_creation_scission(self):
        ctx = SimulationContext(
            operation_type="SCISSION",
            parcelle_ids=["uuid-1"],
            user_id="user-1",
            region="NI-02",
            nb_lots=4,
        )
        assert ctx.nb_lots == 4


# ════════════════════════════════════════════════════════════════════
# SimulationResult
# ════════════════════════════════════════════════════════════════════

class TestSimulationResult:

    def _make_result(self, statut=SimulationStatut.SUCCES, impacts=None):
        return SimulationResult(
            simulation_id  = "sim-id-1",
            operation_type = "MUTATION",
            statut         = statut,
            parcelles_cibles = ["p1"],
            impacts        = impacts or [],
            nb_bloquants   = sum(1 for i in (impacts or []) if i.niveau==ImpactNiveau.BLOQUANT),
            nb_avertissements = sum(1 for i in (impacts or []) if i.niveau==ImpactNiveau.AVERTISSEMENT),
        )

    def test_succes_autorisee(self):
        r = self._make_result(SimulationStatut.SUCCES)
        assert r.autorisee is True

    def test_avertissement_autorisee(self):
        r = self._make_result(SimulationStatut.AVERTISSEMENT)
        assert r.autorisee is True

    def test_echec_non_autorisee(self):
        r = self._make_result(SimulationStatut.ECHEC)
        assert r.autorisee is False

    def test_as_dict_complet(self):
        imp = ImpactSimule(
            couche="S1", niveau=ImpactNiveau.BLOQUANT,
            entite="parcelle", entite_id="uuid",
            message="Test", detail="Detail", correction="Correction",
        )
        r = self._make_result(SimulationStatut.ECHEC, [imp])
        d = r.as_dict()
        assert "simulation_id"   in d
        assert "statut"          in d
        assert "autorisee"       in d
        assert "impacts"         in d
        assert "etapes"          in d
        assert "recommandation"  in d
        assert "sha256_rapport"  in d
        assert "effets_projetes" in d
        assert d["statut"]       == "ECHEC"
        assert not d["autorisee"]

    def test_as_dict_impacts_structure(self):
        imp = ImpactSimule(
            couche="S2", niveau=ImpactNiveau.AVERTISSEMENT,
            entite="regle", entite_id="",
            message="Avert", detail="", correction="",
        )
        r = self._make_result(impacts=[imp])
        d = r.as_dict()
        assert len(d["impacts"]) == 1
        assert d["impacts"][0]["couche"]  == "S2"
        assert d["impacts"][0]["niveau"]  == "AVERTISSEMENT"
        assert d["impacts"][0]["message"] == "Avert"


# ════════════════════════════════════════════════════════════════════
# SimulationEngine — sans DB (statique)
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_simulation_sans_db_type_valide():
    ctx = SimulationContext(
        operation_type="MUTATION",
        parcelle_ids=["00000000-0000-0000-0000-000000000001"],
        user_id="00000000-0000-0000-0000-000000000099",
        region="NI-01",
    )
    result = await SimulationEngine.simuler(ctx, db=None)
    assert result.statut == SimulationStatut.AVERTISSEMENT
    assert len(result.etapes) > 0
    assert result.simulation_id


@pytest.mark.asyncio
async def test_simulation_type_inconnu():
    ctx = SimulationContext(
        operation_type="TYPE_INEXISTANT",
        parcelle_ids=["uuid-1"],
        user_id="user-1",
        region="NI-01",
    )
    result = await SimulationEngine.simuler(ctx, db=None)
    assert result.statut == SimulationStatut.ECHEC
    assert result.nb_bloquants >= 1
    assert any("inconnu" in i.message.lower() for i in result.impacts)


@pytest.mark.asyncio
async def test_simulation_as_dict_contient_operation_type():
    ctx = SimulationContext(
        operation_type="HYPOTHEQUE",
        parcelle_ids=["uuid-1"],
        user_id="user-1",
        region="NI-02",
    )
    result = await SimulationEngine.simuler(ctx, db=None)
    d = result.as_dict()
    assert d["operation_type"] == "HYPOTHEQUE"


@pytest.mark.asyncio
async def test_simulation_sha256_64_chars():
    ctx = SimulationContext(
        operation_type="SUCCESSION",
        parcelle_ids=["uuid-1"],
        user_id="user-1",
        region="NI-03",
    )
    result = await SimulationEngine.simuler(ctx, db=None)
    assert len(result.sha256_rapport) == 64
    assert all(c in "0123456789abcdef" for c in result.sha256_rapport)


@pytest.mark.asyncio
async def test_simulation_id_unique():
    ctx = SimulationContext("MUTATION", ["uuid-1"], "user-1", "NI-01")
    r1 = await SimulationEngine.simuler(ctx, db=None)
    r2 = await SimulationEngine.simuler(ctx, db=None)
    assert r1.simulation_id != r2.simulation_id


# ════════════════════════════════════════════════════════════════════
# SimulationEngine — avec DB mockée
# ════════════════════════════════════════════════════════════════════

def _make_mock_db(parcelle_row=None, droit_rows=None, ccfm_row=None,
                  agent_row=None, conflit_rows=None):
    """
    Crée un mock AsyncSession pour les tests avec DB.
    Retourne des données cohérentes par défaut.
    """
    db = AsyncMock()

    parcelle_row = parcelle_row or {
        "id": "00000000-0000-0000-0000-000000000001",
        "nicad": "NI-01-01-001-001",
        "statut": "active",
        "is_gele": False,
        "surface_m2": 5000.0,
        "region": "NI-01",
        "commune": "Niamey-1",
        "somme_droits": 100.0,
        "nb_verrous": 0,
        "nb_litiges_actifs": 0,
        "workflows_en_cours": 0,
    }

    droit_rows = droit_rows or [{
        "type_droit": "PROPRIETE",
        "pourcentage": 100.0,
        "titulaire_id": "00000000-0000-0000-0000-000000000099",
        "nom_complet": "Mahamane Koné",
        "statut_version": "active",
    }]

    agent_row = agent_row or {
        "region_principale": "NI-01",
        "regions_autorisees": ["NI-01", "NI-02"],
        "niveau_autorite": 2,
        "peut_valider_hors_region": False,
        "institution_nom": "Direction Régionale NI-01",
    }

    # Séquence de retours simulés pour chaque execute()
    def make_scalar_result(val):
        m = MagicMock()
        m.scalar.return_value = val
        m.mappings.return_value.first.return_value = None
        m.mappings.return_value.all.return_value = []
        return m

    def make_mapping_result(row):
        m = MagicMock()
        mr = MagicMock()
        mr.__iter__ = MagicMock(return_value=iter([row]))
        mapping_obj = MagicMock()
        mapping_obj.first.return_value = row
        mapping_obj.all.return_value = [row] if row else []
        m.mappings.return_value = mapping_obj
        m.scalar.return_value = 0
        return m

    def make_list_result(rows):
        m = MagicMock()
        mapping_obj = MagicMock()
        mapping_obj.all.return_value = rows
        mapping_obj.first.return_value = rows[0] if rows else None
        m.mappings.return_value = mapping_obj
        m.scalar.return_value = len(rows)
        return m

    call_count = [0]

    async def mock_execute(stmt, params=None):
        call_count[0] += 1
        sql_str = str(stmt).lower()

        if "savepoint" in sql_str or "set local" in sql_str or "rollback" in sql_str:
            return MagicMock()
        if "from parcelles p" in sql_str and "nicad" in sql_str:
            return make_mapping_result(parcelle_row)
        if "parcel_right_version" in sql_str:
            return make_list_result(droit_rows)
        if "decision_engine_evaluer" in sql_str:
            return make_mapping_result({
                "autorise": True, "decision": "AUTORISE",
                "regles_evaluees": 5, "blocages": 0,
                "avertissements": 0, "motifs": [], "sha256": "abc",
            })
        if "demandes_ccfm" in sql_str:
            if ccfm_row is not False:
                return make_mapping_result(ccfm_row or {
                    "id": "ccfm-1", "etat": "SIGNE_DIRECTEUR",
                    "date_creation": "2024-01-01",
                    "date_signature_directeur": "2024-01-10",
                })
            return make_mapping_result(None)
        if "rnaf" in sql_str:
            return make_scalar_result(1)
        if "agent_profil" in sql_str:
            return make_mapping_result(agent_row)
        if "conflits_parcellaires" in sql_str:
            return make_list_result(conflit_rows or [])
        if "bgu_parcel_status" in sql_str:
            return make_scalar_result(1)
        if "users" in sql_str and "nom_complet" in sql_str:
            return make_mapping_result({"id": "user-1", "nom_complet": "Agent Test"})
        if "simulation_log" in sql_str:
            return MagicMock()
        # Par défaut
        return make_mapping_result(None)

    db.execute = mock_execute
    return db


@pytest.mark.asyncio
async def test_simulation_mutation_succes():
    """MUTATION sans blocage → SUCCES."""
    ctx = SimulationContext(
        operation_type="MUTATION",
        parcelle_ids=["00000000-0000-0000-0000-000000000001"],
        user_id="00000000-0000-0000-0000-000000000099",
        region="NI-01",
        nouveau_titulaire_id="00000000-0000-0000-0000-000000000002",
    )
    db = _make_mock_db()
    result = await SimulationEngine.simuler(ctx, db=db)
    assert result.autorisee
    assert result.statut in (SimulationStatut.SUCCES, SimulationStatut.AVERTISSEMENT)
    assert result.nb_bloquants == 0


@pytest.mark.asyncio
async def test_simulation_parcelle_gelee():
    """Parcelle gelée → impact BLOQUANT en S1."""
    ctx = SimulationContext(
        operation_type="MUTATION",
        parcelle_ids=["00000000-0000-0000-0000-000000000001"],
        user_id="00000000-0000-0000-0000-000000000099",
        region="NI-01",
    )
    db = _make_mock_db(parcelle_row={
        "id": "00000000-0000-0000-0000-000000000001",
        "nicad": "NI-01-01-001-001",
        "statut": "active",
        "is_gele": True,     # ← gelée
        "surface_m2": 1000.0,
        "region": "NI-01",
        "commune": "Niamey",
        "somme_droits": 100.0,
        "nb_verrous": 0,
        "nb_litiges_actifs": 0,
        "workflows_en_cours": 0,
    })
    result = await SimulationEngine.simuler(ctx, db=db)
    assert result.statut == SimulationStatut.ECHEC
    assert result.nb_bloquants >= 1
    s1_blok = [i for i in result.impacts
               if i.couche == "S1" and i.niveau == ImpactNiveau.BLOQUANT]
    assert s1_blok
    assert "gelée" in s1_blok[0].message.lower() or "gel" in s1_blok[0].message.lower()


@pytest.mark.asyncio
async def test_simulation_sans_ccfm():
    """MUTATION sans CCFM signé → impact BLOQUANT en S4."""
    ctx = SimulationContext(
        operation_type="MUTATION",
        parcelle_ids=["00000000-0000-0000-0000-000000000001"],
        user_id="00000000-0000-0000-0000-000000000099",
        region="NI-01",
    )
    db = _make_mock_db(ccfm_row=False)  # Pas de CCFM
    result = await SimulationEngine.simuler(ctx, db=db)
    s4_blok = [i for i in result.impacts
               if i.couche == "S4" and i.niveau == ImpactNiveau.BLOQUANT]
    assert s4_blok, "Absence de CCFM aurait dû créer un impact S4 BLOQUANT"


@pytest.mark.asyncio
async def test_simulation_agent_hors_region():
    """Agent sans autorisation régionale → impact S5 BLOQUANT."""
    ctx = SimulationContext(
        operation_type="MUTATION",
        parcelle_ids=["00000000-0000-0000-0000-000000000001"],
        user_id="00000000-0000-0000-0000-000000000099",
        region="NI-08",  # Hors zone agent
    )
    db = _make_mock_db(agent_row={
        "region_principale": "NI-01",
        "regions_autorisees": ["NI-01"],
        "niveau_autorite": 2,
        "peut_valider_hors_region": False,
        "institution_nom": "Dir. NI-01",
    })
    result = await SimulationEngine.simuler(ctx, db=db)
    s5_blok = [i for i in result.impacts
               if i.couche == "S5" and i.niveau == ImpactNiveau.BLOQUANT]
    assert s5_blok, "Agent hors région aurait dû créer un impact S5 BLOQUANT"


@pytest.mark.asyncio
async def test_simulation_conflit_geographique():
    """Conflit géo critique → impact S6 BLOQUANT."""
    ctx = SimulationContext(
        operation_type="FUSION",
        parcelle_ids=["00000000-0000-0000-0000-000000000001"],
        user_id="00000000-0000-0000-0000-000000000099",
        region="NI-01",
    )
    db = _make_mock_db(conflit_rows=[{
        "id": "conf-1",
        "type_conflit": "superposition",
        "gravite": "critique",
        "nicad_ref": "NI-01-01-001-001",
        "nicad_conflit": "NI-01-01-001-002",
        "statut": "ouvert",
    }])
    result = await SimulationEngine.simuler(ctx, db=db)
    s6 = [i for i in result.impacts if i.couche == "S6"]
    assert s6


@pytest.mark.asyncio
async def test_simulation_droits_somme_incorrecte():
    """Droits totaux ≠ 100% → impact S3 BLOQUANT."""
    ctx = SimulationContext(
        operation_type="MUTATION",
        parcelle_ids=["00000000-0000-0000-0000-000000000001"],
        user_id="00000000-0000-0000-0000-000000000099",
        region="NI-01",
    )
    db = _make_mock_db(droit_rows=[{
        "type_droit": "PROPRIETE",
        "pourcentage": 60.0,   # ← ne fait pas 100%
        "titulaire_id": "user-1",
        "nom_complet": "Koné",
        "statut_version": "active",
    }])
    result = await SimulationEngine.simuler(ctx, db=db)
    s3_blok = [i for i in result.impacts
               if i.couche == "S3" and i.niveau == ImpactNiveau.BLOQUANT]
    assert s3_blok, "Somme droits ≠ 100% aurait dû créer un impact S3 BLOQUANT"


@pytest.mark.asyncio
async def test_simulation_toutes_etapes_presentes():
    """Toutes les couches S1-S6 doivent être présentes dans les traces."""
    ctx = SimulationContext(
        operation_type="MUTATION",
        parcelle_ids=["00000000-0000-0000-0000-000000000001"],
        user_id="00000000-0000-0000-0000-000000000099",
        region="NI-01",
    )
    db = _make_mock_db()
    result = await SimulationEngine.simuler(ctx, db=db)
    couches_presentes = {e.couche for e in result.etapes}
    for couche in ["S1", "S2", "S3", "S4", "S5", "S6"]:
        assert couche in couches_presentes, f"Couche {couche} absente des traces"


@pytest.mark.asyncio
async def test_simulation_rollback_garantie():
    """
    Vérifie que ROLLBACK TO SAVEPOINT est bien appelé
    même si une couche lève une exception.
    """
    from unittest.mock import AsyncMock as AM, call

    execute_calls = []

    async def mock_exec(stmt, params=None):
        sql = str(stmt).lower()
        execute_calls.append(sql[:60])
        if "from parcelles" in sql and "nicad" in sql:
            raise Exception("DB Error simulée")
        return MagicMock()

    db = AsyncMock()
    db.execute = mock_exec

    ctx = SimulationContext(
        operation_type="MUTATION",
        parcelle_ids=["00000000-0000-0000-0000-000000000001"],
        user_id="user-1", region="NI-01",
    )
    result = await SimulationEngine.simuler(ctx, db=db)
    # ROLLBACK doit être présent dans les appels
    rollback_calls = [c for c in execute_calls if "rollback" in c]
    assert rollback_calls, "ROLLBACK TO SAVEPOINT non appelé malgré une erreur"


@pytest.mark.asyncio
async def test_simulation_scission_surface_insuffisante():
    """Scission avec surface < 200m²/lot → impact S6 AVERTISSEMENT."""
    ctx = SimulationContext(
        operation_type="SCISSION",
        parcelle_ids=["00000000-0000-0000-0000-000000000001"],
        user_id="00000000-0000-0000-0000-000000000099",
        region="NI-01",
        nb_lots=10,  # 5000m² / 10 lots = 500m² mais on va mock à 500m²
    )

    async def mock_exec(stmt, params=None):
        sql = str(stmt).lower()
        if "savepoint" in sql or "set local" in sql or "rollback" in sql:
            return MagicMock()
        if "from parcelles p" in sql and "nicad" in sql:
            m = MagicMock()
            m.mappings.return_value.first.return_value = {
                "id": params.get("pid", ""), "nicad": "TEST",
                "statut": "active", "is_gele": False,
                "surface_m2": 1000.0,  # 1000m² / 10 = 100m² < 200 seuil
                "region": "NI-01", "commune": "Niamey",
                "somme_droits": 100.0, "nb_verrous": 0,
                "nb_litiges_actifs": 0, "workflows_en_cours": 0,
            }
            return m
        m = MagicMock()
        m.scalar.return_value = 0
        m.mappings.return_value.first.return_value = None
        m.mappings.return_value.all.return_value = []
        return m

    db = AsyncMock(); db.execute = mock_exec
    result = await SimulationEngine.simuler(ctx, db=db)
    s6 = [i for i in result.impacts if i.couche == "S6"]
    # Si surface_m2 = 1000 / 10 lots = 100 < 200 → AVERTISSEMENT
    avert_s6 = [i for i in s6 if i.niveau == ImpactNiveau.AVERTISSEMENT]
    assert avert_s6, "Surface insuffisante par lot devrait générer un AVERTISSEMENT S6"


# ════════════════════════════════════════════════════════════════════
# Expropriation — niveau 4 requis
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_simulation_expropriation_niveau_insuffisant():
    """Expropriation requiert niveau 4 — agent niveau 2 → BLOQUANT."""
    ctx = SimulationContext(
        operation_type="EXPROPRIATION",
        parcelle_ids=["00000000-0000-0000-0000-000000000001"],
        user_id="00000000-0000-0000-0000-000000000099",
        region="NI-01",
    )
    db = _make_mock_db(agent_row={
        "region_principale": "NI-01",
        "regions_autorisees": ["NI-01"],
        "niveau_autorite": 2,   # Insuffisant pour EXPROPRIATION (4 requis)
        "peut_valider_hors_region": False,
        "institution_nom": "Dir. Régionale",
    })
    result = await SimulationEngine.simuler(ctx, db=db)
    s5_blok = [i for i in result.impacts
               if i.couche == "S5" and i.niveau == ImpactNiveau.BLOQUANT
               and "niveau" in i.message.lower()]
    assert s5_blok, "Niveau autorité insuffisant pour EXPROPRIATION"


# ════════════════════════════════════════════════════════════════════
# Performances
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_simulation_performance_sans_db():
    """Simulation sans DB doit se terminer en < 500ms."""
    import time
    ctx = SimulationContext("MUTATION", ["uuid-1"], "user-1", "NI-01")
    t = time.monotonic()
    result = await SimulationEngine.simuler(ctx, db=None)
    elapsed = (time.monotonic() - t) * 1000
    assert elapsed < 500, f"Simulation trop lente : {elapsed:.0f}ms"


@pytest.mark.asyncio
async def test_simulation_avec_db_mocked_performance():
    """Simulation avec DB mockée doit se terminer en < 2000ms."""
    import time
    ctx = SimulationContext(
        operation_type="HYPOTHEQUE",
        parcelle_ids=["00000000-0000-0000-0000-000000000001"],
        user_id="00000000-0000-0000-0000-000000000099",
        region="NI-01",
    )
    db = _make_mock_db()
    t = time.monotonic()
    result = await SimulationEngine.simuler(ctx, db=db)
    elapsed = (time.monotonic() - t) * 1000
    assert elapsed < 2000, f"Simulation mockée trop lente : {elapsed:.0f}ms"
    assert result.duree_ms > 0


# ════════════════════════════════════════════════════════════════════
# Intégration — tous types d'opérations
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.parametrize("op_type", list(OPERATION_TYPES.keys()))
async def test_tous_types_operations_sans_db(op_type: str):
    """Chaque type d'opération doit pouvoir démarrer sans exception."""
    ctx = SimulationContext(
        operation_type=op_type,
        parcelle_ids=["00000000-0000-0000-0000-000000000001"],
        user_id="user-1",
        region="NI-01",
    )
    result = await SimulationEngine.simuler(ctx, db=None)
    assert result.simulation_id
    assert result.operation_type == op_type
    assert result.statut in SimulationStatut.__members__.values()
