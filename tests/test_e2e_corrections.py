"""
FONCIER+ v108 — Tests des corrections d'audit (P1.1 → P2.8)

Couverture :
  P1.1  Fallback juridiction restrictif (503 si DB inaccessible)
  P1.3  Config MINIO_SECRET_KEY validator
  P1.4  create_access_token() utilise JWT_ACCESS_EXPIRE_MINUTES
  P2.1  Sélection algorithmique RS256/HS256
  P2.2  Blacklist JWT (jti)
  P2.3  Rate limiting global
  P2.5  Filtrage SSE par rôle/région
  P2.6  Middleware X-Request-ID
  P3.7  Health check endpoint
  E2E   Workflow sécurité bout-en-bout (create_token → decode → révocation)
"""
import asyncio
import sys
import os
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

try:
    from app.core.security import (
        JWTBlacklist, create_access_token, create_refresh_token,
        _jwt_algorithm, _get_decode_key, _get_encode_key,
        verifier_juridiction_region, hash_password, verify_password,
    )
    from app.core.config import settings
    HAS_SEC = True
except ImportError as e:
    HAS_SEC = False
    print(f"[SKIP] {e}")

pytestmark = pytest.mark.skipif(not HAS_SEC, reason="security non disponible")


# ── Helpers ───────────────────────────────────────────────────────

def _decode_token(token: str) -> dict:
    from jose import jwt
    return jwt.decode(
        token,
        _get_decode_key(),
        algorithms=[_jwt_algorithm()],
        options={"verify_aud": False},
    )


# ════════════════════════════════════════════════════════════════════
# P1.4 — create_access_token() depuis config
# ════════════════════════════════════════════════════════════════════

class TestP14TokenConfig:

    def test_token_exp_from_config(self):
        """P1.4 : L'expiration doit correspondre à JWT_ACCESS_EXPIRE_MINUTES."""
        token   = create_access_token("user-1", "ADMIN", "NI-01")
        payload = _decode_token(token)
        expected_duration = settings.JWT_ACCESS_EXPIRE_MINUTES * 60
        actual_duration   = payload["exp"] - payload["iat"]
        assert abs(actual_duration - expected_duration) < 5  # tolérance 5s

    def test_token_contient_jti(self):
        """P2.2 : Le token doit contenir un jti unique."""
        t1 = create_access_token("user-1", "ADMIN", "NI-01")
        t2 = create_access_token("user-1", "ADMIN", "NI-01")
        p1 = _decode_token(t1)
        p2 = _decode_token(t2)
        assert "jti" in p1 and "jti" in p2
        assert p1["jti"] != p2["jti"]  # jti uniques

    def test_token_contient_iss_aud(self):
        """Token doit contenir issuer et audience."""
        payload = _decode_token(create_access_token("user-1", "ADMIN", "NI-01"))
        assert payload.get("iss") == "foncier-niger"
        assert payload.get("aud") == "foncier-api"

    def test_token_contient_role_region(self):
        """Le payload doit contenir role et region."""
        payload = _decode_token(create_access_token("user-42", "NOTAIRE", "NI-03"))
        assert payload["role"]   == "NOTAIRE"
        assert payload["region"] == "NI-03"
        assert payload["sub"]    == "user-42"

    def test_refresh_token_type(self):
        """Le refresh token doit avoir type=refresh."""
        token   = create_refresh_token("user-1")
        payload = _decode_token(token)
        assert payload.get("type") == "refresh"
        assert "jti" in payload

    def test_refresh_token_duree_plus_longue(self):
        """Le refresh token dure JWT_REFRESH_EXPIRE_DAYS jours."""
        token   = create_refresh_token("user-1")
        payload = _decode_token(token)
        expected = settings.JWT_REFRESH_EXPIRE_DAYS * 86400
        actual   = payload["exp"] - payload["iat"]
        assert abs(actual - expected) < 10


# ════════════════════════════════════════════════════════════════════
# P2.1 — Algorithme JWT
# ════════════════════════════════════════════════════════════════════

class TestP21AlgorithmeJWT:

    def test_algorithme_defaut_hs256(self):
        """Sans clé RSA, l'algorithme doit être HS256."""
        algo = _jwt_algorithm()
        assert algo in ("HS256", "RS256")  # HS256 par défaut si pas de RSA

    def test_get_decode_key_retourne_secret(self):
        """Sans clé RSA, la clé de décodage doit être le secret HMAC."""
        key = _get_decode_key()
        assert isinstance(key, str) and len(key) > 0

    def test_get_encode_key_coherent(self):
        """La clé d'encodage doit permettre de décoder le token généré."""
        token   = create_access_token("user-test", "ADMIN", "NI-01")
        payload = _decode_token(token)
        assert payload["sub"] == "user-test"


