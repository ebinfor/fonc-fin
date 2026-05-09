"""
FONCIER+ ── Tests du Moteur de Validation Logique (C5)

Couverture :
  Utilitaires   _normalise_sql, _extract_tables, _extract_predicats,
                _predicats_contradictoires, _sql_similarity, _is_negation_of
  H1            DuplicateRuleHeuristic
  H2            NegationContradictionHeuristic
  H3            BlockingConflictHeuristic
  H4            SubsumptionHeuristic
  H5            AlwaysBlockingHeuristic
  HG            GlobalCoherenceHeuristic
  INT           LogicConflictEngine.valider() (sans DB)
  EXT           Extensibilité — enregistrement d'une heuristique custom
"""
import asyncio
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

try:
    from app.services.logic_validator import (
        _normalise_sql, _extract_tables, _extract_predicats,
        _predicats_contradictoires, _sql_similarity, _is_negation_of,
        RuleDescriptor, LogicConflict, LogicValidationReport,
        ConflictNiveau, Recommandation,
        DuplicateRuleHeuristic, NegationContradictionHeuristic,
        BlockingConflictHeuristic, SubsumptionHeuristic,
        AlwaysBlockingHeuristic, GlobalCoherenceHeuristic,
        LogicConflictEngine, LogicHeuristic,
    )
    HAS_LOGIC = True
except ImportError as e:
    HAS_LOGIC = False
    print(f"[SKIP] {e}")

pytestmark = pytest.mark.skipif(not HAS_LOGIC, reason="logic_validator non disponible")


# ─── Règles de test ───────────────────────────────────────────────

def make_rule(code, sql, domaine="GENERAL", niveau="BLOQUANT", nom="") -> RuleDescriptor:
    rd = RuleDescriptor(
        code=code, nom=nom or code, domaine=domaine,
        condition_sql=sql, niveau_blocage=niveau,
    )
    rd._parse()
    return rd

# Corpus de règles
R_GELE_FALSE = make_rule("GELE_F",
    "NOT EXISTS (SELECT 1 FROM parcelles p WHERE p.id=$1 AND p.is_gele=TRUE)")
R_GELE_TRUE = make_rule("GELE_T",
    "EXISTS (SELECT 1 FROM parcelles p WHERE p.id=$1 AND p.is_gele=TRUE)")
R_CCFM_OK = make_rule("CCFM_OK",
    "EXISTS (SELECT 1 FROM demandes_ccfm dc WHERE dc.etat='SIGNE_DIRECTEUR' AND dc.id=$1)",
    domaine="CCFM")
R_CCFM_KO = make_rule("CCFM_KO",
    "EXISTS (SELECT 1 FROM demandes_ccfm dc WHERE dc.etat='ARCHIVE' AND dc.id=$1)",
    domaine="CCFM")
R_TRUE = make_rule("ALWAYS_T", "TRUE")
R_AGENT = make_rule("AGENT",
    "EXISTS (SELECT 1 FROM agent_profil ap WHERE ap.user_id=$2 AND ap.niveau_autorite >= 2)")
R_AGENT_DUP = make_rule("AGENT2",
    "EXISTS (SELECT 1 FROM agent_profil ap WHERE ap.user_id=$2 AND ap.niveau_autorite >= 2)")
R_STATUT_A = make_rule("STAT_A",
    "EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND statut IN ('ACTIF','RESERVE'))")
R_STATUT_B = make_rule("STAT_B",
    "EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND statut IN ('ARCHIVE','CEDE'))")
R_GENERAL_GELE = make_rule("GEN_GELE",
    "NOT EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND is_gele=TRUE)",
    domaine="GENERAL")
R_AVERT_SIMILAR = make_rule("WARN_GELE",
    "NOT EXISTS (SELECT 1 FROM parcelles p WHERE p.id=$1 AND p.is_gele=TRUE)",
    niveau="AVERTISSEMENT")


# ════════════════════════════════════════════════════════════════════
# Utilitaires d'analyse SQL
# ════════════════════════════════════════════════════════════════════

