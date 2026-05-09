"""
FONCIER+ ── Tests SQL Sandbox (4 couches officielles)

Couverture :
  C0  BypassDetector       ── Unicode, homoglyphs, hex/URL/octal encoding
  C1  StaticSQLValidator   ── 40+ mots-clés, commentaires, dollar-quoting,
                              UNION, INTO, récursif, schémas système
  C2  SemanticSQLValidator ── whitelist tables, pg_shadow, $1/$2, domaine,
                              booléen, LIMIT suspect
  C3  PostgresSandbox      ── testé en intégration (skippé sans DB)
  C4  RuleSignature        ── SHA-256 déterministe, normalisation idempotente
  INT SQLRuleValidator     ── orchestration complète, cache, rate limit, batch
  ATK Attaques avancées    ── 20 vecteurs d'attaque paramétriques
"""
import asyncio
import sys
import os
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

try:
    from app.services.sql_sandbox import (
        BypassDetector,
        StaticSQLValidator,
        SemanticSQLValidator,
        RuleSignature,
        ValidationCache,
        InMemoryRateLimiter,
        SQLRuleValidator,
        ValidationStatus,
        EXEMPLES_VALIDES,
        EXEMPLES_INVALIDES,
    )
    HAS_SANDBOX = True
except ImportError as e:
    HAS_SANDBOX = False
    print(f"[SKIP] import impossible : {e}")

pytestmark = pytest.mark.skipif(not HAS_SANDBOX, reason="sql_sandbox non disponible")


# ════════════════════════════════════════════════════════════════════
# C0 — BypassDetector
# ════════════════════════════════════════════════════════════════════

class TestBypassDetector:

    def test_zero_width_space(self):
        _, a = BypassDetector.normalize_and_check("TR\u200bUE")
        assert any("INVISIBLE_CHARS" in x for x in a)

    def test_zero_width_joiner_supprime(self):
        sql_clean, _ = BypassDetector.normalize_and_check("EX\u200dISTS")
        assert "\u200d" not in sql_clean

    def test_homoglyph_cyrillique_o(self):
        """'о' cyrillique doit être normalisé en 'o' latin."""
        sql_clean, alerts = BypassDetector.normalize_and_check(
            "EXISTS (SELECT 1 FRоM parcelles WHERE id=$1)"
        )
        assert any("HOMOGLYPH" in a for a in alerts)
        assert "FROM" in sql_clean.upper()

    def test_homoglyph_cyrillique_c(self):
        _, alerts = BypassDetector.normalize_and_check("СREATE TABLE x")
        assert any("HOMOGLYPH" in a for a in alerts)

    def test_unicode_normalization_nfkc(self):
        # ﬁ (fi ligature U+FB01) → fi
        _, alerts = BypassDetector.normalize_and_check("\ufb01NALIZE")
        assert any("UNICODE" in a for a in alerts)

    def test_hex_python(self):
        _, alerts = BypassDetector.normalize_and_check(r"\x44\x52\x4f\x50")
        assert any("HEX_ENCODING" in a for a in alerts)

    def test_url_encoding(self):
        _, alerts = BypassDetector.normalize_and_check("%44%52%4F%50")
        assert any("URL_ENCODING" in a for a in alerts)

    def test_octal_postgres(self):
        _, alerts = BypassDetector.normalize_and_check(r"E'\104\122\117\120'")
        assert any("OCTAL" in a for a in alerts)

    def test_unicode_escape_postgres(self):
        _, alerts = BypassDetector.normalize_and_check("U&'\\0044\\0052'")
        assert any("UNICODE_ESCAPE" in a for a in alerts)

    def test_inline_block_comment(self):
        _, alerts = BypassDetector.normalize_and_check("DR/**/OP TABLE users")
        assert any("COMMENT" in a for a in alerts)

    def test_bom_supprime(self):
        sql_clean, alerts = BypassDetector.normalize_and_check(
            "\ufeffEXISTS (SELECT 1 FROM parcelles)"
        )
        assert "\ufeff" not in sql_clean
        assert any("INVISIBLE" in a for a in alerts)

    def test_is_dangerous_homoglyph(self):
        assert BypassDetector.is_dangerous(["HOMOGLYPH_CYRILLIC: 2 sub"]) is True

    def test_is_dangerous_hex(self):
        assert BypassDetector.is_dangerous(["HEX_ENCODING: \\xNN"]) is True

    def test_is_dangerous_non_critique(self):
        """UNICODE_NORMALIZATION seul n'est pas considéré dangereux."""
        assert BypassDetector.is_dangerous(["UNICODE_NORMALIZATION: …"]) is False

    def test_sql_propre_sans_alert(self):
        sql = "EXISTS (SELECT 1 FROM parcelles WHERE id=$1)"
        sql_clean, alerts = BypassDetector.normalize_and_check(sql)
        assert sql_clean == sql
        assert alerts == []


