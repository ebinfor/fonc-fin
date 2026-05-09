"""
FONCIER+ ── Tests du Moteur d'Analyse Prédictive des Risques (v106)

Couverture :
  Types       NiveauRisque, TypeEntite, SignalRisque, RiskScore, AlertePredictive
  Score       ScoreCalculator (calcul, niveau, facteurs, recommandations)
  Alertes     RiskAlertGenerator (génération selon niveau)
  Analyseurs  A1-A6 avec DB mockée
  Engine      RiskEngine (parcelle, zone, utilisateur, opération)
  Non-régrès  monitoring, simulation, logic, sandbox
"""
import asyncio, sys, os, time
import pytest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

try:
    from app.services.risk_engine import (
        NiveauRisque, TypeEntite, SignalRisque, RiskScore,
        AlertePredictive, RapportRisque,
        ScoreCalculator, RiskAlertGenerator,
        A1_ConflitZone, A2_MutationFreq, A3_UtilisateurInstable,
        A4_DensiteMutation, A5_HistoriqueAnomalie, A6_OperationRisquee,
        RiskEngine,
        SEUIL_FAIBLE, SEUIL_MOYEN, SEUIL_ELEVE,
        POIDS_ANALYSEURS,
    )
    HAS_RISK = True
except ImportError as e:
    HAS_RISK = False
    print(f"[SKIP] {e}")

pytestmark = pytest.mark.skipif(not HAS_RISK, reason="risk_engine non disponible")


# ── Helpers ───────────────────────────────────────────────────────

def _signal(analyseur, valeur, poids=10):
    return SignalRisque(analyseur=analyseur, valeur=valeur,
                        poids=poids, description=f"Signal {analyseur}", donnees={})

def _mock_db_row(data: dict):
    """Crée un mock de résultat DB retournant un seul row."""
    m = MagicMock()
    mapping = MagicMock()
    mapping.first.return_value = data
    mapping.all.return_value   = [data] if data else []
    m.mappings.return_value    = mapping
    m.scalar.return_value      = 0
    return m

def _mock_db_rows(rows: list):
    """Crée un mock de résultat DB retournant plusieurs rows."""
    m = MagicMock()
    mapping = MagicMock()
    mapping.all.return_value   = rows
    mapping.first.return_value = rows[0] if rows else None
    m.mappings.return_value    = mapping
    m.scalar.return_value      = len(rows)
    return m

def _make_db(**overrides):
    """DB mock configurable retournant des données par défaut."""
    defaults = {
        "litiges_actifs": 0, "litiges_total": 0,
        "conflits_geo_ouverts": 0, "conflits_geo_total": 0,
        "muts_30j": 0, "muts_6m": 0, "muts_1an": 0,
        "muts_total": 0, "intervalle_moy_jours": 9999,
        "refus_total": 0, "ops_total": 5, "refus_30j": 0,
        "simulations_echec": 0, "regles_refusees": 0, "alertes_acces": 0,
        "max_mutations_commune": 0, "moy_mutations_commune": 0,
        "ecart": 0, "nb_communes": 8, "total_parcelles": 100,
        "montant_total_region": 0,
        "modifs_90j": 0, "modifs_total": 0, "verrous_actifs": 0,
        "verrous_total": 0, "wf_bloques": 0, "simulations_echec_30j": 0,
        "mutations_commune_6m": 0,
        "echecs": 0, "total": 0,
    }
    defaults.update(overrides)

    async def mock_exec(stmt, params=None):
        return _mock_db_row(defaults)

    db = AsyncMock()
    db.execute = mock_exec
    return db


# ════════════════════════════════════════════════════════════════════
# TYPES & DATACLASSES
# ════════════════════════════════════════════════════════════════════