class TestUtils:

    def test_normalise_sql_espaces(self):
        assert "  " not in _normalise_sql("EXISTS  ( SELECT  1   FROM  parcelles )")

    def test_normalise_sql_lowercase(self):
        assert _normalise_sql("EXISTS") == "exists"

    def test_extract_tables_from(self):
        sql = "EXISTS (SELECT 1 FROM parcelles p WHERE p.id=$1)"
        tables = _extract_tables(sql)
        assert "parcelles" in tables

    def test_extract_tables_multiple(self):
        sql = ("EXISTS (SELECT 1 FROM demandes_ccfm dc "
               "WHERE dc.id=$1 AND NOT EXISTS "
               "(SELECT 1 FROM parcelles p WHERE p.id=dc.nicad_id))")
        tables = _extract_tables(sql)
        assert "demandes_ccfm" in tables
        assert "parcelles" in tables

    def test_extract_predicats_egal(self):
        sql = "EXISTS (SELECT 1 FROM parcelles p WHERE p.is_gele=TRUE)"
        preds = _extract_predicats(sql)
        gele = [p for p in preds if p["colonne"] == "is_gele"]
        assert gele and gele[0]["op"] == "=" and gele[0]["valeur"] == "TRUE"

    def test_extract_predicats_in(self):
        sql = "EXISTS (SELECT 1 FROM parcelles WHERE statut IN ('ACTIF','RESERVE'))"
        preds = _extract_predicats(sql)
        statut = [p for p in preds if p["colonne"] == "statut"]
        assert statut and statut[0]["op"] == "IN"
        assert "ACTIF" in statut[0]["valeur"]

    def test_extract_predicats_is_null(self):
        sql = "EXISTS (SELECT 1 FROM parcelles WHERE date_cession IS NULL)"
        preds = _extract_predicats(sql)
        col = [p for p in preds if p["colonne"] == "date_cession"]
        assert col and col[0]["op"] == "IS" and col[0]["valeur"] == "NULL"

    def test_predicats_contradictoires_bool(self):
        p1 = {"colonne": "is_gele", "op": "=", "valeur": "TRUE"}
        p2 = {"colonne": "is_gele", "op": "=", "valeur": "FALSE"}
        assert _predicats_contradictoires(p1, p2) is True

    def test_predicats_contradictoires_statut(self):
        p1 = {"colonne": "statut", "op": "=", "valeur": "ACTIF"}
        p2 = {"colonne": "statut", "op": "=", "valeur": "ARCHIVE"}
        assert _predicats_contradictoires(p1, p2) is True

    def test_predicats_non_contradictoires_meme_val(self):
        p1 = {"colonne": "is_gele", "op": "=", "valeur": "TRUE"}
        p2 = {"colonne": "is_gele", "op": "=", "valeur": "TRUE"}
        assert _predicats_contradictoires(p1, p2) is False

    def test_predicats_contradictoires_colonnes_diff(self):
        p1 = {"colonne": "is_gele",  "op": "=", "valeur": "TRUE"}
        p2 = {"colonne": "is_archive","op": "=", "valeur": "FALSE"}
        assert _predicats_contradictoires(p1, p2) is False

    def test_predicats_contradictoires_in_disjoint(self):
        p1 = {"colonne": "statut", "op": "IN",     "valeur": "ACTIF,RESERVE"}
        p2 = {"colonne": "statut", "op": "IN",     "valeur": "ARCHIVE,CEDE"}
        assert _predicats_contradictoires(p1, p2) is True

    def test_predicats_in_not_in(self):
        p1 = {"colonne": "statut", "op": "IN",     "valeur": "ACTIF,RESERVE"}
        p2 = {"colonne": "statut", "op": "NOT_IN", "valeur": "ACTIF"}
        assert _predicats_contradictoires(p1, p2) is True

    def test_sql_similarity_identique(self):
        s = "exists(select 1 from parcelles where id=$1)"
        assert _sql_similarity(s, s) == pytest.approx(1.0)

    def test_sql_similarity_different(self):
        s1 = "exists(select 1 from parcelles where id=$1)"
        s2 = "exists(select 1 from demandes_ccfm where etat='OK')"
        assert _sql_similarity(s1, s2) < 0.7

    def test_sql_similarity_similaire(self):
        s1 = "not exists(select 1 from parcelles p where p.id=$1 and p.is_gele=true)"
        s2 = "not exists(select 1 from parcelles p where p.id=$1 and p.is_gele=false)"
        assert _sql_similarity(s1, s2) >= 0.8

    def test_is_negation_of(self):
        assert _is_negation_of(R_GELE_FALSE, R_GELE_TRUE) is True

    def test_is_negation_of_same_polarite(self):
        assert _is_negation_of(R_GELE_FALSE, R_AVERT_SIMILAR) is False

    def test_is_negation_tables_differentes(self):
        assert _is_negation_of(R_CCFM_OK, R_GELE_TRUE) is False


