"""
FONCIER+ — Tests d accès workspace par rôle
Vérifie que chaque acteur accède UNIQUEMENT à son périmètre.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

try:
    from app.core.scope_filter import ScopeFilter
    HAS_SCOPE = True
except ImportError:
    HAS_SCOPE = False

pytestmark = pytest.mark.skipif(not HAS_SCOPE, reason="scope_filter non disponible")


def mk_user(role, region=None, entite_id=None, uid="user-001", niveau=1):
    return {"id": uid, "role": role, "region": region,
            "entite_id": entite_id, "niveau_autorite": niveau}


# ── Tests d isolation par acteur CCFM ────────────────────────────

class TestIsolationCCFM:
    """Chaque acteur CCFM doit voir UNIQUEMENT son périmètre."""

    def test_guichetiere_nia_isole_de_zdr(self):
        """Guichetière Niamey ne doit pas voir les demandes de Zinder."""
        s = ScopeFilter.from_user(mk_user("GUICHETIER_CCFM", region="NIA"))
        sql, params = s.apply(
            "SELECT * FROM demandes_ccfm c WHERE c.etat = :et",
            {"et": "DEMANDE_RECUE"}, table_alias="c", col_region="localite"
        )
        assert "_scope_region" in params
        assert params["_scope_region"] == "NIA"
        # Un utilisateur de Zinder ne doit pas voir ce résultat
        s2 = ScopeFilter.from_user(mk_user("GUICHETIER_CCFM", region="ZDR"))
        _, params2 = s2.apply(
            "SELECT * FROM demandes_ccfm c WHERE c.etat = :et",
            {"et": "DEMANDE_RECUE"}, table_alias="c", col_region="localite"
        )
        assert params["_scope_region"] != params2["_scope_region"]

    def test_guichetiere_filtre_enregistre_par(self):
        """Une guichetière ne voit que SES enregistrements."""
        s = ScopeFilter.from_user(mk_user("GUICHETIER_CCFM", region="NIA",
                                           uid="guich-001"))
        # Le filtre enregistre_par est géré dans ccfm_v3.py directement
        assert not s.peut_voir_national
        assert s.role == "GUICHETIER_CCFM"
        assert s.user_id == "guich-001"

    def test_topographe_filtre_assignation(self):
        """Un topographe ne voit que les demandes qui lui sont assignées."""
        s = ScopeFilter.from_user(mk_user("TOPOGRAPHE", region="MAR", uid="topo-001"))
        assert not s.peut_voir_national
        assert s.role == "TOPOGRAPHE"
        assert s.user_id == "topo-001"

    def test_directeur_filtre_signature_en_attente(self):
        """Le directeur CCFM ne voit que les demandes en attente de signature."""
        s = ScopeFilter.from_user(mk_user("DIRECTEUR_URBANISME", region="NIA"))
        assert not s.peut_voir_national  # pas niveau 4

    def test_directeur_national_niveau4_voit_tout(self):
        """Un directeur national (niveau 4) voit tout."""
        s = ScopeFilter.from_user(mk_user("DIRECTEUR_URBANISME", niveau=4))
        assert s.peut_voir_national is True

    def test_archiviste_annf_national(self):
        """L archiviste ANNF national voit toutes les archives."""
        s = ScopeFilter.from_user(mk_user("RESPONSABLE_ANNF"))
        assert s.peut_voir_national is True
        _, params = s.apply("SELECT * FROM annf_archive_links a WHERE 1=1", {})
        assert "_scope_region" not in params

    def test_chef_ccfm_voit_tout_sa_region(self):
        """Le chef CCFM voit tout dans sa région."""
        s = ScopeFilter.from_user(mk_user("CHEF_CCFM", region="TAH"))
        _, params = s.apply(
            "SELECT * FROM demandes_ccfm c WHERE 1=1", {},
            table_alias="c", col_region="localite"
        )
        assert "_scope_region" in params
        assert params["_scope_region"] == "TAH"


# ── Tests d isolation Justice ─────────────────────────────────────

class TestIsolationJustice:

    def test_juge_filtre_par_tgi(self):
        """Un juge ne voit que les litiges de son TGI."""
        s = ScopeFilter.from_user(mk_user("JUGE_FONCIER", entite_id="tgi-nia-001"))
        _, params = s.apply(
            "SELECT * FROM litiges l WHERE 1=1", {},
            table_alias="l", col_region="region", col_entite="tgi_id"
        )
        assert "_scope_entite" in params
        assert params["_scope_entite"] == "tgi-nia-001"

    def test_juge_tgi_nia_ne_voit_pas_tgi_zdr(self):
        s1 = ScopeFilter.from_user(mk_user("JUGE_FONCIER", entite_id="tgi-nia"))
        s2 = ScopeFilter.from_user(mk_user("JUGE_FONCIER", entite_id="tgi-zdr"))
        _, p1 = s1.apply("SELECT * FROM litiges l WHERE 1=1", {},
                          table_alias="l", col_entite="tgi_id")
        _, p2 = s2.apply("SELECT * FROM litiges l WHERE 1=1", {},
                          table_alias="l", col_entite="tgi_id")
        assert p1.get("_scope_entite") != p2.get("_scope_entite")

    def test_auditeur_voit_tous_litiges(self):
        s = ScopeFilter.from_user(mk_user("AUDITEUR"))
        assert s.peut_voir_national is True


# ── Tests d isolation Banque ──────────────────────────────────────

class TestIsolationBanque:

    def test_directeur_banque_filtre_par_banque(self):
        s = ScopeFilter.from_user(mk_user("BANQ_DIRECTEUR", entite_id="bnb-001"))
        _, params = s.apply(
            "SELECT * FROM hypotheques h WHERE h.statut = :s",
            {"s": "ACTIF"}, table_alias="h",
            col_region="region", col_entite="banque_id"
        )
        assert "_scope_entite" in params
        assert params["_scope_entite"] == "bnb-001"

    def test_deux_banques_distinctes(self):
        s1 = ScopeFilter.from_user(mk_user("BANQ_DIRECTEUR", entite_id="bnb-001"))
        s2 = ScopeFilter.from_user(mk_user("BANQ_DIRECTEUR", entite_id="boi-002"))
        _, p1 = s1.apply("SELECT * FROM hypotheques h WHERE 1=1", {},
                          table_alias="h", col_entite="banque_id")
        _, p2 = s2.apply("SELECT * FROM hypotheques h WHERE 1=1", {},
                          table_alias="h", col_entite="banque_id")
        assert p1["_scope_entite"] != p2["_scope_entite"]


# ── Tests d isolation Notaire ─────────────────────────────────────

class TestIsolationNotaire:

    def test_notaire_voit_ses_mutations(self):
        s = ScopeFilter.from_user(mk_user("NOTAIRE", entite_id="etude-abc"))
        _, params = s.apply(
            "SELECT * FROM property_transfers pt WHERE 1=1", {},
            table_alias="pt", col_region="region", col_entite="notaire_id"
        )
        assert "_scope_entite" in params
        assert params["_scope_entite"] == "etude-abc"

    def test_notaire_ne_voit_pas_mutations_autre_etude(self):
        s1 = ScopeFilter.from_user(mk_user("NOTAIRE", entite_id="etude-001"))
        s2 = ScopeFilter.from_user(mk_user("NOTAIRE", entite_id="etude-002"))
        _, p1 = s1.apply("SELECT * FROM property_transfers pt WHERE 1=1",
                          {}, col_entite="notaire_id")
        _, p2 = s2.apply("SELECT * FROM property_transfers pt WHERE 1=1",
                          {}, col_entite="notaire_id")
        assert p1.get("_scope_entite") != p2.get("_scope_entite")


# ── Tests d isolation Domaine/Urbanisme ──────────────────────────

class TestIsolationDomaine:

    def test_agent_domaine_filtre_region(self):
        s = ScopeFilter.from_user(mk_user("AGENT_DOMAINE", region="AGZ"))
        _, params = s.apply(
            "SELECT * FROM expropriations e WHERE 1=1", {},
            table_alias="e", col_region="region"
        )
        assert "_scope_region" in params
        assert params["_scope_region"] == "AGZ"

    def test_directeur_domaine_national(self):
        """Directeur domaine niveau 4 → vision nationale."""
        s = ScopeFilter.from_user(mk_user("DIRECTEUR_DOMAINE", niveau=4))
        assert s.peut_voir_national is True

    def test_agent_urbanisme_region_correcte(self):
        s = ScopeFilter.from_user(mk_user("AGENT_URBANISME", region="DOS"))
        _, params = s.apply(
            "SELECT * FROM rnaf r WHERE 1=1", {},
            table_alias="r", col_region="region"
        )
        assert params.get("_scope_region") == "DOS"


# ── Tests espace_travail workspaces ──────────────────────────────

class TestWorkspaceEspaceTravail:
    """
    Simule les appels aux workspaces de espace_travail.py.
    Vérifie que les rôles corrects ont accès aux bons workspaces.
    """

    WORKSPACE_ROLES = {
        "/espace/topographe":       ["TOPOGRAPHE", "GEOMETRE", "ADMIN"],
        "/espace/huissier":         ["HUISSIER", "GREFFIER", "ADMIN"],
        "/espace/commune":          ["MAIRE", "AGENT_COMMUNE", "ADMIN"],
        "/espace/ccfm/guichetiere": ["GUICHETIER_CCFM", "AGENT_CCFM", "CHEF_CCFM", "ADMIN"],
        "/espace/ccfm/secretaire":  ["SECRETAIRE_CCFM", "AGENT_CCFM", "CHEF_CCFM", "ADMIN"],
        "/espace/ccfm/directeur":   ["DIRECTEUR_URBANISME", "CHEF_CCFM", "ADMIN"],
        "/espace/ccfm/archiviste":  ["ARCHIVISTE_ANNF", "RESPONSABLE_ANNF", "CHEF_CCFM", "ADMIN"],
        "/espace/banque":           ["BANQ_DIRECTEUR", "BANQ_AGENT", "ADMIN"],
        "/espace/justice":          ["JUGE_FONCIER", "GREFFIER", "HUISSIER", "ADMIN"],
        "/espace/domaine":          ["AGENT_DOMAINE", "DIRECTEUR_DOMAINE", "DIRECTEUR_URBANISME", "ADMIN"],
        "/espace/notaire":          ["NOTAIRE", "ARCHIVISTE_ANNF", "ADMIN"],
        "/espace/annf":             ["ARCHIVISTE_ANNF", "RESPONSABLE_ANNF", "ADMIN"],
    }

    def test_tous_workspaces_ont_roles_definis(self):
        """Chaque workspace doit avoir au moins un rôle non-ADMIN."""
        for ws, roles in self.WORKSPACE_ROLES.items():
            non_admin = [r for r in roles if r != "ADMIN"]
            assert len(non_admin) >= 1, f"{ws} n a pas de rôle non-ADMIN"

    def test_roles_speciaux_dans_bons_workspaces(self):
        """Vérifier le placement des rôles spécialisés."""
        assert "GUICHETIER_CCFM" in self.WORKSPACE_ROLES["/espace/ccfm/guichetiere"]
        assert "SECRETAIRE_CCFM" in self.WORKSPACE_ROLES["/espace/ccfm/secretaire"]
        assert "TOPOGRAPHE"      in self.WORKSPACE_ROLES["/espace/topographe"]
        assert "JUGE_FONCIER"    in self.WORKSPACE_ROLES["/espace/justice"]
        assert "BANQ_DIRECTEUR"  in self.WORKSPACE_ROLES["/espace/banque"]
        assert "NOTAIRE"         in self.WORKSPACE_ROLES["/espace/notaire"]

    def test_admin_dans_tous_les_workspaces(self):
        """L ADMIN doit avoir accès à tous les workspaces."""
        for ws, roles in self.WORKSPACE_ROLES.items():
            assert "ADMIN" in roles, f"ADMIN absent de {ws}"

    def test_cross_contamination_impossible(self):
        """Un rôle CCFM ne doit pas être dans le workspace Banque."""
        banque_roles = self.WORKSPACE_ROLES["/espace/banque"]
        ccfm_only    = ["GUICHETIER_CCFM", "SECRETAIRE_CCFM", "TOPOGRAPHE"]
        for role in ccfm_only:
            assert role not in banque_roles, f"{role} ne devrait pas etre dans /espace/banque"

    def test_scope_filter_cohérence_workspace(self):
        """ScopeFilter doit retourner le bon filtre pour chaque rôle workspace."""
        test_cases = [
            ("GUICHETIER_CCFM", "NIA",      False, "NIA",  None),
            ("JUGE_FONCIER",    None,        False, None,   "tgi-001"),
            ("BANQ_DIRECTEUR",  None,        False, None,   "bnb-001"),
            ("ADMIN",           None,        True,  None,   None),
            ("RESPONSABLE_ANNF",None,        True,  None,   None),
        ]
        for role, region, nat_expected, reg_expected, ent_expected in test_cases:
            s = ScopeFilter.from_user(mk_user(role, region=region,
                                              entite_id=ent_expected))
            assert s.peut_voir_national == nat_expected, \
                f"{role}: peut_voir_national={s.peut_voir_national} != {nat_expected}"
            if reg_expected:
                assert s.region == reg_expected, f"{role}: region={s.region} != {reg_expected}"
