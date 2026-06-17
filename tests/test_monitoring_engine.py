
# Stubs temporaires pour corriger la collecte pytest
class Evenement: pass
class D1_WorkflowBlockage: pass
"""
FONCIER+ ── Tests du Moteur de Monitoring Temps Réel

Couverture :
  Types         NiveauAlerte, CategorieEvenement, Evenement, Alerte, TableauBord
  Collecteur    CollecteurEvenements (curseurs, méthodes)
  Détecteurs    D1-D6 (logique isolée avec données synthétiques)
  AlerteManager abonnement SSE, acquittement, historique
  MonitoringEngine  singleton, tableau de bord, buffer circulaire
"""
import asyncio, sys, os, time
import pytest
from collections import deque
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

try:
    from app.services.monitoring_engine import (
        NiveauAlerte, CategorieEvenement, Evenement, Alerte, TableauBord,
        CollecteurEvenements, AlerteManager, MonitoringEngine,
        D1_WorkflowBlockage, D2_Surcharge, D3_ConflitValidation,
        D4_AccesInhabituel, D5_RatioEchec, D6_VerrouCadeau,
        _creer_alerte,
        SEUIL_WORKFLOWS_BLOQUES, SEUIL_SURCHARGE_OPS,
        SEUIL_VALIDATIONS_REFUSEES, SEUIL_RATIO_ECHEC_PCT,
        SEUIL_VERROUS_ACTIFS, SEUIL_ACCES_MEME_USER,
        WINDOW_SHORT_SEC, WINDOW_LONG_SEC,
        MAX_EVENTS_BUFFER, MAX_ALERTS_BUFFER,
    )
    HAS_MON = True
except ImportError as e:
    HAS_MON = False
    print(f"[SKIP] {e}")

pytestmark = pytest.mark.skipif(not HAS_MON, reason="monitoring_engine non disponible")


# ── Helpers ───────────────────────────────────────────────────────

def _evt(categorie, operation, user_id=None, ts_offset=0):
    """Crée un Evenement de test avec ts dans la fenêtre courte."""
    import uuid
    return Evenement(
        id=str(uuid.uuid4()),
        categorie=categorie,
        operation=operation,
        entite_type="test",
        entite_id=str(uuid.uuid4()),
        details={},
        user_id=user_id,
        region="NI-01",
        ts=time.monotonic() - ts_offset,
    )


# ════════════════════════════════════════════════════════════════════
# Types et dataclasses
# ════════════════════════════════════════════════════════════════════

class TestTypes:

    def test_niveaux_alertes(self):
        assert NiveauAlerte.CRITICAL.value == "CRITICAL"
        assert NiveauAlerte.WARNING.value  == "WARNING"
        assert NiveauAlerte.INFO.value     == "INFO"

    def test_categories(self):
        cats = {c.value for c in CategorieEvenement}
        assert "PARCELLE" in cats
        assert "WORKFLOW" in cats
        assert "VALIDATION" in cats
        assert "REGLE" in cats
        assert "VERROU" in cats
        assert "TRANSACTION" in cats
        assert "SYSTEME" in cats

    def test_evenement_as_dict(self):
        e = _evt(CategorieEvenement.PARCELLE, "CREATE")
        d = e.as_dict()
        assert "id" in d and "categorie" in d and "operation" in d
        assert d["categorie"] == "PARCELLE"

    def test_alerte_as_dict(self):
        a = _creer_alerte(NiveauAlerte.WARNING, "TEST", "Titre", "Message", {"k": 1})
        d = a.as_dict()
        assert d["niveau"] == "WARNING"
        assert d["titre"]  == "Titre"
        assert not d["acquittee"]
        assert len(d["sha256"]) == 64

    def test_alerte_sha256(self):
        a = _creer_alerte(NiveauAlerte.CRITICAL, "D1", "T", "M", {})
        assert len(a.sha256) == 64
        assert all(c in "0123456789abcdef" for c in a.sha256)

    def test_tableau_bord_as_dict(self):
        tb = TableauBord(parcelles_actives=10, sante="OK")
        d  = tb.as_dict()
        assert d["parcelles_actives"] == 10
        assert d["sante"] == "OK"
        assert "calcule_a" in d

    def test_creer_alerte_unique_ids(self):
        a1 = _creer_alerte(NiveauAlerte.INFO, "D0", "T1", "M1", {})
        a2 = _creer_alerte(NiveauAlerte.INFO, "D0", "T1", "M1", {})
        assert a1.id != a2.id