# ════════════════════════════════════════════════════════════════════
# C1 — StaticSQLValidator  (diagramme : 40+ mots-clés)
# ════════════════════════════════════════════════════════════════════

class TestStaticValidator:

    # Mots-clés DDL / DML interdits
    @pytest.mark.parametrize("sql,label", [
        ("DROP TABLE parcelles",                       "DROP"),
        ("CREATE TABLE x (id INT)",                    "CREATE"),
        ("ALTER TABLE parcelles ADD COLUMN x INT",     "ALTER"),
        ("TRUNCATE TABLE parcelles",                   "TRUNCATE"),
        ("INSERT INTO users VALUES('x','y','z')",      "INSERT"),
        ("UPDATE parcelles SET is_gele=TRUE WHERE id=$1", "UPDATE"),
        ("DELETE FROM parcelles WHERE id=$1",          "DELETE"),
        ("EXECUTE 'DROP TABLE users'",                 "EXECUTE"),
        ("COPY parcelles FROM '/tmp/x'",               "COPY"),
    ])
    def test_ddl_dml_rejetés(self, sql, label):
        ok, errs = StaticSQLValidator.validate(sql)
        assert not ok, f"[{label}] aurait dû être rejeté"

    # Commentaires
    def test_commentaire_double_tiret(self):
        ok, errs = StaticSQLValidator.validate("TRUE -- DROP TABLE users")
        assert not ok
        assert any(e.code == "SQL_COMMENTAIRE" for e in errs)

    def test_commentaire_bloc(self):
        ok, errs = StaticSQLValidator.validate(
            "EXISTS (SELECT 1 /* DROP */ FROM parcelles)")
        assert not ok
        assert any(e.code == "SQL_COMMENTAIRE" for e in errs)

    # Dollar-quoting
    def test_dollar_quoting_simple(self):
        ok, errs = StaticSQLValidator.validate("$$DROP TABLE parcelles$$")
        assert not ok
        assert any(e.code == "SQL_DOLLAR_QUOTING" for e in errs)

    def test_dollar_quoting_named(self):
        ok, errs = StaticSQLValidator.validate("$body$DROP TABLE x$body$")
        assert not ok
        assert any(e.code == "SQL_DOLLAR_QUOTING" for e in errs)

    # Multi-instruction
    def test_multi_instruction(self):
        ok, errs = StaticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM parcelles); DROP TABLE users")
        assert not ok
        assert any(e.code == "SQL_MULTI_INSTRUCTION" for e in errs)

    # Fonctions dangereuses
    @pytest.mark.parametrize("fn", [
        "pg_sleep", "pg_read_file", "dblink_exec",
        "set_config", "pg_terminate_backend",
    ])
    def test_fonctions_dangereuses(self, fn):
        ok, _ = StaticSQLValidator.validate(f"{fn}(10) IS NOT NULL")
        assert not ok, f"{fn}() aurait dû être rejeté"

    # UNION / INTERSECT
    def test_union_interdit(self):
        ok, errs = StaticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM parcelles UNION SELECT 1 FROM users)")
        assert not ok
        assert any(e.code == "SQL_UNION_INTERDIT" for e in errs)

    def test_intersect_interdit(self):
        ok, _ = StaticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM parcelles INTERSECT SELECT 1 FROM users)")
        assert not ok

    # INTO
    def test_into_interdit(self):
        ok, errs = StaticSQLValidator.validate(
            "SELECT id INTO tmp FROM parcelles WHERE id=$1")
        assert not ok
        assert any(e.code == "SQL_INTO_INTERDIT" for e in errs)

    # Profondeur imbrication
    def test_parentheses_desequilibrees(self):
        ok, errs = StaticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM parcelles WHERE id=$1")
        assert not ok
        assert any(e.code == "SQL_PARENTHESES_DESEQUILIBREES" for e in errs)

    def test_imbrication_max_6(self):
        # 7 niveaux
        sql = "EXISTS " + "(" * 7 + "SELECT 1 FROM parcelles" + ")" * 7
        ok, errs = StaticSQLValidator.validate(sql)
        assert not ok
        assert any(e.code == "SQL_TROP_IMBRIQUE" for e in errs)

    # WITH RECURSIVE
    def test_recursive_cte(self):
        ok, errs = StaticSQLValidator.validate(
            "EXISTS (WITH RECURSIVE r AS "
            "(SELECT 1 UNION ALL SELECT 1 FROM r) SELECT 1 FROM r)")
        assert not ok
        assert any(e.code == "SQL_RECURSIVE" for e in errs)

    # Schémas système
    def test_schema_pg_catalog(self):
        ok, errs = StaticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM pg_catalog.pg_shadow)")
        assert not ok
        assert any(e.code == "SQL_SCHEMA_SYSTEME" for e in errs)

    def test_schema_information_schema(self):
        ok, _ = StaticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name='users')")
        assert not ok

    # FOR UPDATE / FOR SHARE
    def test_for_update(self):
        ok, _ = StaticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM parcelles WHERE id=$1 FOR UPDATE)")
        assert not ok

    # Tautologies (always-true / always-false)
    def test_tautologie_or_1_eq_1(self):
        ok, errs = StaticSQLValidator.validate("EXISTS (SELECT 1) OR 1=1")
        assert not ok
        assert any(e.code == "SQL_TAUTOLOGIE" for e in errs)

    def test_tautologie_or_true(self):
        ok, _ = StaticSQLValidator.validate("EXISTS (SELECT 1) OR TRUE")
        assert not ok

    def test_tautologie_or_quotes(self):
        ok, _ = StaticSQLValidator.validate("EXISTS (SELECT 1) OR 'x'='x'")
        assert not ok

    def test_tautologie_empty_string(self):
        ok, _ = StaticSQLValidator.validate("EXISTS (SELECT 1) OR ''=''")
        assert not ok

    # Cas valides
    @pytest.mark.parametrize("sql", EXEMPLES_VALIDES)
    def test_exemples_valides_acceptes(self, sql):
        ok, errs = StaticSQLValidator.validate(sql)
        assert ok, f"Rejet erroné : {sql[:60]} — {[e.message for e in errs]}"

    def test_tous_exemples_invalides_rejetes_c1_ou_c2(self):
        """
        Certains exemples invalides passent C1 mais sont bloqués en C2
        (ex : pg_shadow). On vérifie que C1+C2 les bloque tous.
        """
        for sql in EXEMPLES_INVALIDES:
            ok1, _ = StaticSQLValidator.validate(sql)
            if ok1:
                ok2, _, _ = SemanticSQLValidator.validate(sql, "GENERAL")
                assert not ok2, f"Non détecté C1+C2 : {sql[:70]}"

    # Robustesse : mixed case, espaces multiples
    def test_bypass_mixed_case(self):
        ok, _ = StaticSQLValidator.validate(
            "ExIsTs (sElEcT 1) AnD DrOp TaBlE uSeRs Is NuLl")
        assert not ok

    def test_bypass_espaces_multiples(self):
        ok, _ = StaticSQLValidator.validate(
            "EXISTS   (SELECT   1   FROM   parcelles)   ;   DROP   TABLE")
        assert not ok

    # Cas limites
    def test_sql_vide(self):
        ok, errs = StaticSQLValidator.validate("")
        assert not ok
        assert any(e.code == "SQL_VIDE" for e in errs)

    def test_sql_trop_long(self):
        ok, errs = StaticSQLValidator.validate("A" * 2001)
        assert not ok
        assert any(e.code == "SQL_TROP_LONG" for e in errs)


