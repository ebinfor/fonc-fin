"""
FONCIER+ v1.0.9 — Application principale
République du Niger — DNCD — Ministère de l'Urbanisme et de l'Habitat

Corrections intégrées :
  P0-1 : journal_officiel enregistré (17 routes /jo/*)
  P0-2 : GUICHETIER_CCFM + SECRETAIRE_CCFM dans ccfm_v3
  P1-1 : AGENT_DOMAINE étendu (16 routes domaine.py)
  P1-2 : 9 workspaces espace_travail.py (17 routes totales)
  P2-2 : EDITEUR_JO dans POST /jo/parutions
  P2-3 : TOPOGRAPHE explicite dans _TOPOGRAPHE ccfm_v3
  P3-1 : GREFFIER étendu dans justice.py
"""
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.config import get_settings

settings = get_settings()
logger   = logging.getLogger("foncier")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Lifespan : démarrage / arrêt ordonnés
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Blacklist JWT Redis
    from app.core.security import JWTBlacklist
    await JWTBlacklist.init_redis()

    # 2. Moteur de monitoring temps réel
    from app.services.monitoring_engine import MonitoringEngine
    from app.core.database import get_db_session
    try:
        await MonitoringEngine.instance().demarrer(get_db_session)
        logger.info("MonitoringEngine démarré")
    except Exception as exc:
        logger.warning("MonitoringEngine démarrage: %s", exc)

    if settings.SENTRY_DSN:
        import sentry_sdk
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            traces_sample_rate=0.1,
            before_send=lambda e, h: None
                if e.get("level") in ("warning",)
                   and e.get("exception", {}).get("values", [{}])[0]
                      .get("type") in ("AuthenticationError",)
                else e,
        )

    logger.info(
        "FONCIER+ v1.0.9 démarré — JWT:%s Redis blacklist:%s",
        settings.jwt_algorithm_actif,
        settings.redis_blacklist_actif,
    )
    yield

    await MonitoringEngine.instance().arreter()
    from app.core.security import JWTBlacklist
    await JWTBlacklist.fermer()
    logger.info("FONCIER+ v1.0.9 arrêté proprement")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Application FastAPI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

app = FastAPI(
    title       = "FONCIER+ — API Nationale v1.0.9",
    description = "Plateforme nationale de gestion foncière — République du Niger",
    version     = "1.0.9",
    docs_url    = "/docs"  if not settings.is_production else None,
    redoc_url   = "/redoc" if not settings.is_production else None,
    lifespan    = lifespan,
)


# ── Middleware X-Request-ID ───────────────────────────────────
import contextvars
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    _request_id_var.set(request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def get_request_id() -> str:
    return _request_id_var.get()


# ── Rate limiting global ──────────────────────────────────────
# ── Rate limiting global ──────────────────────────────────────
class GlobalRateLimiter:
    """Rate limiter par IP basé sur Redis (Sorted Sets) et asynchrone."""
    WINDOW_SEC = 60
    MAX_GLOBAL = 1000
    MAX_AUTH   = 30

    @classmethod
    async def check(cls, ip: str, path: str = "") -> tuple:
        import time
        from app.core.security import JWTBlacklist
        
        limit = cls.MAX_AUTH if "/auth/" in path else cls.MAX_GLOBAL
        
        # Fallback si Redis n'est pas actif (ex: test local sans Redis)
        if not JWTBlacklist._redis:
            return True, limit

        try:
            r = JWTBlacklist._redis
            now = time.time()
            clear_before = now - cls.WINDOW_SEC
            key_suffix = "auth" if "/auth/" in path else "global"
            key = f"rate_limit:{key_suffix}:{ip}"
            
            # Utilisation d'un pipeline pour l'atomicité et la vitesse
            pipe = r.pipeline()
            pipe.zremrangebyscore(key, "-inf", clear_before)
            pipe.zcard(key)
            # Utiliser la valeur du timestamp + un microsecond float aléatoire pour éviter les doublons de score exacts
            pipe.zadd(key, {f"{now}_{time.monotonic()}": now})
            pipe.expire(key, cls.WINDOW_SEC + 10) # 10s de marge
            
            results = await pipe.execute()
            count = results[1]
            
            if count >= limit:
                # Si la limite est dépassée, on nettoie pour ne pas gonfler artificiellement le ZSET
                await r.zremrangebyscore(key, now, "+inf")
                return False, 0
                
            return True, limit - (count + 1)
        except Exception as e:
            logger.warning("RedisRateLimiter erreur: %s", e)
            return True, limit


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if path in ("/health", "/health/ready", "/health/live",
                "/metrics", "/docs", "/redoc", "/openapi.json"):
        return await call_next(request)
    ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.headers.get("X-Real-IP", "")
        or getattr(request.client, "host", "unknown")
    )
    allowed, remaining = await GlobalRateLimiter.check(ip, path)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={
                "code":    "RATE_LIMIT_EXCEEDED",
                "message": f"Trop de requêtes depuis {ip}. Réessayez dans 60 secondes.",
                "retry_after_sec": GlobalRateLimiter.WINDOW_SEC,
            },
            headers={
                "Retry-After":           str(GlobalRateLimiter.WINDOW_SEC),
                "X-RateLimit-Limit":     str(GlobalRateLimiter.MAX_GLOBAL),
                "X-RateLimit-Remaining": "0",
            }
        )
    response = await call_next(request)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