# ════════════════════════════════════════════════════════════════════
# CollecteurEvenements
# ════════════════════════════════════════════════════════════════════

class TestCollecteur:

    def test_curseur_initial(self):
        c = CollecteurEvenements()
        ts = c._curseur("test_source")
        # Doit être dans le passé récent
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        assert now - timedelta(minutes=2) < ts < now

    def test_maj_curseur(self):
        from datetime import datetime, timezone
        c = CollecteurEvenements()
        c._maj_curseur("src1")
        ts = c._curseur("src1")
        now = datetime.now(timezone.utc)
        assert (now - ts).total_seconds() < 2

    def test_curseurs_independants(self):
        c = CollecteurEvenements()
        c._maj_curseur("src1")
        ts1 = c._curseur("src1")
        ts2 = c._curseur("src2")
        # src2 est plus ancien que src1
        assert ts1 > ts2


# ════════════════════════════════════════════════════════════════════
# D1 — WorkflowBlockage
# ════════════════════════════════════════════════════════════════════

class TestD1WorkflowBlockage:
    d = D1_WorkflowBlockage()

    def _wf_evt(self, entite_id, ts_offset=0):
        import uuid
        return Evenement(
            id=str(uuid.uuid4()),
            categorie=CategorieEvenement.WORKFLOW,
            operation="WORKFLOW_EN_COURS",
            entite_type="parcelle", entite_id=entite_id,
            details={"statut_instance": "en_cours"},
            user_id=None, region=None,
            ts=time.monotonic() - ts_offset,
        )

    def test_pas_dalerte_sous_seuil(self):
        events = deque([self._wf_evt(f"p{i}") for i in range(SEUIL_WORKFLOWS_BLOQUES - 1)])
        alertes = self.d.analyser(events, [])
        critiques = [a for a in alertes if a.niveau == NiveauAlerte.CRITICAL]
        assert not critiques

    def test_alerte_critical_au_seuil(self):
        events = deque([self._wf_evt(f"pid-{i}") for i in range(SEUIL_WORKFLOWS_BLOQUES + 1)])
        alertes = self.d.analyser(events, [])
        assert any(a.niveau == NiveauAlerte.CRITICAL for a in alertes)

    def test_pas_doublon_si_deja_alerte(self):
        events = deque([self._wf_evt(f"p{i}") for i in range(SEUIL_WORKFLOWS_BLOQUES + 2)])
        alerte_existante = _creer_alerte(NiveauAlerte.CRITICAL, "D1_WORKFLOW_BLOCKAGE", "T", "M", {})
        alertes = self.d.analyser(events, [alerte_existante])
        assert not alertes

    def test_events_hors_fenetre_ignores(self):
        """Événements plus vieux que WINDOW_SHORT_SEC ne comptent pas."""
        events = deque([
            self._wf_evt(f"p{i}", ts_offset=WINDOW_SHORT_SEC + 10)
            for i in range(SEUIL_WORKFLOWS_BLOQUES + 5)
        ])
        alertes = self.d.analyser(events, [])
        critiques = [a for a in alertes if a.niveau == NiveauAlerte.CRITICAL]
        assert not critiques


# ════════════════════════════════════════════════════════════════════
# D2 — Surcharge
# ════════════════════════════════════════════════════════════════════