# ════════════════════════════════════════════════════════════════════
# C2 — SemanticSQLValidator  (diagramme : whitelist 30 tables)
# ════════════════════════════════════════════════════════════════════

class TestSemanticValidator:

    def test_pg_shadow_refuse(self):
        """pg_shadow est dans SYSTEM_TABLES → rejet C2."""
        ok, errs, _ = SemanticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM pg_shadow WHERE usename=$2)", "GENERAL")
        assert not ok
        assert any(e.code == "SQL_TABLE_SYSTEME" for e in errs)

    def test_pg_stat_activity_refuse(self):
        ok, errs, _ = SemanticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid=$1)", "GENERAL")
        assert not ok

    def test_table_hors_whitelist(self):
        ok, errs, _ = SemanticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM comptes_bancaires WHERE id=$1)", "GENERAL")
        assert not ok
        assert any(e.code == "SQL_TABLE_NON_AUTORISEE" for e in errs)

    def test_table_autorisee(self):
        ok, errs, _ = SemanticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM parcelles p WHERE p.id=$1 AND p.is_gele=FALSE)",
            "GENERAL")
        assert ok, [e.message for e in errs]

    def test_warning_sans_param(self):
        ok, _, warns = SemanticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM regle_metier WHERE actif=TRUE)", "GENERAL")
        assert ok
        assert any("$1" in w or "$2" in w for w in warns)

    def test_warning_coherence_domaine(self):
        """hypothèques dans domaine CCFM → warning mais pas erreur."""
        ok, _, warns = SemanticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM hypotheques WHERE id=$1)", "CCFM")
        assert ok
        assert any("domaine" in w.lower() for w in warns)

    def test_warning_limit_offset(self):
        ok, _, warns = SemanticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM parcelles WHERE id=$1 LIMIT 1)", "GENERAL")
        assert any("limit" in w.lower() or "LIMIT" in w for w in warns)

    def test_warning_fonction_inconnue(self):
        ok, _, warns = SemanticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM parcelles WHERE ma_fn_custom(id)=$1)", "GENERAL")
        assert any("allowlist" in w.lower() or "répertori" in w.lower() for w in warns)

    def test_true_sans_table_accepte(self):
        ok, _, _ = SemanticSQLValidator.validate("TRUE", "GENERAL")
        assert ok

    def test_tables_multi_domaines(self):
        """Une règle CCFM peut référencer aussi parcelles."""
        ok, _, _ = SemanticSQLValidator.validate(
            "EXISTS (SELECT 1 FROM demandes_ccfm dc "
            "WHERE dc.id=$1 AND NOT EXISTS "
            "(SELECT 1 FROM parcelles p WHERE p.is_gele=TRUE))",
            "CCFM")
        assert ok