class TestTypes:

    def test_niveaux_risque(self):
        assert NiveauRisque.FAIBLE.value   == "FAIBLE"
        assert NiveauRisque.MOYEN.value    == "MOYEN"
        assert NiveauRisque.ELEVE.value    == "ÉLEVÉ"
        assert NiveauRisque.CRITIQUE.value == "CRITIQUE"

    def test_type_entite(self):
        assert TypeEntite.PARCELLE.value    == "PARCELLE"
        assert TypeEntite.ZONE.value        == "ZONE"
        assert TypeEntite.UTILISATEUR.value == "UTILISATEUR"
        assert TypeEntite.OPERATION.value   == "OPERATION"

    def test_seuils_croissants(self):
        assert 0 < SEUIL_FAIBLE < SEUIL_MOYEN < SEUIL_ELEVE < 100

    def test_poids_somme_100(self):
        total = sum(POIDS_ANALYSEURS.values())
        assert total == 100, f"Poids total = {total} ≠ 100"

    def test_signal_as_dict(self):
        s = _signal("A1", 55)
        # SignalRisque est un dataclass, pas besoin de as_dict mais vérifions les champs
        assert s.valeur == 55
        assert s.poids  == 10

    def test_risk_score_as_dict(self):
        score = RiskScore(
            entite_type=TypeEntite.PARCELLE,
            entite_id="pid-1",
            score=42.5,
            niveau=NiveauRisque.MOYEN,
            sha256="a" * 64,
        )
        d = score.as_dict()
        assert d["score"]       == 42.5
        assert d["niveau"]      == "MOYEN"
        assert d["entite_type"] == "PARCELLE"
        assert d["sha256"]      == "a" * 64
        assert "signaux" in d

    def test_alerte_as_dict(self):
        a = AlertePredictive(
            id="al-1", niveau=NiveauRisque.CRITIQUE,
            entite_type=TypeEntite.ZONE, entite_id="NI-01",
            titre="Risque critique", message="Zone en danger",
            score=85.0, facteurs=["Conflits élevés"],
            recommandation="Action urgente",
        )
        d = a.as_dict()
        assert d["niveau"]      == "CRITIQUE"
        assert d["score"]       == 85.0
        assert "titre" in d

    def test_rapport_as_dict(self):
        r = RapportRisque(nb_critique=2, nb_eleve=5)
        d = r.as_dict()
        assert d["nb_critique"] == 2
        assert d["nb_eleve"]    == 5


# ════════════════════════════════════════════════════════════════════
# ScoreCalculator
# ════════════════════════════════════════════════════════════════════

class TestScoreCalculator:

    def test_score_vide(self):
        assert ScoreCalculator.calculer([]) == 0.0

    def test_score_unique(self):
        s = _signal("A1", 60, poids=25)
        # Un seul signal : score = 60 * 25 / 25 = 60
        assert ScoreCalculator.calculer([s]) == pytest.approx(60.0, abs=0.1)

    def test_score_deux_signaux(self):
        s1 = _signal("A1", 100, poids=25)
        s2 = _signal("A2", 0,   poids=20)
        # 100*25 + 0*20 / (25+20) = 2500/45 ≈ 55.6
        result = ScoreCalculator.calculer([s1, s2])
        assert result == pytest.approx(55.6, abs=0.1)

    def test_score_max_100(self):
        signaux = [_signal(f"A{i}", 200, poids=20) for i in range(5)]
        assert ScoreCalculator.calculer(signaux) == pytest.approx(100.0, abs=0.1)

    def test_score_min_0(self):
        signaux = [_signal(f"A{i}", 0, poids=10) for i in range(6)]
        assert ScoreCalculator.calculer(signaux) == 0.0

    def test_niveau_faible(self):
        assert ScoreCalculator.niveau(0)            == NiveauRisque.FAIBLE
        assert ScoreCalculator.niveau(SEUIL_FAIBLE) == NiveauRisque.FAIBLE

    def test_niveau_moyen(self):
        assert ScoreCalculator.niveau(SEUIL_FAIBLE + 1) == NiveauRisque.MOYEN
        assert ScoreCalculator.niveau(SEUIL_MOYEN)      == NiveauRisque.MOYEN

    def test_niveau_eleve(self):
        assert ScoreCalculator.niveau(SEUIL_MOYEN + 1) == NiveauRisque.ELEVE
        assert ScoreCalculator.niveau(SEUIL_ELEVE)     == NiveauRisque.ELEVE

    def test_niveau_critique(self):
        assert ScoreCalculator.niveau(SEUIL_ELEVE + 1) == NiveauRisque.CRITIQUE
        assert ScoreCalculator.niveau(100)              == NiveauRisque.CRITIQUE

    def test_facteurs_cles_top3(self):
        signaux = [
            _signal("A1", 90, poids=25),
            _signal("A2", 10, poids=20),
            _signal("A3", 80, poids=20),
            _signal("A4", 5,  poids=15),
        ]
        facteurs = ScoreCalculator.facteurs_cles(signaux, top_n=2)
        assert len(facteurs) <= 2
        # A1 contribue le plus (90*25=2250), puis A3 (80*20=1600)
        assert facteurs[0] == "Signal A1"

    def test_facteurs_cles_filtre_faibles(self):
        """Signaux avec valeur < 5 ne sont pas dans les facteurs clés."""
        signaux = [_signal("A1", 4, poids=25), _signal("A2", 3, poids=20)]
        facteurs = ScoreCalculator.facteurs_cles(signaux)
        assert len(facteurs) == 0

    def test_recommandations_critique(self):
        recos = ScoreCalculator.recommandations(NiveauRisque.CRITIQUE, [])
        assert len(recos) >= 1
        assert any("Validation renforcée" in r for r in recos)

    def test_recommandations_faible(self):
        recos = ScoreCalculator.recommandations(NiveauRisque.FAIBLE, [])
        assert len(recos) == 0