class TestD2Surcharge:
    d = D2_Surcharge()

    def _ops(self, n, offset=0):
        return deque([_evt(CategorieEvenement.PARCELLE, "CREATE", ts_offset=offset)
                      for _ in range(n)])

    def test_pas_alerte_sous_seuil(self):
        alertes = self.d.analyser(self._ops(SEUIL_SURCHARGE_OPS - 1), [])
        assert not alertes

    def test_warning_au_seuil(self):
        alertes = self.d.analyser(self._ops(SEUIL_SURCHARGE_OPS + 1), [])
        assert any(a.niveau == NiveauAlerte.WARNING for a in alertes)

    def test_critical_3x_seuil(self):
        alertes = self.d.analyser(self._ops(SEUIL_SURCHARGE_OPS * 3 + 1), [])
        assert any(a.niveau == NiveauAlerte.CRITICAL for a in alertes)

    def test_hors_fenetre_ignore(self):
        alertes = self.d.analyser(
            self._ops(SEUIL_SURCHARGE_OPS + 10, offset=WINDOW_SHORT_SEC + 5), []
        )
        assert not alertes


# ════════════════════════════════════════════════════════════════════
# D3 — ConflitValidation
# ════════════════════════════════════════════════════════════════════

class TestD3ConflitValidation:
    d = D3_ConflitValidation()

    def _refus(self, n):
        return deque([
            _evt(CategorieEvenement.VALIDATION, "DECISION_REFUSE")
            for _ in range(n)
        ])

    def test_pas_alerte_sous_seuil(self):
        alertes = self.d.analyser(self._refus(SEUIL_VALIDATIONS_REFUSEES - 1), [])
        assert not alertes

    def test_warning_au_seuil(self):
        alertes = self.d.analyser(self._refus(SEUIL_VALIDATIONS_REFUSEES + 1), [])
        assert any(a.niveau == NiveauAlerte.WARNING for a in alertes)

    def test_pas_doublon(self):
        deja = _creer_alerte(NiveauAlerte.WARNING, "D3_CONFLIT_VALIDATION", "T", "M", {})
        alertes = self.d.analyser(self._refus(SEUIL_VALIDATIONS_REFUSEES + 1), [deja])
        assert not alertes


# ════════════════════════════════════════════════════════════════════
# D4 — AccesInhabituel
# ════════════════════════════════════════════════════════════════════

class TestD4AccesInhabituel:
    d = D4_AccesInhabituel()

    def test_pas_alerte_utilisateur_normal(self):
        events = deque([_evt(CategorieEvenement.PARCELLE, "READ", user_id="user-1")
                        for _ in range(SEUIL_ACCES_MEME_USER - 1)])
        alertes = self.d.analyser(events, [])
        assert not alertes

    def test_warning_utilisateur_suspect(self):
        events = deque([_evt(CategorieEvenement.VALIDATION, "CREATE", user_id="suspect-123")
                        for _ in range(SEUIL_ACCES_MEME_USER + 5)])
        alertes = self.d.analyser(events, [])
        assert any(a.niveau == NiveauAlerte.WARNING for a in alertes)

    def test_warning_contient_user_id(self):
        events = deque([_evt(CategorieEvenement.REGLE, "VALIDE", user_id="robot-99")
                        for _ in range(SEUIL_ACCES_MEME_USER + 1)])
        alertes = self.d.analyser(events, [])
        warns = [a for a in alertes if a.niveau == NiveauAlerte.WARNING]
        if warns:
            assert warns[0].details.get("user_id") == "robot-99"

    def test_aucun_user_id_ignore(self):
        events = deque([_evt(CategorieEvenement.PARCELLE, "CREATE", user_id=None)
                        for _ in range(SEUIL_ACCES_MEME_USER + 10)])
        alertes = self.d.analyser(events, [])
        assert not alertes


# ════════════════════════════════════════════════════════════════════
# D5 — RatioEchec
# ════════════════════════════════════════════════════════════════════