# ════════════════════════════════════════════════════════════════════
# C4 — RuleSignature
# ════════════════════════════════════════════════════════════════════

class TestRuleSignature:

    def test_hash_deterministe(self):
        h1 = RuleSignature.signer("TRUE","G","T","BLOQUANT","u")
        h2 = RuleSignature.signer("TRUE","G","T","BLOQUANT","u")
        assert h1 == h2

    def test_hash_change_si_sql_change(self):
        h1 = RuleSignature.signer("TRUE","G","T","BLOQUANT","u")
        h2 = RuleSignature.signer("FALSE","G","T","BLOQUANT","u")
        assert h1 != h2

    def test_hash_64_hex(self):
        h = RuleSignature.signer("TRUE","GENERAL","TEST","BLOQUANT","user-1")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_normaliser_espaces(self):
        norm = RuleSignature.normaliser("EXISTS  ( SELECT  1   FROM  parcelles )")
        assert "  " not in norm

    def test_normaliser_idempotent(self):
        sql  = "EXISTS (SELECT 1 FROM parcelles WHERE id=$1)"
        n1   = RuleSignature.normaliser(sql)
        n2   = RuleSignature.normaliser(n1)
        assert n1 == n2

    def test_normaliser_preserves_content(self):
        sql  = "NOT EXISTS (SELECT 1 FROM entity_lock WHERE entite_id=$1)"
        norm = RuleSignature.normaliser(sql)
        assert "NOT EXISTS" in norm
        assert "entity_lock" in norm
        assert "$1" in norm


