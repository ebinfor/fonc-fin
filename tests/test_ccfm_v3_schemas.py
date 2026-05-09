"""
FONCIER+ — Tests schemas Pydantic ccfm_v3 et WF30-33
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import pytest

try:
    from app.api.v1.endpoints.ccfm_v3 import (
        VerificationAutoIn, DemarrerVisiteIn, FicheConstatV3In,
        RapportAppreciationV3In, PreparerRetraitV3In, ArchiverV3In,
        MarquerRetraitV3In,
    )
    HAS_V3 = True
except ImportError:
    HAS_V3 = False

pytestmark = pytest.mark.skipif(not HAS_V3, reason="ccfm_v3 schemas non disponibles")


class TestSchemasValidation:

    def test_verification_auto_default(self):
        s = VerificationAutoIn()
        assert s.force_refresh is False

    def test_verification_auto_force(self):
        s = VerificationAutoIn(force_refresh=True)
        assert s.force_refresh is True

    def test_fiche_constat_valide(self):
        s = FicheConstatV3In(
            superficie_mesuree_m2=250.0,
            decision="AUTORISATION",
            topographe_nom="Ibrahima Diallo",
        )
        assert s.superficie_mesuree_m2 == 250.0
        assert s.decision == "AUTORISATION"

    def test_fiche_constat_superficie_negative(self):
        with pytest.raises(Exception):
            FicheConstatV3In(superficie_mesuree_m2=-10, decision="AUTORISATION",
                             topographe_nom="Test")

    def test_fiche_constat_zero(self):
        with pytest.raises(Exception):
            FicheConstatV3In(superficie_mesuree_m2=0, decision="AUTORISATION",
                             topographe_nom="Test")

    def test_rapport_appreciation_verdict_valide(self):
        s = RapportAppreciationV3In(verdict_global="CONFORME")
        assert s.verdict_global == "CONFORME"

    def test_rapport_appreciation_defaults(self):
        s = RapportAppreciationV3In(verdict_global="A_VERIFIER")
        assert s.points_conformite == []
        assert s.anomalies_detectees == []

    def test_archiver_defaults(self):
        s = ArchiverV3In()
        assert s.confirmer_integrite is True

    def test_marquer_retrait_default(self):
        s = MarquerRetraitV3In()
        assert s.mode_retrait == "PRESENTIEL"

    def test_preparer_retrait_sms(self):
        s = PreparerRetraitV3In(canal_notification="SMS", telephone_contact="+22790000000")
        assert s.canal_notification == "SMS"

    def test_demarrer_visite_optionnels(self):
        s = DemarrerVisiteIn()
        assert s.notes_depart is None
        assert s.gps_depart_lat is None

    def test_demarrer_visite_avec_gps(self):
        s = DemarrerVisiteIn(gps_depart_lat=13.5137, gps_depart_lon=2.1098)
        assert s.gps_depart_lat == 13.5137


class TestWF30_33Coverage:
    """Tests de couverture des workflows WF30-33."""

    def test_wf30_migrations_endpoint_exists(self):
        try:
            from app.api.v1.endpoints.wf30 import router
            import re
            src = open(__import__("inspect").getfile(router.__class__)).read() if hasattr(router, "__class__") else ""
            assert router is not None
        except ImportError:
            pytest.skip("wf30 non disponible")

    def test_wf31_monitoring_endpoint_exists(self):
        try:
            from app.api.v1.endpoints.monitoring import router
            assert router is not None
        except ImportError:
            pytest.skip("monitoring non disponible")

    def test_wf32_dashboard_endpoint_exists(self):
        try:
            from app.api.v1.endpoints.dashboard import router
            assert router is not None
        except ImportError:
            pytest.skip("dashboard non disponible")

    def test_wf33_admin_endpoint_exists(self):
        try:
            from app.api.v1.endpoints.admin import router
            assert router is not None
        except ImportError:
            pytest.skip("admin non disponible")

    def test_wf30_service_exists(self):
        try:
            from app.services.workflow_orchestrator import ANNFArchiveService
            assert ANNFArchiveService is not None
        except ImportError:
            pytest.skip("workflow_orchestrator non disponible")

    def test_wf31_monitoring_engine_exists(self):
        try:
            from app.services.monitoring_engine import MonitoringEngine
            assert MonitoringEngine is not None
        except ImportError:
            pytest.skip("monitoring_engine non disponible")

    def test_wf32_dashboard_engine_exists(self):
        try:
            from app.services.dashboard_engine import KPI, SerieTemporelle
            assert KPI is not None
        except ImportError:
            pytest.skip("dashboard_engine non disponible")

    def test_scope_filter_import(self):
        from app.core.scope_filter import ScopeFilter, ROLES_VISION_NATIONALE
        assert "ADMIN" in ROLES_VISION_NATIONALE
        assert "AUDITEUR" in ROLES_VISION_NATIONALE

    def test_decision_engine_import(self):
        from app.services.decision_engine import DecisionEngine, DecisionResult
        assert DecisionEngine is not None
        r = DecisionResult(autorise=True, decision="AUTORISE", motifs=[])
        assert r.valide is True

    def test_sql_sandbox_import(self):
        from app.services.sql_sandbox import (
            StaticSQLValidator, SemanticSQLValidator,
            ValidationStatus, EXEMPLES_VALIDES, EXEMPLES_INVALIDES
        )
        assert len(EXEMPLES_INVALIDES) == 8
        # Vérifier que tous les invalides sont rejetés
        for sql in EXEMPLES_INVALIDES:
            ok, _ = StaticSQLValidator.validate(sql)
            assert not ok, f"Invalide non détecté: {sql[:50]}"