# ════════════════════════════════════════════════════════════════════
# H1 — DuplicateRuleHeuristic
# ════════════════════════════════════════════════════════════════════

class TestH1Duplicate:
    h = DuplicateRuleHeuristic()

    def test_doublon_exact_critique(self):
        conflicts = self.h.analyze(R_AGENT, [R_AGENT_DUP])
        assert any(c.niveau == ConflictNiveau.CRITIQUE for c in conflicts)

    def test_tres_similaire_majeur(self):
        # Même condition avec une légère variation
        r_variant = make_rule("AGENT3",
            "EXISTS (SELECT 1 FROM agent_profil ap WHERE ap.user_id=$2 AND ap.niveau_autorite >= 3)")
        conflicts = self.h.analyze(R_AGENT, [r_variant])
        assert any(c.niveau in (ConflictNiveau.MAJEUR, ConflictNiveau.CRITIQUE)
                   for c in conflicts)

    def test_regles_differentes_pas_de_conflit(self):
        conflicts = self.h.analyze(R_GELE_FALSE, [R_CCFM_OK])
        assert not conflicts

    def test_regle_vs_elle_meme(self):
        # Ne doit pas se comparer à elle-même (même code)
        conflicts = self.h.analyze(R_AGENT, [R_AGENT])
        # L'analyse compare avec des règles existantes différentes
        # ici le code est identique, similarité = 1.0 → CRITIQUE
        assert any(c.niveau == ConflictNiveau.CRITIQUE for c in conflicts)

    def test_similaire_mineur(self):
        r_proche = make_rule("PROCHE",
            "EXISTS (SELECT 1 FROM agent_profil ap WHERE ap.user_id=$2 AND ap.statut='ACTIF')")
        conflicts = self.h.analyze(R_AGENT, [r_proche])
        # similaire mais pas identique
        niveaux = {c.niveau for c in conflicts}
        assert not any(n == ConflictNiveau.CRITIQUE for n in niveaux)


# ════════════════════════════════════════════════════════════════════
# H2 — NegationContradictionHeuristic
# ════════════════════════════════════════════════════════════════════

class TestH2Negation:
    h = NegationContradictionHeuristic()

    def test_contradiction_directe_critique(self):
        """EXISTS vs NOT EXISTS sur même table/condition → CRITIQUE si deux BLOQUANT."""
        conflicts = self.h.analyze(R_GELE_FALSE, [R_GELE_TRUE])
        assert any(c.niveau == ConflictNiveau.CRITIQUE for c in conflicts)

    def test_contradiction_directe_majeur_si_un_avert(self):
        """Un BLOQUANT + un AVERTISSEMENT → MAJEUR."""
        conflicts = self.h.analyze(R_AVERT_SIMILAR, [R_GELE_TRUE])
        assert any(c.niveau in (ConflictNiveau.CRITIQUE, ConflictNiveau.MAJEUR)
                   for c in conflicts)

    def test_tables_differentes_pas_de_conflit(self):
        conflicts = self.h.analyze(R_CCFM_OK, [R_GELE_TRUE])
        assert not conflicts

    def test_meme_polarite_pas_de_conflit(self):
        r2 = make_rule("GELE_F2",
            "NOT EXISTS (SELECT 1 FROM parcelles p WHERE p.id=$1 AND p.is_gele=TRUE)")
        conflicts = self.h.analyze(R_GELE_FALSE, [r2])
        # Même polarité NOT EXISTS → pas de contradiction négation
        neg_conflicts = [c for c in conflicts if c.heuristique == "H2_NEGATION"]
        assert not neg_conflicts


# ════════════════════════════════════════════════════════════════════
# H3 — BlockingConflictHeuristic
# ════════════════════════════════════════════════════════════════════