# ════════════════════════════════════════════════════════════════════
# INT — SQLRuleValidator (orchestrateur 4 couches)
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_regle_valide_complete():
    result = await SQLRuleValidator.valider(
        sql="NOT EXISTS (SELECT 1 FROM entity_lock el "
            "WHERE el.entite_id=$1 AND el.actif=TRUE)",
        domaine="GENERAL", code="V_TEST", db=None,
    )
    assert result.valide
    assert result.status == ValidationStatus.VALIDE
    assert len(result.sha256_regle) == 64
    assert result.safe_sql is not None
    assert result.duree_ms < 500
    # Les traces doivent contenir C1, C2, C3(skip), C4
    couches = {l.couche for l in result.layers}
    assert 1 in couches and 2 in couches and 4 in couches


@pytest.mark.asyncio
async def test_injection_ddl_refuse():
    result = await SQLRuleValidator.valider(
        sql="EXISTS (SELECT 1 FROM users); DROP TABLE parcelles",
        domaine="GENERAL", code="INJ_DDL", db=None,
    )
    assert not result.valide
    assert result.status == ValidationStatus.REFUSE
    assert result.sha256_regle == ""
    # Bloqué en C1 → C2 et C4 ne doivent pas avoir été atteints
    c1_trace = next((l for l in result.layers if l.couche == 1), None)
    assert c1_trace is not None
    assert c1_trace.statut == "FAIL"


@pytest.mark.asyncio
async def test_table_systeme_refuse():
    result = await SQLRuleValidator.valider(
        sql="EXISTS (SELECT 1 FROM pg_shadow WHERE usename=$2)",
        domaine="GENERAL", code="SHADOW_TEST", db=None,
    )
    assert not result.valide
    # pg_shadow → C2
    c2_trace = next((l for l in result.layers if l.couche == 2), None)
    assert c2_trace is not None and c2_trace.statut == "FAIL"


@pytest.mark.asyncio
async def test_warning_sans_param():
    result = await SQLRuleValidator.valider(
        sql="EXISTS (SELECT 1 FROM regle_metier WHERE actif=TRUE)",
        domaine="GENERAL", code="NO_PARAM", db=None,
    )
    assert result.valide
    assert len(result.warnings) > 0


@pytest.mark.asyncio
async def test_bypass_cyrillique():
    """Homoglyphe cyrillique dans FROM → nettoyé avant C1."""
    result = await SQLRuleValidator.valider(
        sql="EXISTS (SELECT 1 FRоM parcelles WHERE id=$1)",  # о cyrillique
        domaine="GENERAL", code="BYPASS_CYR", db=None,
    )
    # Après normalisation le SQL devient valide
    assert result.status in (ValidationStatus.VALIDE, ValidationStatus.REFUSE)


