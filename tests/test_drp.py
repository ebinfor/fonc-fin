"""FONCIER+ — Tests DRP et infrastructure"""
import sys, os, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import pytest
from collections import defaultdict

MIG_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "alembic", "versions")
EP_DIR  = os.path.join(os.path.dirname(__file__), "..", "backend", "app", "api", "v1", "endpoints")


class TestDRP:

    def test_drp_tables_in_migrations(self):
        all_sql = "".join(
            open(os.path.join(MIG_DIR, f)).read()
            for f in os.listdir(MIG_DIR) if f.endswith(".py")
        )
        for t in ["drp_backup_log", "drp_replication_status", "drp_simulation"]:
            assert t in all_sql, f"Table DRP manquante : {t}"

    def test_health_endpoints_dans_main(self):
        main_src = open(os.path.join(
            os.path.dirname(__file__), "..", "backend", "app", "main.py"
        )).read()
        for ep in ["/health", "/health/ready", "/health/live"]:
            assert ep in main_src, f"Endpoint manquant : {ep}"

    def test_rollback_modules_critiques(self):
        for ep_name in ["notaire.py", "domaine.py", "justice.py", "banque.py", "ccfm_v3.py"]:
            src = open(os.path.join(EP_DIR, ep_name)).read()
            assert "rollback" in src, f"{ep_name} manque rollback"

    def test_alembic_chain_integre(self):
        revs, downs = {}, {}
        pat_rev  = re.compile(r'^revision\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
        pat_down = re.compile(r'^down_revision\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
        for f in os.listdir(MIG_DIR):
            if not f.endswith(".py"):
                continue
            src = open(os.path.join(MIG_DIR, f)).read()
            rv  = pat_rev.search(src)
            dv  = pat_down.search(src)
            if rv:
                revs[f]  = rv.group(1)
                if dv: downs[f] = dv.group(1)
        by_down = defaultdict(list)
        for fn, d in downs.items():
            by_down[d].append(fn)
        conflicts = {k: v for k, v in by_down.items() if len(v) > 1}
        assert not conflicts, f"Conflits Alembic : {conflicts}"

    def test_migration_041_exists(self):
        files = os.listdir(MIG_DIR)
        assert any("041" in f for f in files), "Migration 041 index composites manquante"

    def test_auth_rate_limit_applicatif(self):
        src = open(os.path.join(EP_DIR, "auth.py")).read()
        assert any(t in src for t in ["_LOGIN_MAX", "rate_limit", "_check_login"]), \
            "auth.py manque rate-limit applicatif"

    def test_scope_filter_complet(self):
        sf_path = os.path.join(
            os.path.dirname(__file__), "..", "backend", "app", "core", "scope_filter.py"
        )
        sf = open(sf_path).read()
        for attr in ["ROLES_VISION_NATIONALE", "ROLES_FILTRES_REGION",
                     "ROLES_FILTRES_ENTITE", "def apply", "def from_user"]:
            assert attr in sf, f"scope_filter.py manque : {attr}"

    def test_sql_sandbox_bloque_vecteurs(self):
        try:
            from app.services.sql_sandbox import StaticSQLValidator, EXEMPLES_INVALIDES
            for sql in EXEMPLES_INVALIDES:
                ok, _ = StaticSQLValidator.validate(sql)
                assert not ok, f"Vecteur non bloqué : {sql[:50]}"
        except ImportError:
            pytest.skip("sql_sandbox non disponible")

    def test_readme_version(self):
        readme = os.path.join(os.path.dirname(__file__), "..", "README.md")
        if os.path.exists(readme):
            src = open(readme).read()
            assert "v1.0.9" in src, "README.md ne mentionne pas v1.0.9"

    def test_foncier_plus_md_complet(self):
        md = os.path.join(os.path.dirname(__file__), "..", "docs", "foncier_plus.md")
        if os.path.exists(md):
            src = open(md).read()
            for section in ["CCFM", "ScopeFilter", "workflow", "Migrations", "SEO"]:
                assert section.lower() in src.lower(), f"foncier_plus.md manque section {section}"