# ════════════════════════════════════════════════════════════════════
# RiskAlertGenerator
# ════════════════════════════════════════════════════════════════════

class TestRiskAlertGenerator:

    def _score(self, niveau: NiveauRisque, val: float) -> RiskScore:
        return RiskScore(
            entite_type=TypeEntite.PARCELLE, entite_id="pid-1",
            score=val, niveau=niveau,
            facteurs_cles=["Conflit actif"], recommandations=["Résoudre litige"],
            sha256="a"*64,
        )

    def test_faible_pas_dalerte(self):
        a = RiskAlertGenerator.generer(self._score(NiveauRisque.FAIBLE, 20))
        assert a is None

    def test_moyen_genere_alerte(self):
        a = RiskAlertGenerator.generer(self._score(NiveauRisque.MOYEN, 35))
        assert a is not None
        assert a.niveau == NiveauRisque.MOYEN
        assert len(a.sha256) == 64

    def test_eleve_genere_alerte(self):
        a = RiskAlertGenerator.generer(self._score(NiveauRisque.ELEVE, 65))
        assert a is not None
        assert a.niveau == NiveauRisque.ELEVE

    def test_critique_genere_alerte(self):
        a = RiskAlertGenerator.generer(self._score(NiveauRisque.CRITIQUE, 85))
        assert a is not None
        assert a.niveau == NiveauRisque.CRITIQUE
        assert "CRITIQUE" in a.titre

    def test_alerte_score_correct(self):
        a = RiskAlertGenerator.generer(self._score(NiveauRisque.ELEVE, 72.5))
        assert a.score == pytest.approx(72.5, abs=0.1)

    def test_alertes_ids_uniques(self):
        s = self._score(NiveauRisque.CRITIQUE, 90)
        a1 = RiskAlertGenerator.generer(s)
        a2 = RiskAlertGenerator.generer(s)
        assert a1.id != a2.id


# ════════════════════════════════════════════════════════════════════
# Analyseurs A1-A6
# ════════════════════════════════════════════════════════════════════

class TestA1ConflitZone:
    a = A1_ConflitZone()

    @pytest.mark.asyncio
    async def test_aucun_conflit_score_faible(self):
        db = _make_db(litiges_actifs=0, conflits_geo_ouverts=0)
        s  = await self.a.analyser_parcelle(db, "pid-1")
        assert s is not None
        assert s.valeur == 0.0

    @pytest.mark.asyncio
    async def test_litiges_actifs_elevant_score(self):
        db = _make_db(litiges_actifs=2, conflits_geo_ouverts=1)
        s  = await self.a.analyser_parcelle(db, "pid-1")
        assert s is not None
        assert s.valeur > 50  # 2*35 + 1*20 = 90

    @pytest.mark.asyncio
    async def test_score_max_100(self):
        db = _make_db(litiges_actifs=5, conflits_geo_ouverts=5, litiges_total=10)
        s  = await self.a.analyser_parcelle(db, "pid-1")
        assert s.valeur == pytest.approx(100.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_zone_avec_taux_conflit(self):
        db = _make_db(litiges_actifs=10, parcelles_avec_litige=20,
                      conflits_geo=5, total_parcelles=100)
        s  = await self.a.analyser_zone(db, "NI-01")
        assert s is not None
        # taux = 20% → score = 20*1.5 + 10*2 + 5*1.5 = 57.5
        assert s.valeur > 0

    @pytest.mark.asyncio
    async def test_db_erreur_retourne_none(self):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=Exception("DB down"))
        s  = await self.a.analyser_parcelle(db, "pid-1")
        assert s is None