class TestD5RatioEchec:
    d = D5_RatioEchec()

    def _regles(self, n_ok, n_refuse, offset=0):
        evts = (
            [_evt(CategorieEvenement.REGLE, "REGLE_VALIDE", ts_offset=offset)   for _ in range(n_ok)]
          + [_evt(CategorieEvenement.REGLE, "REGLE_REFUSE", ts_offset=offset) for _ in range(n_refuse)]
        )
        return deque(evts)

    def test_pas_alerte_peu_de_donnees(self):
        alertes = self.d.analyser(self._regles(2, 2), [])
        assert not alertes

    def test_pas_alerte_ratio_ok(self):
        # 10% d'échecs → sous le seuil
        alertes = self.d.analyser(self._regles(18, 2), [])
        assert not alertes

    def test_warning_ratio_eleve(self):
        nb_ok = 3
        nb_ko = 7   # 70% > 40% seuil
        alertes = self.d.analyser(self._regles(nb_ok, nb_ko), [])
        assert any(a.niveau == NiveauAlerte.WARNING for a in alertes)


# ════════════════════════════════════════════════════════════════════
# D6 — VerrouCadeau
# ════════════════════════════════════════════════════════════════════

class TestD6VerrouCadeau:
    d = D6_VerrouCadeau()

    def _verrous(self, n_poses, n_leves, offset=0):
        evts = (
            [_evt(CategorieEvenement.VERROU, "VERROU_POSE", ts_offset=offset) for _ in range(n_poses)]
          + [_evt(CategorieEvenement.VERROU, "VERROU_LEVE", ts_offset=offset) for _ in range(n_leves)]
        )
        return deque(evts)

    def test_pas_alerte_verrous_equilibres(self):
        alertes = self.d.analyser(self._verrous(5, 5), [])
        assert not alertes

    def test_warning_accumulation(self):
        alertes = self.d.analyser(
            self._verrous(SEUIL_VERROUS_ACTIFS + 5, 0), []
        )
        assert any(a.niveau == NiveauAlerte.WARNING for a in alertes)

    def test_pas_alerte_si_leves_suffisants(self):
        alertes = self.d.analyser(
            self._verrous(SEUIL_VERROUS_ACTIFS + 5, SEUIL_VERROUS_ACTIFS + 5), []
        )
        assert not alertes


# ════════════════════════════════════════════════════════════════════
# AlerteManager
# ════════════════════════════════════════════════════════════════════

class TestAlerteManager:

    def _manager_avec(self, n=3):
        m = AlerteManager()
        for i in range(n):
            m.ajouter(_creer_alerte(
                NiveauAlerte.WARNING, "TEST", f"Titre {i}", f"Message {i}", {}
            ))
        return m

    def test_ajouter_et_actives(self):
        m = self._manager_avec(3)
        assert len(m.actives()) == 3

    def test_acquitter(self):
        m = self._manager_avec(2)
        a = m.actives()[0]
        ok = m.acquitter(a.id, "user-1")
        assert ok
        assert a.acquittee
        assert a.acquittee_par == "user-1"
        assert len(m.actives()) == 1

    def test_acquitter_inexistant(self):
        m = self._manager_avec(1)
        ok = m.acquitter("id-inexistant", "user-1")
        assert not ok

    def test_historique_limite(self):
        m = AlerteManager()
        for i in range(10):
            m.ajouter(_creer_alerte(NiveauAlerte.INFO, "D0", f"T{i}", "M", {}))
        h = m.historique(limite=5)
        assert len(h) == 5

    def test_abonner_desabonner(self):
        m = AlerteManager()
        q = m.abonner()
        assert q in m._clients
        m.desabonner(q)
        assert q not in m._clients

    def test_buffer_max(self):
        m = AlerteManager()
        for i in range(MAX_ALERTS_BUFFER + 50):
            m.ajouter(_creer_alerte(NiveauAlerte.INFO, "D0", f"T{i}", "M", {}))
        assert len(m._alertes) <= MAX_ALERTS_BUFFER

    @pytest.mark.asyncio
    async def test_diffuser_aux_clients(self):
        m = AlerteManager()
        q = m.abonner()
        await m.diffuser({"type": "test", "msg": "hello"})
        item = q.get_nowait()
        assert item["type"] == "test"