# ── Middleware audit / temps de réponse ───────────────────────
@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    start    = time.time()
    response = await call_next(request)
    elapsed  = round((time.time() - start) * 1000)
    response.headers["X-Response-Time-Ms"] = str(elapsed)
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        logger.info(
            "%s %s → %d (%dms) ip=%s rid=%s",
            request.method, request.url.path,
            response.status_code, elapsed,
            getattr(request.client, "host", "?"),
            getattr(request.state, "request_id", "-")[:8],
        )
    return response


# ── CORS ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=(
        ["https://foncier.gov.ne", "https://www.foncier.gov.ne"]
        if settings.is_production else ["*"]
    ),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-RateLimit-Remaining"],
)


# ── Gestionnaire d'erreurs global ────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Erreur non gérée %s %s: %s",
        request.method, request.url.path, exc, exc_info=True
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Erreur interne du serveur", "path": str(request.url.path)},
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Health checks
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.get("/health", tags=["Système"])
async def health_check():
    """Liveness + Readiness probe — vérifie DB, Redis, MonitoringEngine."""
    from app.core.database import engine
    from app.services.monitoring_engine import MonitoringEngine
    issues = []
    try:
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        issues.append(f"db: {str(exc)[:80]}")
    from app.core.security import JWTBlacklist
    if JWTBlacklist._redis:
        try:
            await JWTBlacklist._redis.ping()
        except Exception as exc:
            issues.append(f"redis: {str(exc)[:80]}")
    mon = MonitoringEngine.instance()
    if not mon._running:
        issues.append("monitoring: stopped")
    body = {
        "status":  "ok" if not issues else "degraded",
        "version": "1.0.9",
        "issues":  issues,
    }
    return JSONResponse(content=body, status_code=200 if not issues else 503)

@app.get("/health/ready", tags=["Système"])
async def readiness(): return {"ready": True}

@app.get("/health/live", tags=["Système"])
async def liveness(): return {"alive": True}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Routers intelligents (monitoring/risk/dashboard) — préfixe /api/v1
# Chargés en dur car leurs préfixes sont distincts des modules /v1
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from app.api.v1.endpoints.monitoring       import router as monitoring_router
from app.api.v1.endpoints.risk             import router as risk_router
from app.api.v1.endpoints.dashboard        import router as dashboard_router
from app.api.v1.endpoints.ccfm_v3          import router as ccfm_v3_router

app.include_router(monitoring_router, prefix="/api/v1")

# ── Routeur DAR — monté sous chaque module ──────────────────
from app.api.v1.endpoints.dar import router as _dar_router_template
from fastapi import APIRouter as _APIRouter

# ── Montage des DAR par module ──────────────────────────────
# Chaque module a son propre DAR avec ses archives spécifiques
_dar_modules = {
    "urbanisme": "/urbanisme/dar",  # Arrêtés fonciers, RNAF papier, plans directeurs, c
    "cadastre": "/cadastre/dar",  # Plans cadastraux papier, NICAD historiques, BD Top
    "ccfm": "/ccfm/dar",  # Certificats CCFM antérieurs à v1.0, dossiers de de
    "domaine": "/domaine/dar",  # Expropriations historiques, concessions domaniales
    "notaire": "/notaire/dar",  # Actes notariés papier, mutations anciennes, succes
    "banque": "/banque/dar",  # Hypothèques papier, actes de mainlevée anciens, re
    "justice": "/justice/dar",  # Jugements fonciers papier, greffes judiciaires, dé
    "commune": "/commune/dar",  # Dépôts fonciers communaux anciens, registres commu
    "annf": "/annf/dar",  # Supervision des DAR nationaux, scellement central 
    "bgu": "/bgu/dar",  # Plans graphiques papier, cartes topographiques, or
    "journal_officiel": "/jo/dar",  # Parutions JO papier, journaux anciens, textes fonc
}
for _dar_prefix in _dar_modules.values():
    # Créer une copie du routeur pour chaque module
    _dar_copy = _APIRouter(prefix=_dar_prefix, tags=[f'DAR — {_dar_prefix}'])
    for _route in _dar_router_template.routes:
        _dar_copy.routes.append(_route)
    app.include_router(_dar_copy, prefix='/v1')

app.include_router(risk_router,       prefix="/api/v1")
app.include_router(dashboard_router,  prefix="/api/v1")
# ccfm_v3 déclare son propre préfixe /api/v3/ccfm
app.include_router(ccfm_v3_router)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Routers dynamiques via ServiceRegistry
# En mode monolithique : tous les modules sont chargés.
# En mode microservice : seuls les modules définis par
#   FONCIER_MODULES sont activés (ex: "ccfm,ccfm_v3,auth,users")
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from app.core.service_registry import ServiceRegistry

# Modules à exclure car déjà déclarés ci-dessus avec préfixe personnalisé
_ALREADY_LOADED = {"monitoring", "risk", "dashboard", "ccfm_v3"}

for _router, _prefix in ServiceRegistry.get_routers():
    # Ignorer les modules déjà chargés
    _name = getattr(_router, "prefix", "")
    if any(skip in str(_name) for skip in ["/monitoring", "/risk", "/dashboard", "/api/v3/ccfm"]):
        continue
    if _prefix:
        app.include_router(_router, prefix=_prefix)
    else:
        app.include_router(_router)

logger.info(
    "FONCIER+ v1.0.9 — service=%s modules=%s",
    ServiceRegistry.service_name(),
    ServiceRegistry.active_modules(),
)