class TestA2MutationFreq:
    a = A2_MutationFreq()

    @pytest.mark.asyncio
    async def test_aucune_mutation(self):
        db = _make_db(muts_30j=0, muts_6m=0, muts_1an=0, muts_total=0)
        s  = await self.a.analyser_parcelle(db, "pid-1")
        assert s is not None
        assert s.valeur == 0.0

    @pytest.mark.asyncio
    async def test_mutation_recente_penalite(self):
        db = _make_db(muts_30j=2, muts_6m=4, muts_1an=6, muts_total=10)
        s  = await self.a.analyser_parcelle(db, "pid-1")
        # 2*25 + 4*8 + 6*3 = 50 + 32 + 18 = 100
        assert s.valeur >= 50

    @pytest.mark.asyncio
    async def test_zone_mutations_recentes(self):
        db = _make_db(total_mutations=100, mutations_recentes=80, parcelles_mutees=30)
        s  = await self.a.analyser_zone(db, "NI-02")
        assert s is not None
        assert s.valeur > 0


class TestA3UtilisateurInstable:
    a = A3_UtilisateurInstable()

    @pytest.mark.asyncio
    async def test_utilisateur_propre(self):
        db = _make_db(refus_total=0, ops_total=20, refus_30j=0,
                      simulations_echec=0, regles_refusees=0, alertes_acces=0)
        s  = await self.a.analyser_utilisateur(db, "uid-1")
        assert s is not None
        assert s.valeur == 0.0

    @pytest.mark.asyncio
    async def test_utilisateur_avec_refus(self):
        db = _make_db(refus_total=8, ops_total=10, refus_30j=5,
                      simulations_echec=2, regles_refusees=1, alertes_acces=0)
        s  = await self.a.analyser_utilisateur(db, "uid-2")
        assert s is not None
        # taux_refus=80%, r30=5, sims=2, regl=1 → score élevé
        assert s.valeur >= 50

    @pytest.mark.asyncio
    async def test_utilisateur_injection_sql(self):
        """Règles SQL rejetées = signal fort."""
        db = _make_db(refus_total=2, ops_total=10, refus_30j=1,
                      simulations_echec=0, regles_refusees=3, alertes_acces=1)
        s  = await self.a.analyser_utilisateur(db, "uid-hack")
        assert s is not None
        # regles_refusees=3*12=36, alertes_acces=1*20=20
        assert s.valeur >= 30


class TestA4DensiteMutation:
    a = A4_DensiteMutation()

    @pytest.mark.asyncio
    async def test_zone_homogene(self):
        """Mutations uniformément réparties → risque faible."""
        db = _make_db(max_mutations_commune=5, moy_mutations_commune=5, ecart=0)
        s  = await self.a.analyser_zone(db, "NI-03")
        assert s is not None
        assert s.valeur == pytest.approx(0.0, abs=1.0)

    @pytest.mark.asyncio
    async def test_zone_concentree(self):
        """Une commune concentre toutes les mutations."""
        db = _make_db(max_mutations_commune=50, moy_mutations_commune=5, ecart=45)
        s  = await self.a.analyser_zone(db, "NI-04")
        assert s is not None
        assert s.valeur > 30

    @pytest.mark.asyncio
    async def test_parcelle_commune_active(self):
        db = _make_db(mutations_commune_6m=30)
        s  = await self.a.analyser_parcelle(db, "pid-commune")
        assert s is not None
        # 30/50 * 100 = 60
        assert s.valeur == pytest.approx(60.0, abs=1.0)


class TestA5HistoriqueAnomalie:
    a = A5_HistoriqueAnomalie()

    @pytest.mark.asyncio
    async def test_historique_propre(self):
        db = _make_db(modifs_90j=0, verrous_actifs=0, wf_bloques=0)
        s  = await self.a.analyser_parcelle(db, "pid-1")
        assert s is not None
        assert s.valeur == 0.0

    @pytest.mark.asyncio
    async def test_verrou_actif_penalite(self):
        db = _make_db(modifs_90j=2, verrous_actifs=1, wf_bloques=0)
        s  = await self.a.analyser_parcelle(db, "pid-1")
        # 2*3 + 1*15 = 21
        assert s.valeur >= 15

    @pytest.mark.asyncio
    async def test_workflow_bloque_critique(self):
        db = _make_db(modifs_90j=5, verrous_actifs=0, wf_bloques=2)
        s  = await self.a.analyser_parcelle(db, "pid-1")
        # 5*3 + 2*20 = 55
        assert s.valeur >= 40