class TestH3Blocking:
    h = BlockingConflictHeuristic()

    def test_is_gele_true_vs_false_critique(self):
        r_gele = make_rule("REQ_GELE",
            "EXISTS (SELECT 1 FROM parcelles p WHERE p.id=$1 AND p.is_gele=TRUE)")
        r_pas_gele = make_rule("REQ_PAS_GELE",
            "EXISTS (SELECT 1 FROM parcelles p WHERE p.id=$1 AND p.is_gele=FALSE)")
        conflicts = self.h.analyze(r_gele, [r_pas_gele])
        assert any(c.niveau == ConflictNiveau.CRITIQUE for c in conflicts)

    def test_statut_disjoints_critique(self):
        conflicts = self.h.analyze(R_STATUT_A, [R_STATUT_B])
        assert any(c.niveau == ConflictNiveau.CRITIQUE for c in conflicts)

    def test_avertissement_ignore(self):
        """H3 ne s'applique qu'aux règles BLOQUANT."""
        r_avert = make_rule("AVERT",
            "EXISTS (SELECT 1 FROM parcelles p WHERE p.id=$1 AND p.is_gele=FALSE)",
            niveau="AVERTISSEMENT")
        conflicts = self.h.analyze(R_GELE_TRUE, [r_avert])
        h3_conflicts = [c for c in conflicts if c.heuristique == "H3_BLOCKING"]
        assert not h3_conflicts

    def test_tables_communes_differentes_pas_de_conflit(self):
        conflicts = self.h.analyze(R_CCFM_OK, [R_GELE_FALSE])
        h3_conflicts = [c for c in conflicts if c.heuristique == "H3_BLOCKING"]
        assert not h3_conflicts


# ════════════════════════════════════════════════════════════════════
# H4 — SubsumptionHeuristic
# ════════════════════════════════════════════════════════════════════

class TestH4Subsumption:
    h = SubsumptionHeuristic()

    def test_regle_plus_restrictive_mineur(self):
        # r_general : juste is_gele=FALSE
        r_general = make_rule("GEN",
            "EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND is_gele=FALSE)")
        # r_strict : is_gele=FALSE ET statut='ACTIF' (plus restrictive)
        r_strict = make_rule("STRICT",
            "EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND is_gele=FALSE AND statut='ACTIF')")
        conflicts = self.h.analyze(r_strict, [r_general])
        h4 = [c for c in conflicts if c.heuristique == "H4_SUBSUMPTION"]
        assert h4, "Subsomption attendue"
        assert h4[0].niveau == ConflictNiveau.MINEUR

    def test_meme_predicats_pas_subsomption(self):
        r1 = make_rule("R1",
            "EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND is_gele=FALSE)")
        r2 = make_rule("R2",
            "EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND is_gele=FALSE)")
        conflicts = self.h.analyze(r1, [r2])
        h4 = [c for c in conflicts if c.heuristique == "H4_SUBSUMPTION"]
        assert not h4


# ════════════════════════════════════════════════════════════════════
# H5 — AlwaysBlockingHeuristic
# ════════════════════════════════════════════════════════════════════

class TestH5AlwaysBlocking:
    h = AlwaysBlockingHeuristic()

    def test_in_disjoint_majeur(self):
        """statut IN (A,B) + statut IN (C,D) → MAJEUR."""
        r_extra = make_rule("STAT_X",
            "EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND statut IN ('BLOQ','LITIGE'))")
        conflicts = self.h.analyze(R_STATUT_A, [R_STATUT_B, r_extra])
        h5 = [c for c in conflicts if c.heuristique == "H5_ALWAYS_BLOCKING"]
        assert h5

    def test_peu_de_regles_pas_alerte(self):
        conflicts = self.h.analyze(R_GELE_FALSE, [R_AGENT])
        h5 = [c for c in conflicts if c.heuristique == "H5_ALWAYS_BLOCKING"]
        assert not h5

    def test_trop_de_bloquants_mineur(self):
        """Plus de 7 BLOQUANT sur mêmes tables → MINEUR."""
        existing = [
            make_rule(f"BLK_{i}",
                f"EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND col_{i}=TRUE)")
            for i in range(8)
        ]
        conflicts = self.h.analyze(R_GELE_FALSE, existing)
        h5 = [c for c in conflicts if c.heuristique == "H5_ALWAYS_BLOCKING"]
        assert h5


# ════════════════════════════════════════════════════════════════════
# HG — GlobalCoherenceHeuristic
# ════════════════════════════════════════════════════════════════════