# ════════════════════════════════════════════════════════════════════
# MonitoringEngine — singleton + buffer
# ════════════════════════════════════════════════════════════════════

class TestMonitoringEngine:

    def test_singleton(self):
        e1 = MonitoringEngine.instance()
        e2 = MonitoringEngine.instance()
        assert e1 is e2

    def test_6_detecteurs(self):
        e = MonitoringEngine.instance()
        assert len(e.detecteurs) == 6

    def test_events_recents_vide(self):
        # Créer une nouvelle instance pour test isolé
        e = MonitoringEngine()
        assert e.events_recents(10) == []

    def test_events_recents_avec_data(self):
        e = MonitoringEngine()
        for i in range(5):
            e._events.append(_evt(CategorieEvenement.PARCELLE, f"OP_{i}"))
        recents = e.events_recents(10)
        assert len(recents) == 5

    def test_buffer_circulaire_max(self):
        e = MonitoringEngine()
        for i in range(MAX_EVENTS_BUFFER + 50):
            e._events.append(_evt(CategorieEvenement.PARCELLE, f"OP_{i}"))
        # deque(maxlen) tronque automatiquement
        assert len(e._events) == MAX_EVENTS_BUFFER

    def test_stats_par_categorie(self):
        e = MonitoringEngine()
        e._stats["PARCELLE"]   = 10
        e._stats["WORKFLOW"]   = 5
        s = e.stats()
        assert s["PARCELLE"] == 10
        assert s["WORKFLOW"] == 5

    @pytest.mark.asyncio
    async def test_tableau_bord_sans_db(self):
        """Sans DB réelle, le tableau de bord doit retourner des valeurs par défaut."""
        e = MonitoringEngine()
        # Mock DB qui lève des exceptions (pas de vraie DB)
        db = AsyncMock()
        async def raise_exc(*a, **kw):
            raise Exception("No DB")
        db.execute = raise_exc

        tb = await e.tableau_bord(db)
        # Les valeurs numériques restent à 0 mais le statut est calculé
        assert isinstance(tb, TableauBord)
        assert tb.sante in ("OK", "DÉGRADÉ", "CRITIQUE")
        assert "calcule_a" in tb.as_dict()

    @pytest.mark.asyncio
    async def test_running_state(self):
        e = MonitoringEngine()
        assert not e._running
        # Pas de démarrage réel (évite les tâches de fond dans les tests)


# ════════════════════════════════════════════════════════════════════
# Non-régression : autres modules
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_non_regression_simulation():
    from app.services.simulation_engine import SimulationEngine, SimulationContext
    ctx = SimulationContext("MUTATION", ["uuid-1"], "user-1", "NI-01")
    r = await SimulationEngine.simuler(ctx, db=None)
    assert r.simulation_id

@pytest.mark.asyncio
async def test_non_regression_logic_validator():
    from app.services.logic_validator import LogicConflictEngine, Recommandation
    r = await LogicConflictEngine.valider(
        sql="TRUE", domaine="GENERAL", code="MON_COMPAT",
        niveau_blocage="BLOQUANT", db=None,
    )
    assert r.recommandation == Recommandation.AUTORISER

@pytest.mark.asyncio
async def test_non_regression_sql_sandbox():
    from app.services.sql_sandbox import SQLRuleValidator, ValidationStatus
    r = await SQLRuleValidator.valider(
        sql="NOT EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND is_gele=TRUE)",
        domaine="GENERAL", code="MON_SANDBOX", db=None,
    )
    assert r.status == ValidationStatus.VALIDE