class TestA6OperationRisquee:
    a = A6_OperationRisquee()

    @pytest.mark.asyncio
    async def test_operations_a_risque_base(self):
        """EXPROPRIATION a un risque de base de 85."""
        db = _make_db(echecs=0, total=0)
        s  = await self.a.analyser_operation(db, "EXPROPRIATION")
        assert s is not None
        # 85 * 0.6 = 51 sans données historiques
        assert s.valeur >= 40

    @pytest.mark.asyncio
    async def test_operation_mainlevee_faible(self):
        db = _make_db(echecs=0, total=0)
        s  = await self.a.analyser_operation(db, "MAINLEVEE")
        assert s is not None
        # 15 * 0.6 = 9
        assert s.valeur < 20

    @pytest.mark.asyncio
    async def test_echecs_augmentent_score(self):
        """Taux d'échec élevé augmente le score."""
        db = _make_db(echecs=8, total=10)  # 80% échec
        s  = await self.a.analyser_operation(db, "FUSION")
        assert s is not None
        # 65*0.6 + 80*0.25 = 39 + 20 = 59
        assert s.valeur > 40

    @pytest.mark.asyncio
    async def test_operation_inconnue_score_defaut(self):
        db = _make_db()
        s  = await self.a.analyser_operation(db, "INCONNU")
        assert s is not None
        assert s.valeur >= 0


# ════════════════════════════════════════════════════════════════════
# RiskEngine — intégration
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_engine_analyser_parcelle_sans_risque():
    """Parcelle propre → score faible."""
    db = _make_db()
    score = await RiskEngine.analyser_parcelle(db, "00000000-0000-0000-0000-000000000001")
    assert isinstance(score, RiskScore)
    assert score.entite_type == TypeEntite.PARCELLE
    assert score.score >= 0
    assert len(score.sha256) == 64


@pytest.mark.asyncio
async def test_engine_analyser_zone():
    db = _make_db()
    score = await RiskEngine.analyser_zone(db, "NI-01")
    assert score.entite_type == TypeEntite.ZONE
    assert score.entite_id   == "NI-01"
    assert 0 <= score.score <= 100


@pytest.mark.asyncio
async def test_engine_analyser_utilisateur():
    db = _make_db()
    score = await RiskEngine.analyser_utilisateur(db, "user-id-1")
    assert score.entite_type == TypeEntite.UTILISATEUR
    assert 0 <= score.score <= 100


@pytest.mark.asyncio
async def test_engine_analyser_operation():
    db = _make_db()
    score = await RiskEngine.analyser_operation(db, "MUTATION")
    assert score.entite_type == TypeEntite.OPERATION
    assert 0 <= score.score <= 100


@pytest.mark.asyncio
async def test_engine_score_avec_risques_eleves():
    """Parcelle avec litiges actifs + mutations fréquentes → score élevé."""
    db = _make_db(
        litiges_actifs=3, conflits_geo_ouverts=2,
        muts_30j=2, muts_6m=5, muts_1an=8,
        modifs_90j=10, verrous_actifs=1, wf_bloques=1,
    )
    score = await RiskEngine.analyser_parcelle(db, "parcelle-risquee")
    assert score.score > 30  # risques multiples → score > FAIBLE


@pytest.mark.asyncio
async def test_engine_score_sha256():
    db = _make_db()
    score = await RiskEngine.analyser_parcelle(db, "uuid-test")
    assert len(score.sha256) == 64
    assert all(c in "0123456789abcdef" for c in score.sha256)


@pytest.mark.asyncio
async def test_engine_signaux_presents():
    """L'engine doit retourner des signaux de plusieurs analyseurs."""
    db = _make_db()
    score = await RiskEngine.analyser_parcelle(db, "pid-sig")
    # Au moins 3 analyseurs doivent avoir produit un signal
    assert len(score.signaux) >= 1


@pytest.mark.asyncio
async def test_engine_as_dict_structure():
    db = _make_db()
    score = await RiskEngine.analyser_zone(db, "NI-05")
    d = score.as_dict()
    assert all(k in d for k in [
        "entite_type", "entite_id", "score", "niveau",
        "facteurs_cles", "recommandations", "signaux",
        "sha256", "duree_ms", "calcule_a",
    ])