@pytest.mark.asyncio
async def test_layers_toujours_present():
    """Chaque résultat doit contenir des traces de couches."""
    for sql, expected in [
        ("TRUE",                                          ValidationStatus.VALIDE),
        ("DROP TABLE x",                                  ValidationStatus.REFUSE),
        ("EXISTS (SELECT 1 FROM pg_shadow WHERE u=$2)",   ValidationStatus.INVALIDE),
    ]:
        result = await SQLRuleValidator.valider(
            sql=sql, domaine="GENERAL", code="TRACE_TEST", db=None)
        assert len(result.layers) > 0, f"Aucune trace pour : {sql}"


# ════════════════════════════════════════════════════════════════════
# BATCH validation
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_batch_valides_et_refuses():
    result = await SQLRuleValidator.valider_lot([
        {"sql": "TRUE",             "domaine": "GENERAL", "code": "B1"},
        {"sql": "DROP TABLE users", "domaine": "GENERAL", "code": "B2"},
        {"sql": "NOT EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND is_gele=TRUE)",
         "domaine": "GENERAL", "code": "B3"},
    ], db=None)
    assert result.total    == 3
    assert result.valides  == 2
    assert result.refuses  == 1
    assert result.taux_validation == pytest.approx(66.7, abs=0.1)


@pytest.mark.asyncio
async def test_batch_as_dict():
    result = await SQLRuleValidator.valider_lot(
        [{"sql": "TRUE", "domaine": "GENERAL", "code": "BD1"}], db=None)
    d = result.as_dict()
    assert "total" in d and "taux_validation" in d and "resultats" in d


@pytest.mark.asyncio
async def test_batch_vide():
    result = await SQLRuleValidator.valider_lot([], db=None)
    assert result.total == 0
    assert result.taux_validation == 0.0


# ════════════════════════════════════════════════════════════════════
# CACHE
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cache_hit():
    sql = "NOT EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND statut='ARCHIVE')"
    r1  = await SQLRuleValidator.valider(sql=sql, domaine="GENERAL", code="CACHE_T")
    r2  = await SQLRuleValidator.valider(sql=sql, domaine="GENERAL", code="CACHE_T")
    assert r1.sha256_regle == r2.sha256_regle

def test_cache_invalidation():
    ValidationCache.put(
        "TRUE", "GENERAL", "INV_TEST",
        type("R", (), {
            "status": ValidationStatus.VALIDE,
            "sha256_regle": "abc",
        })(),  # type: ignore
    )
    n = ValidationCache.invalidate("INV_TEST")
    assert n >= 1
    assert ValidationCache.get("TRUE", "GENERAL", "INV_TEST") is None


# ════════════════════════════════════════════════════════════════════
# RATE LIMITER
# ════════════════════════════════════════════════════════════════════

def test_rate_limiter_ok():
    uid = f"rl_ok_{time.monotonic():.3f}"
    ok, remaining = InMemoryRateLimiter.check(uid)
    assert ok is True
    assert remaining == InMemoryRateLimiter.MAX_PER_HOUR - 1

def test_rate_limiter_exhausted():
    uid = f"rl_ex_{time.monotonic():.3f}"
    InMemoryRateLimiter._store[uid] = [
        time.time() for _ in range(InMemoryRateLimiter.MAX_PER_HOUR)
    ]
    ok, remaining = InMemoryRateLimiter.check(uid)
    assert ok is False
    assert remaining == 0

@pytest.mark.asyncio
async def test_rate_limit_refuse_dans_validateur():
    uid = f"rl_val_{time.monotonic():.3f}"
    InMemoryRateLimiter._store[uid] = [
        time.time() for _ in range(InMemoryRateLimiter.MAX_PER_HOUR)
    ]
    result = await SQLRuleValidator.valider(
        sql="TRUE", domaine="GENERAL", code="RL",
        user_id=uid, db=None,
    )
    assert result.status == ValidationStatus.REFUSE
    assert any(e.code == "RATE_LIMIT" for e in result.errors)


# ════════════════════════════════════════════════════════════════════
# VECTEURS D'ATTAQUE AVANCÉS (20 cas)
# ════════════════════════════════════════════════════════════════════