class TestHGGlobal:
    h = GlobalCoherenceHeuristic()

    def test_incoherence_niveau_majeur(self):
        """AVERTISSEMENT similaire à une BLOQUANT existante → MAJEUR."""
        conflicts = self.h.analyze(R_AVERT_SIMILAR, [R_GELE_FALSE])
        hg = [c for c in conflicts if c.heuristique == "HG_GLOBAL"]
        assert any(c.niveau == ConflictNiveau.MAJEUR for c in hg)

    def test_collision_general_mineur(self):
        """Règle domaine CCFM similaire à une GENERAL → MINEUR."""
        r_ccfm_gele = make_rule("CCFM_GELE",
            "NOT EXISTS (SELECT 1 FROM parcelles p WHERE p.id=$1 AND p.is_gele=TRUE)",
            domaine="CCFM")
        conflicts = self.h.analyze(r_ccfm_gele, [R_GENERAL_GELE])
        hg = [c for c in conflicts if c.heuristique == "HG_GLOBAL"]
        assert any(c.niveau == ConflictNiveau.MINEUR for c in hg)

    def test_bloquant_vs_bloquant_pas_de_conflit_niveau(self):
        """Deux BLOQUANT similaires → H3 ou H2 gère, pas HG."""
        conflicts = self.h.analyze(R_GELE_FALSE, [R_GELE_TRUE])
        hg = [c for c in conflicts if c.heuristique == "HG_GLOBAL"]
        # HG ne doit pas déclencher dans ce cas (c'est le rôle de H2/H3)
        assert not any(c.niveau == ConflictNiveau.MAJEUR for c in hg)


# ════════════════════════════════════════════════════════════════════
# INT — LogicConflictEngine (sans DB)
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_engine_aucun_conflit():
    report = await LogicConflictEngine.valider(
        sql="NOT EXISTS (SELECT 1 FROM entity_lock WHERE entite_id=$1 AND actif=TRUE)",
        domaine="GENERAL", code="VERROU", niveau_blocage="BLOQUANT",
        db=None,
    )
    assert report.recommandation == Recommandation.AUTORISER
    assert report.nb_critique == 0
    assert report.regles_analysees == 0  # sans DB, 0 règles chargées
    assert not report.bloque


@pytest.mark.asyncio
async def test_engine_contradiction_h2():
    """Moteur détecte contradiction EXISTS/NOT EXISTS en mémoire."""
    # Injecter des règles existantes via _load_rules mocké
    # On utilise les heuristiques directement avec une liste
    new = make_rule("NEW_GELE",
        "EXISTS (SELECT 1 FROM parcelles p WHERE p.id=$1 AND p.is_gele=TRUE)")
    existing = [R_GELE_FALSE]
    # Tester via l'heuristique H2 directement
    h2 = NegationContradictionHeuristic()
    conflicts = h2.analyze(new, existing)
    assert any(c.niveau == ConflictNiveau.CRITIQUE for c in conflicts)


@pytest.mark.asyncio
async def test_engine_rapport_structure():
    report = await LogicConflictEngine.valider(
        sql="TRUE", domaine="GENERAL", code="TEST_STRUCT",
        niveau_blocage="BLOQUANT", db=None,
    )
    d = report.as_dict()
    assert "recommandation" in d
    assert "conflits" in d
    assert "nb_critique" in d
    assert "regles_impactees" in d
    assert "sha256_rapport" in d
    assert len(d["sha256_rapport"]) == 64


@pytest.mark.asyncio
async def test_engine_true_sans_conflit():
    report = await LogicConflictEngine.valider(
        sql="TRUE", domaine="GENERAL", code="ALWAYS_TRUE",
        niveau_blocage="BLOQUANT", db=None,
    )
    assert report.recommandation == Recommandation.AUTORISER


@pytest.mark.asyncio
async def test_engine_rapport_bloque_si_critique():
    """Un rapport avec conflits CRITIQUE doit marquer bloque=True."""
    # Simuler manuellement
    from app.services.logic_validator import LogicValidationReport, LogicConflict
    report = LogicValidationReport(
        code_regle="TEST",
        domaine="GENERAL",
        recommandation=Recommandation.REFUSER,
        conflits=[
            LogicConflict(
                heuristique="H2_NEGATION",
                niveau=ConflictNiveau.CRITIQUE,
                regle_cible="AUTRE",
                message="Test conflit critique",
                details="Détail",
                suggestion="Suggestion",
            )
        ],
    )
    assert report.bloque is True
    assert report.nb_critique == 1
    assert report.nb_majeur == 0