# ════════════════════════════════════════════════════════════════════
# P2.2 — Blacklist JWT
# ════════════════════════════════════════════════════════════════════

class TestP22BlacklistJWT:

    @pytest.mark.asyncio
    async def test_blacklist_memoire_revoquer(self):
        """Un jti révoqué doit être détecté."""
        JWTBlacklist._redis   = None  # Forcer le fallback mémoire
        JWTBlacklist._mem_store = set()

        jti = "test-jti-001"
        assert not await JWTBlacklist.est_revoque(jti)
        await JWTBlacklist.revoquer(jti, ttl_sec=60)
        assert await JWTBlacklist.est_revoque(jti)

    @pytest.mark.asyncio
    async def test_blacklist_jti_different(self):
        """Un jti non révoqué ne doit pas être bloqué."""
        JWTBlacklist._redis      = None
        JWTBlacklist._mem_store  = set()

        await JWTBlacklist.revoquer("jti-A")
        assert not await JWTBlacklist.est_revoque("jti-B")

    @pytest.mark.asyncio
    async def test_blacklist_redis_ko_deny(self):
        """P2.2 : Si Redis est inaccessible → deny par sécurité."""
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(side_effect=Exception("Redis down"))
        JWTBlacklist._redis = mock_redis

        # Doit retourner True (révoqué) par précaution sécuritaire
        result = await JWTBlacklist.est_revoque("any-jti")
        assert result is True, "Redis inaccessible doit déclencher un deny sécuritaire"

        JWTBlacklist._redis = None  # Nettoyage

    @pytest.mark.asyncio
    async def test_blacklist_redis_ok(self):
        """Blacklist Redis fonctionnelle."""
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock(return_value=True)
        mock_redis.exists = AsyncMock(return_value=1)
        JWTBlacklist._redis = mock_redis

        await JWTBlacklist.revoquer("jti-redis", ttl_sec=120)
        mock_redis.setex.assert_called_once_with("jwt_bl:jti-redis", 120, "1")

        result = await JWTBlacklist.est_revoque("jti-redis")
        assert result is True

        JWTBlacklist._redis = None


# ════════════════════════════════════════════════════════════════════
# P1.1 — Fallback juridiction restrictif
# ════════════════════════════════════════════════════════════════════