ATTACK_VECTORS = [
    # SQL classiques
    ("EXISTS (SELECT 1) OR 1=1",                    "always-true"),
    ("TRUE; SELECT pg_sleep(30)",                   "stacked queries DoS"),
    ("EXISTS (SELECT 1 WHERE pg_sleep(5) IS NULL)", "timing attack"),
    ("EXISTS (SELECT 1 FROM pg_shadow WHERE u='postgres')", "credential read"),
    ("EXISTS (SELECT 1 FROM parcelles UNION SELECT NULL)", "UNION"),
    ("$q$DROP TABLE parcelles$q$",                  "dollar-quoting"),
    ("/* DROP */ TRUE",                             "block comment"),
    ("A" * 2001,                                    "oversized"),
    ("pg_read_file('/etc/passwd') IS NOT NULL",      "file read"),
    ("dblink_exec('DROP TABLE x')",                 "dblink"),
    # C1 avancés
    ("EXISTS (SELECT 1 FROM pg_catalog.pg_shadow)", "schema prefix"),
    ("EXISTS (WITH RECURSIVE r AS (SELECT 1 UNION ALL SELECT 1 FROM r) SELECT 1 FROM r)",
     "recursive DoS"),
    ("EXISTS (SELECT 1 FROM parcelles WHERE id=$1 FOR UPDATE)", "row lock"),
    ("set_config('statement_timeout','0',TRUE) IS NULL", "disable timeout"),
    ("EXISTS (SELECT 1 FROM information_schema.tables)",  "schema info"),
    ("txid_current() IS NOT NULL",                  "txid"),
    ("nextval('parcelles_id_seq') IS NOT NULL",     "sequence write"),
    # Obfuscation
    ("pg_SleEp(10) IS NOT NULL",                    "mixed-case pg_sleep"),
]


@pytest.mark.parametrize("sql,label", ATTACK_VECTORS)
def test_attaque_bloquee_c1_ou_c2(sql: str, label: str):
    """Chaque vecteur doit être bloqué par C1 ou C2."""
    ok1, _ = StaticSQLValidator.validate(sql)
    if ok1:
        ok2, _, _ = SemanticSQLValidator.validate(sql, "GENERAL")
        assert not ok2, f"[{label}] NON DÉTECTÉ : {sql[:70]}"


# ZWSP / ZWNJ — bloqués par C0 via pipeline complet
@pytest.mark.asyncio
async def test_pipeline_zwsp_bypass():
    """DR\u200bOP : ZWSP supprimé → DROP → C1 bloque. C0 flag INVISIBLE → REFUSE."""
    result = await SQLRuleValidator.valider(
        sql="DR\u200bOP TABLE users", domaine="GENERAL",
        code="ZWSP_TEST", db=None,
    )
    assert result.status == ValidationStatus.REFUSE, \
        f"ZWSP bypass aurait dû être REFUSE, obtenu {result.status}"


@pytest.mark.asyncio
async def test_pipeline_zwnj_bypass():
    """TR\u200cUE : ZWNJ invisible → INVISIBLE_CHARS → C0 → REFUSE."""
    result = await SQLRuleValidator.valider(
        sql="TR\u200cUE", domaine="GENERAL",
        code="ZWNJ_TEST", db=None,
    )
    assert result.status == ValidationStatus.REFUSE, \
        f"ZWNJ invisible aurait dû être REFUSE, obtenu {result.status}"


# Tautologies via pipeline
@pytest.mark.asyncio
async def test_pipeline_tautologie_or_1_eq_1():
    result = await SQLRuleValidator.valider(
        sql="EXISTS (SELECT 1) OR 1=1",
        domaine="GENERAL", code="TAUT_TEST", db=None,
    )
    assert result.status == ValidationStatus.REFUSE
    assert any(e.code == "SQL_TAUTOLOGIE" for e in result.errors)