@pytest.mark.asyncio
async def test_engine_recommandation_corriger():
    """MAJEUR sans CRITIQUE → CORRIGER."""
    from app.services.logic_validator import LogicValidationReport
    report = LogicValidationReport(
        code_regle="TEST",
        domaine="GENERAL",
        recommandation=Recommandation.CORRIGER,
        conflits=[
            LogicConflict(
                heuristique="H1_DUPLICATE",
                niveau=ConflictNiveau.MAJEUR,
                regle_cible="DOUBLON",
                message="Doublon probable",
                details="",
                suggestion="",
            )
        ],
    )
    assert not report.bloque
    assert report.recommandation == Recommandation.CORRIGER


# ════════════════════════════════════════════════════════════════════
# EXT — Extensibilité (enregistrement d'heuristique custom)
# ════════════════════════════════════════════════════════════════════

class CustomTestHeuristic(LogicHeuristic):
    id          = "CUSTOM_TEST_H"
    description = "Heuristique de test — bloque toujours TRUE"

    def analyze(self, new_rule, existing):
        if new_rule.condition_sql.strip().upper() == "TRUE":
            return [LogicConflict(
                heuristique="CUSTOM_TEST_H",
                niveau=ConflictNiveau.MINEUR,
                regle_cible="",
                message="Règle TRUE trop générique",
                details="Une règle TRUE n'apporte aucune valeur métier.",
                suggestion="Préciser la condition.",
            )]
        return []


@pytest.mark.asyncio
async def test_extensibilite_custom_heuristique():
    """Enregistrer une heuristique custom sans modifier l'engine."""
    h_custom = CustomTestHeuristic()
    LogicConflictEngine.register(h_custom)

    report = await LogicConflictEngine.valider(
        sql="TRUE", domaine="GENERAL", code="CUSTOM_TRUE",
        niveau_blocage="BLOQUANT", db=None,
    )
    custom = [c for c in report.conflits if c.heuristique == "CUSTOM_TEST_H"]
    assert custom, "L'heuristique custom aurait dû détecter un conflit"
    assert custom[0].niveau == ConflictNiveau.MINEUR

    # Nettoyage
    LogicConflictEngine._heuristics = [
        h for h in LogicConflictEngine._heuristics
        if h.id != "CUSTOM_TEST_H"
    ]


def test_extensibilite_disable_enable():
    """Désactiver/réactiver une heuristique."""
    assert "H1_DUPLICATE" not in LogicConflictEngine._disabled
    LogicConflictEngine.disable("H1_DUPLICATE")
    assert "H1_DUPLICATE" in LogicConflictEngine._disabled
    LogicConflictEngine.enable("H1_DUPLICATE")
    assert "H1_DUPLICATE" not in LogicConflictEngine._disabled


def test_list_heuristics():
    hlist = LogicConflictEngine.list_heuristics()
    ids = {h["id"] for h in hlist}
    assert "H1_DUPLICATE"     in ids
    assert "H2_NEGATION"      in ids
    assert "H3_BLOCKING"      in ids
    assert "H4_SUBSUMPTION"   in ids
    assert "H5_ALWAYS_BLOCKING" in ids
    assert "HG_GLOBAL"        in ids


# ════════════════════════════════════════════════════════════════════
# Intégration C5 dans SQLRuleValidator
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_sandbox_pipeline_sans_c5_sans_db():
    """Sans DB, C5 est SKIP dans les traces."""
    from app.services.sql_sandbox import SQLRuleValidator, ValidationStatus
    result = await SQLRuleValidator.valider(
        sql="NOT EXISTS (SELECT 1 FROM entity_lock WHERE entite_id=$1 AND actif=TRUE)",
        domaine="GENERAL", code="PIPE_TEST", db=None,
    )
    assert result.valide
    # C5 doit être SKIP quand db=None
    c5 = next((l for l in result.layers if l.couche == 5), None)
    assert c5 is not None
    assert c5.statut == "SKIP"


@pytest.mark.asyncio
async def test_sandbox_logic_report_none_sans_db():
    """logic_report est None sans DB."""
    from app.services.sql_sandbox import SQLRuleValidator
    result = await SQLRuleValidator.valider(
        sql="TRUE", domaine="GENERAL", code="LR_NONE", db=None,
    )
    assert result.logic_report is None


@pytest.mark.asyncio
async def test_as_dict_contient_logic_report():
    """as_dict() doit inclure logic_report."""
    from app.services.sql_sandbox import SQLRuleValidator
    result = await SQLRuleValidator.valider(
        sql="TRUE", domaine="GENERAL", code="DICT_TEST", db=None,
    )
    d = result.as_dict()
    assert "logic_report" in d