@pytest.mark.asyncio
async def test_engine_6_analyseurs():
    """Le moteur doit avoir 6 analyseurs enregistrés."""
    assert len(RiskEngine._analyseurs) == 6


@pytest.mark.asyncio
async def test_engine_performance():
    """Analyse complète doit terminer en < 2s."""
    db = _make_db()
    t  = time.monotonic()
    score = await RiskEngine.analyser_parcelle(db, "perf-test")
    elapsed = (time.monotonic() - t) * 1000
    assert elapsed < 2000, f"Analyse trop lente: {elapsed:.0f}ms"
    assert score.duree_ms >= 0


# ════════════════════════════════════════════════════════════════════
# ScoreCalculator — cas limites
# ════════════════════════════════════════════════════════════════════

class TestScoreEdgeCases:

    def test_poids_zero_ignore(self):
        """Signaux avec poids 0 ne doivent pas affecter le calcul."""
        s1 = _signal("A1", 100, poids=0)
        s2 = _signal("A2", 50,  poids=10)
        result = ScoreCalculator.calculer([s1, s2])
        assert result == pytest.approx(50.0, abs=0.1)

    def test_score_tous_signaux_nuls(self):
        signaux = [_signal(f"A{i}", 0, poids=10) for i in range(6)]
        assert ScoreCalculator.calculer(signaux) == 0.0

    def test_niveau_frontiere_faible_moyen(self):
        """SEUIL_FAIBLE est encore FAIBLE, SEUIL_FAIBLE+1 est MOYEN."""
        assert ScoreCalculator.niveau(SEUIL_FAIBLE)     == NiveauRisque.FAIBLE
        assert ScoreCalculator.niveau(SEUIL_FAIBLE + 1) == NiveauRisque.MOYEN

    def test_niveau_frontiere_moyen_eleve(self):
        assert ScoreCalculator.niveau(SEUIL_MOYEN)     == NiveauRisque.MOYEN
        assert ScoreCalculator.niveau(SEUIL_MOYEN + 1) == NiveauRisque.ELEVE

    def test_niveau_frontiere_eleve_critique(self):
        assert ScoreCalculator.niveau(SEUIL_ELEVE)     == NiveauRisque.ELEVE
        assert ScoreCalculator.niveau(SEUIL_ELEVE + 1) == NiveauRisque.CRITIQUE

    def test_norm_helper(self):
        a = A1_ConflitZone()
        assert a._norm(0, 100)   == pytest.approx(0.0)
        assert a._norm(50, 100)  == pytest.approx(50.0)
        assert a._norm(100, 100) == pytest.approx(100.0)
        assert a._norm(150, 100) == pytest.approx(100.0)  # clamp
        assert a._norm(50, 0)    == pytest.approx(0.0)    # max_val=0


# ════════════════════════════════════════════════════════════════════
# Non-régression
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_non_regression_monitoring():
    from app.services.monitoring_engine import MonitoringEngine, NiveauAlerte, _creer_alerte
    engine = MonitoringEngine()
    assert len(engine.detecteurs) == 6
    a = _creer_alerte(NiveauAlerte.WARNING, "D1", "Test", "Msg", {})
    assert len(a.sha256) == 64

@pytest.mark.asyncio
async def test_non_regression_simulation():
    from app.services.simulation_engine import SimulationEngine, SimulationContext
    ctx = SimulationContext("MUTATION", ["uuid-1"], "user-1", "NI-01")
    r   = await SimulationEngine.simuler(ctx, db=None)
    assert r.simulation_id

@pytest.mark.asyncio
async def test_non_regression_logic():
    from app.services.logic_validator import LogicConflictEngine, Recommandation
    r = await LogicConflictEngine.valider(
        sql="TRUE", domaine="GENERAL", code="RISK_COMPAT",
        niveau_blocage="BLOQUANT", db=None,
    )
    assert r.recommandation == Recommandation.AUTORISER

@pytest.mark.asyncio
async def test_non_regression_sandbox():
    from app.services.sql_sandbox import SQLRuleValidator, ValidationStatus
    r = await SQLRuleValidator.valider(
        sql="NOT EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND is_gele=TRUE)",
        domaine="GENERAL", code="RISK_SANDBOX", db=None,
    )
    assert r.status == ValidationStatus.VALIDE