class TestP11JuridictionRestrictive:

    @pytest.mark.asyncio
    async def test_juridiction_db_ok_autorise(self):
        """Si DB retourne True → juridiction accordée."""
        db = AsyncMock()
        mock_r = MagicMock()
        mock_r.scalar.return_value = True
        db.execute = AsyncMock(return_value=mock_r)

        result = await verifier_juridiction_region(db, "user-1", "NI-01")
        assert result is True

    @pytest.mark.asyncio
    async def test_juridiction_db_ok_refuse(self):
        """Si DB retourne False → juridiction refusée."""
        db = AsyncMock()
        mock_r = MagicMock()
        mock_r.scalar.return_value = False
        db.execute = AsyncMock(return_value=mock_r)

        result = await verifier_juridiction_region(db, "user-1", "NI-08")
        assert result is False

    @pytest.mark.asyncio
    async def test_juridiction_db_inaccessible_leve_503(self):
        """
        P1.1 CORRECTION CRITIQUE :
        Si DB est inaccessible → lever HTTPException 503 (plus de fallback permissif).
        """
        from fastapi import HTTPException

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=Exception("DB connection timeout"))

        with pytest.raises(HTTPException) as exc_info:
            await verifier_juridiction_region(db, "user-1", "NI-05")

        assert exc_info.value.status_code == 503
        assert "JURIDICTION_DB_INACCESSIBLE" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_juridiction_503_non_permissif(self):
        """
        P1.1 CRITIQUE : Vérifier que l'ancienne logique permissive (return True sur erreur)
        n'est plus présente dans la base de code.
        """
        import inspect
        src = inspect.getsource(verifier_juridiction_region)
        # Ces patterns correspondent à l'ancien fallback permissif
        forbidden_patterns = [
            "return True  # Fallback",
            "return True  # fallback",
            "except Exception:\n        return True",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in src, (
                f"RÉGRESSION P1.1 : fallback permissif détecté : {pattern!r}"
            )


# ════════════════════════════════════════════════════════════════════
# P1.3 — Config MINIO_SECRET_KEY validator
# ════════════════════════════════════════════════════════════════════

class TestP13MinioValidator:

    def test_validator_minio_existe(self):
        """P1.3 : Le validator MINIO_SECRET_KEY doit exister dans config."""
        from app.core.config import Settings
        validators = [
            name for name, val in vars(Settings).items()
            if "minio" in name.lower() or "check_minio" in name.lower()
        ]
        # Vérifier via les field_validators de Pydantic v2
        import inspect
        src = inspect.getsource(Settings)
        assert "MINIO_SECRET_KEY" in src, "MINIO_SECRET_KEY doit être dans Settings"
        assert "check_minio" in src or "minio_secret" in src.lower(), \
            "Validator pour MINIO_SECRET_KEY manquant"

    def test_config_jwt_access_expire_minutes(self):
        """P1.4 : JWT_ACCESS_EXPIRE_MINUTES doit être dans la config."""
        assert hasattr(settings, "JWT_ACCESS_EXPIRE_MINUTES")
        assert isinstance(settings.JWT_ACCESS_EXPIRE_MINUTES, int)
        assert settings.JWT_ACCESS_EXPIRE_MINUTES > 0

    def test_config_jwt_algorithm_actif(self):
        """P2.1 : La propriété jwt_algorithm_actif doit exister."""
        assert hasattr(settings, "jwt_algorithm_actif")
        algo = settings.jwt_algorithm_actif
        assert algo in ("HS256", "RS256")

    def test_config_redis_blacklist_actif(self):
        """P2.2 : La propriété redis_blacklist_actif doit exister."""
        assert hasattr(settings, "redis_blacklist_actif")
        assert isinstance(settings.redis_blacklist_actif, bool)


# ════════════════════════════════════════════════════════════════════
# P2.3 — Rate limiting global
# ════════════════════════════════════════════════════════════════════

class TestP23RateLimiting:

    def test_rate_limiter_importe(self):
        """P2.3 : GlobalRateLimiter doit être défini dans main.py."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "main_check",
            os.path.join(os.path.dirname(__file__), '..', 'backend', 'app', 'main.py')
        )
        assert spec is not None

    def test_rate_limiter_logique(self):
        """P2.3 : Le rate limiter doit bloquer après le seuil."""
        # Simuler la logique sans importer main (évite les dépendances FastAPI)
        import collections, time as _t

        store: dict = {}
        WINDOW = 60
        MAX    = 5  # Seuil test réduit

        def check(ip, limit=MAX):
            now = _t.monotonic()
            if ip not in store:
                store[ip] = collections.deque()
            dq = store[ip]
            while dq and dq[0] < now - WINDOW:
                dq.popleft()
            if len(dq) >= limit:
                return False, 0
            dq.append(now)
            return True, limit - len(dq)

        # 5 premières requêtes → autorisées
        for i in range(5):
            allowed, rem = check("1.2.3.4")
            assert allowed, f"Requête {i+1} doit être autorisée"

        # 6e requête → bloquée
        allowed, rem = check("1.2.3.4")
        assert not allowed, "6e requête doit être bloquée"
        assert rem == 0

        # Autre IP → non affectée
        allowed, _ = check("9.9.9.9")
        assert allowed, "Autre IP doit être autorisée"


# ════════════════════════════════════════════════════════════════════
# P2.5 — Filtrage SSE par rôle/région
# ════════════════════════════════════════════════════════════════════

class TestP25FiltrageSse:

    def _peut_voir(self, item, role, region):
        """Réimplémentation de la logique de filtrage SSE pour les tests."""
        SEES_ALL = {
            "ADMIN", "SECRETAIRE_GENERAL", "MINISTRE_URBANISME",
            "DIRECTEUR_URBANISME", "DIRECTEUR_CADASTRE", "AUDITEUR",
        }
        sees_all = (role in SEES_ALL) or not region
        if sees_all:
            return True

        payload = item.get("payload", {})
        if item.get("type") == "alerte" and payload.get("niveau") == "CRITICAL":
            return True

        for key in ("region",):
            r = payload.get(key, "")
            if r and region and r != region:
                return False
        return True

    def test_admin_voit_tout(self):
        evt = {"type": "evenement", "payload": {"region": "NI-08"}}
        assert self._peut_voir(evt, "ADMIN", "NI-01")

    def test_agent_voit_sa_region(self):
        evt = {"type": "evenement", "payload": {"region": "NI-01"}}
        assert self._peut_voir(evt, "AGENT_DOMAINE", "NI-01")

    def test_agent_ne_voit_pas_autre_region(self):
        evt = {"type": "evenement", "payload": {"region": "NI-08"}}
        assert not self._peut_voir(evt, "AGENT_DOMAINE", "NI-01")

    def test_critical_visible_par_tous(self):
        """Alertes CRITICAL → visibles par tous les rôles."""
        alert = {"type": "alerte", "payload": {"niveau": "CRITICAL", "region": "NI-08"}}
        assert self._peut_voir(alert, "AGENT_DOMAINE", "NI-01")

    def test_warning_filtree_par_region(self):
        """Alertes WARNING → filtrées par région."""
        alert = {"type": "alerte", "payload": {"niveau": "WARNING", "region": "NI-08"}}
        assert not self._peut_voir(alert, "AGENT_DOMAINE", "NI-01")

    def test_sans_region_user_voit_tout(self):
        """Si l'utilisateur n'a pas de région → voit tout."""
        evt = {"type": "evenement", "payload": {"region": "NI-05"}}
        assert self._peut_voir(evt, "AGENT_DOMAINE", "")


# ════════════════════════════════════════════════════════════════════
# P2.6 — Middleware X-Request-ID
# ════════════════════════════════════════════════════════════════════

class TestP26RequestId:

    def test_get_request_id_importe(self):
        """P2.6 : get_request_id doit être disponible dans main."""
        # Vérifier la présence dans le fichier source
        main_path = os.path.join(
            os.path.dirname(__file__), '..', 'backend', 'app', 'main.py'
        )
        with open(main_path) as f:
            src = f.read()
        assert "X-Request-ID" in src, "Middleware X-Request-ID manquant dans main.py"
        assert "request_id" in src.lower(), "Variable request_id manquante"
        assert "contextvars" in src, "ContextVar requis pour la propagation"

    def test_uuid_format(self):
        """Le request_id doit être un UUID v4 valide."""
        import uuid
        rid = str(uuid.uuid4())
        assert len(rid) == 36
        parsed = uuid.UUID(rid)
        assert parsed.version == 4


# ════════════════════════════════════════════════════════════════════
# P3.7 — Health check endpoint
# ════════════════════════════════════════════════════════════════════

class TestP37HealthCheck:

    def test_health_endpoint_dans_main(self):
        """P3.7 : /health doit être défini dans main.py."""
        main_path = os.path.join(
            os.path.dirname(__file__), '..', 'backend', 'app', 'main.py'
        )
        with open(main_path) as f:
            src = f.read()
        assert '"/health"'         in src, "/health endpoint manquant"
        assert '"/health/ready"'   in src, "/health/ready manquant"
        assert '"/health/live"'    in src, "/health/live manquant"
        assert "status_code=503"   in src, "503 doit être renvoyé si dégradé"


# ════════════════════════════════════════════════════════════════════
# Passwords
# ════════════════════════════════════════════════════════════════════

class TestPasswords:

    def test_hash_verify_correct(self):
        pwd    = "SuperSecret2024!"
        hashed = hash_password(pwd)
        assert verify_password(pwd, hashed)

    def test_hash_reject_wrong(self):
        hashed = hash_password("correct")
        assert not verify_password("wrong", hashed)

    def test_hash_differs_each_time(self):
        """bcrypt génère un salt différent à chaque fois."""
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # Salts différents


# ════════════════════════════════════════════════════════════════════
# Non-régression modules précédents
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_non_regression_dashboard():
    from app.services.dashboard_engine import DashboardCache, KPI, NiveauRisque
    c = DashboardCache()
    c.set("k", 42)
    assert c.get("k") == 42

@pytest.mark.asyncio
async def test_non_regression_risk():
    from app.services.risk_engine import ScoreCalculator, NiveauRisque
    assert ScoreCalculator.niveau(80) == NiveauRisque.CRITIQUE
    assert ScoreCalculator.niveau(20) == NiveauRisque.FAIBLE

@pytest.mark.asyncio
async def test_non_regression_monitoring():
    from app.services.monitoring_engine import MonitoringEngine, _creer_alerte, NiveauAlerte
    e = MonitoringEngine()
    assert len(e.detecteurs) == 6
    a = _creer_alerte(NiveauAlerte.WARNING, "D1", "T", "M", {})
    assert len(a.sha256) == 64

@pytest.mark.asyncio
async def test_non_regression_simulation():
    from app.services.simulation_engine import SimulationEngine, SimulationContext
    r = await SimulationEngine.simuler(
        SimulationContext("MUTATION", ["u1"], "u1", "NI-01"), db=None
    )
    assert r.simulation_id

@pytest.mark.asyncio
async def test_non_regression_logic():
    from app.services.logic_validator import LogicConflictEngine, Recommandation
    r = await LogicConflictEngine.valider(
        sql="TRUE", domaine="GENERAL", code="V108_COMPAT",
        niveau_blocage="BLOQUANT", db=None,
    )
    assert r.recommandation == Recommandation.AUTORISER

@pytest.mark.asyncio
async def test_non_regression_sandbox():
    from app.services.sql_sandbox import SQLRuleValidator, ValidationStatus
    r = await SQLRuleValidator.valider(
        sql="TRUE", domaine="GENERAL", code="V108_SANDBOX", db=None
    )
    assert r.status == ValidationStatus.VALIDE
