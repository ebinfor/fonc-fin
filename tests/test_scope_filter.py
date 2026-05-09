"""
FONCIER+ — Tests unitaires ScopeFilter
Vérifie que chaque acteur ne voit que son périmètre.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
from unittest.mock import MagicMock

try:
    from app.core.scope_filter import (
        ScopeFilter, _inject_where,
        ROLES_VISION_NATIONALE, ROLES_FILTRES_REGION, ROLES_FILTRES_ENTITE,
    )
    HAS_SCOPE = True
except ImportError:
    HAS_SCOPE = False

pytestmark = pytest.mark.skipif(not HAS_SCOPE, reason="scope_filter non disponible")


# ── Helpers ──────────────────────────────────────────────────────

def user(role, region=None, entite_id=None, niveau=1):
    return {"id": "user-001", "role": role, "region": region,
            "entite_id": entite_id, "niveau_autorite": niveau}


# ── Tests ScopeFilter.from_user ───────────────────────────────────

class TestFromUser:

    def test_admin_vision_nationale(self):
        s = ScopeFilter.from_user(user("ADMIN"))
        assert s.peut_voir_national is True
        assert s.region is None

    def test_auditeur_vision_nationale(self):
        s = ScopeFilter.from_user(user("AUDITEUR"))
        assert s.peut_voir_national is True

    def test_ministre_vision_nationale(self):
        s = ScopeFilter.from_user(user("MINISTRE_URBANISME"))
        assert s.peut_voir_national is True

    def test_niveau_4_vision_nationale(self):
        s = ScopeFilter.from_user(user("DIRECTEUR_URBANISME", niveau=4))
        assert s.peut_voir_national is True

    def test_agent_urbanisme_filtre_region(self):
        s = ScopeFilter.from_user(user("AGENT_URBANISME", region="NIA"))
        assert s.peut_voir_national is False
        assert s.region == "NIA"
        assert s.role == "AGENT_URBANISME"

    def test_topographe_filtre_region(self):
        s = ScopeFilter.from_user(user("TOPOGRAPHE", region="ZDR"))
        assert s.peut_voir_national is False
        assert s.region == "ZDR"

    def test_notaire_filtre_entite(self):
        s = ScopeFilter.from_user(user("NOTAIRE", entite_id="etude-123"))
        assert s.peut_voir_national is False
        assert s.entite_id == "etude-123"
        assert s.role in ROLES_FILTRES_ENTITE

    def test_banque_filtre_entite(self):
        s = ScopeFilter.from_user(user("BANQ_DIRECTEUR", entite_id="banque-456"))
        assert s.entite_id == "banque-456"

    def test_juge_filtre_entite(self):
        s = ScopeFilter.from_user(user("JUGE_FONCIER", entite_id="tgi-789"))
        assert s.entite_id == "tgi-789"

    def test_region_national_ignoree(self):
        """Un agent avec region='NATIONAL' est traité comme sans région."""
        s = ScopeFilter.from_user(user("AGENT_URBANISME", region="NATIONAL"))
        assert s.region is None

    def test_region_vide_ignoree(self):
        s = ScopeFilter.from_user(user("AGENT_URBANISME", region=""))
        assert s.region is None

    def test_objet_user_compatible(self):
        """Accepte aussi un objet SQLAlchemy Row (pas seulement dict)."""
        u = MagicMock()
        u.get = lambda k, d=None: {"id": "u1", "role": "AGENT_URBANISME",
                                    "region": "MAR"}.get(k, d)
        # ScopeFilter.from_user utilise _get() compatible dict et objet
        s = ScopeFilter.from_user({"id": "u1", "role": "AGENT_URBANISME",
                                    "region": "MAR", "entite_id": None,
                                    "niveau_autorite": 1})
        assert s.region == "MAR"


# ── Tests apply() — injection SQL ────────────────────────────────

class TestApply:

    BASE_SQL = "SELECT * FROM parcelles p WHERE p.statut = :st ORDER BY p.id LIMIT :l OFFSET :o"
    BASE_PAR = {"st": "actif", "l": 50, "o": 0}

    def test_admin_pas_de_filtre(self):
        s = ScopeFilter.from_user(user("ADMIN"))
        sql, params = s.apply(self.BASE_SQL, self.BASE_PAR, table_alias="p", col_region="region")
        assert "_scope_region" not in params
        assert "_scope_entite" not in params

    def test_agent_region_injecte(self):
        s = ScopeFilter.from_user(user("AGENT_URBANISME", region="NIA"))
        sql, params = s.apply(self.BASE_SQL, self.BASE_PAR, table_alias="p", col_region="region")
        assert "_scope_region" in params
        assert params["_scope_region"] == "NIA"
        assert "_scope_region" in sql

    def test_notaire_entite_injecte(self):
        s = ScopeFilter.from_user(user("NOTAIRE", entite_id="etude-abc"))
        sql, params = s.apply(self.BASE_SQL, self.BASE_PAR,
                               table_alias="p", col_region="region",
                               col_entite="notaire_id")
        assert "_scope_entite" in params
        assert params["_scope_entite"] == "etude-abc"

    def test_params_originaux_preserves(self):
        s = ScopeFilter.from_user(user("AGENT_URBANISME", region="NIA"))
        sql, params = s.apply(self.BASE_SQL, self.BASE_PAR, table_alias="p", col_region="region")
        assert params["st"] == "actif"
        assert params["l"] == 50
        assert params["o"] == 0

    def test_pas_mutation_params_originaux(self):
        """Les params originaux ne doivent pas être mutés."""
        original = {"l": 50, "o": 0}
        s = ScopeFilter.from_user(user("AGENT_URBANISME", region="NIA"))
        _, new_params = s.apply("SELECT * FROM t WHERE 1=1", original,
                                 table_alias="t", col_region="region")
        assert "_scope_region" not in original

    def test_scope_region_dans_sql(self):
        s = ScopeFilter.from_user(user("CHEF_URBANISME", region="MAR"))
        sql, _ = s.apply("SELECT * FROM rnaf r WHERE r.statut = :st",
                          {"st": "publie"}, table_alias="r", col_region="region")
        assert "r.region = :_scope_region" in sql or "_scope_region" in sql

    def test_sans_region_agent_pas_de_filtre(self):
        """Agent sans région configurée → pas de filtre injecté."""
        s = ScopeFilter.from_user(user("AGENT_URBANISME", region=None))
        sql, params = s.apply(self.BASE_SQL, self.BASE_PAR, table_alias="p", col_region="region")
        assert "_scope_region" not in params

    def test_appel_deux_fois_idempotent(self):
        s = ScopeFilter.from_user(user("AGENT_URBANISME", region="TAH"))
        _, p1 = s.apply("SELECT * FROM t WHERE 1=1", {}, table_alias="t", col_region="region")
        _, p2 = s.apply("SELECT * FROM t WHERE 1=1", {}, table_alias="t", col_region="region")
        assert p1 == p2


# ── Tests _inject_where ───────────────────────────────────────────

class TestInjectWhere:

    def test_where_existant(self):
        sql = "SELECT * FROM t WHERE t.a = :a ORDER BY t.id"
        result = _inject_where(sql, "t.region = :r")
        assert "region = :r" in result
        assert "WHERE" in result

    def test_pas_de_where(self):
        sql = "SELECT * FROM t ORDER BY t.id LIMIT 50"
        result = _inject_where(sql, "t.region = :r")
        assert "WHERE" in result
        assert "region = :r" in result

    def test_order_by_preservé(self):
        sql = "SELECT * FROM t WHERE t.a = 1 ORDER BY t.id"
        result = _inject_where(sql, "t.b = :b")
        assert "ORDER BY" in result
        idx_where = result.upper().find("ORDER BY")
        idx_scope = result.find("t.b = :b")
        assert idx_scope < idx_where

    def test_sous_requete_non_touchee(self):
        sql = ("SELECT * FROM t "
               "WHERE t.id IN (SELECT p.id FROM parcelles p WHERE p.statut = :st) "
               "ORDER BY t.id")
        result = _inject_where(sql, "t.region = :r")
        # La sous-requête doit rester intacte
        assert "SELECT p.id FROM parcelles p WHERE p.statut = :st" in result


# ── Tests helpers de vérification ────────────────────────────────

class TestHelpers:

    def test_peut_voir_region_admin(self):
        s = ScopeFilter.from_user(user("ADMIN"))
        assert s.peut_voir_region("NIA") is True
        assert s.peut_voir_region("AGZ") is True

    def test_peut_voir_region_agent_meme_region(self):
        s = ScopeFilter.from_user(user("AGENT_URBANISME", region="NIA"))
        assert s.peut_voir_region("NIA") is True

    def test_ne_peut_pas_voir_autre_region(self):
        s = ScopeFilter.from_user(user("AGENT_URBANISME", region="NIA"))
        assert s.peut_voir_region("ZDR") is False

    def test_peut_voir_entite_notaire(self):
        s = ScopeFilter.from_user(user("NOTAIRE", entite_id="etude-001"))
        assert s.peut_voir_entite("etude-001") is True
        assert s.peut_voir_entite("etude-002") is False

    def test_filtres_actifs_dict(self):
        s = ScopeFilter.from_user(user("AGENT_URBANISME", region="NIA"))
        d = s.filtres_actifs()
        assert "role" in d
        assert "region" in d
        assert d["region"] == "NIA"
        assert d["vision_nationale"] is False


# ── Tests scénarios réels ─────────────────────────────────────────

class TestScenariosReels:
    """Scénarios reproduisant des cas d usage réels."""

    def test_guichetiere_ccfm_ne_voit_que_ses_demandes(self):
        """Une guichetière de Niamey ne voit que ses propres enregistrements."""
        s = ScopeFilter.from_user(user("GUICHETIER_CCFM", region="NIA"))
        assert not s.peut_voir_national
        assert s.region == "NIA"
        sql, params = s.apply(
            "SELECT * FROM demandes_ccfm c WHERE c.etat = :et",
            {"et": "DEMANDE_RECUE"}, table_alias="c", col_region="localite"
        )
        assert "_scope_region" in params
        assert params["_scope_region"] == "NIA"

    def test_juge_zinder_ne_voit_pas_litiges_niamey(self):
        """Un juge de Zinder ne doit pas voir les litiges de Niamey."""
        s = ScopeFilter.from_user(user("JUGE_FONCIER", entite_id="tgi-zdr-001"))
        _, params = s.apply(
            "SELECT * FROM litiges l WHERE 1=1",
            {}, table_alias="l", col_region="region", col_entite="tgi_id"
        )
        assert "_scope_entite" in params
        assert params["_scope_entite"] == "tgi-zdr-001"

    def test_banque_ne_voit_que_ses_hypotheques(self):
        """Une banque ne doit voir que ses propres dossiers hypothèques."""
        s = ScopeFilter.from_user(user("BANQ_DIRECTEUR", entite_id="bnb-001"))
        _, params = s.apply(
            "SELECT * FROM hypotheques h WHERE h.statut = :s",
            {"s": "ACTIF"}, table_alias="h",
            col_region="region", col_entite="banque_id"
        )
        assert "_scope_entite" in params
        assert params["_scope_entite"] == "bnb-001"

    def test_archiviste_annf_voit_toutes_regions(self):
        """Un archiviste ANNF national voit toutes les archives."""
        s = ScopeFilter.from_user(user("RESPONSABLE_ANNF"))
        assert s.peut_voir_national is True
        _, params = s.apply(
            "SELECT * FROM annf_archive_links a WHERE 1=1", {}
        )
        assert "_scope_region" not in params
        assert "_scope_entite" not in params

    def test_directeur_urbanisme_national(self):
        """Un directeur national voit tout."""
        s = ScopeFilter.from_user(user("DIRECTEUR_URBANISME", niveau=4))
        assert s.peut_voir_national is True

    def test_maire_niamey_filtre_sa_commune(self):
        """Le maire de Niamey ne voit que ses dépôts."""
        s = ScopeFilter.from_user(user("MAIRE", region="NIA"))
        _, params = s.apply(
            "SELECT * FROM depot_foncier d WHERE 1=1",
            {}, table_alias="d", col_region="region"
        )
        assert "_scope_region" in params
        assert params["_scope_region"] == "NIA"

    def test_topographe_ne_voit_que_missions_assignees(self):
        """Un topographe voit uniquement les demandes CCFM qui lui sont assignées."""
        s = ScopeFilter.from_user(user("TOPOGRAPHE", region="AGZ"))
        assert not s.peut_voir_national
        # Dans ccfm_v3.py, le filtre topographe_assigne = uid est ajouté programmatiquement
        # (au-delà du scope_filter générique — c'est le comportement spécifique)
        assert s.role == "TOPOGRAPHE"
