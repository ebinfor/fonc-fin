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
import contextvars
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
    logger.info("===> [LIFESPAN] Démarrage du cycle de vie...")
    
    # 1. Blacklist JWT Redis
    try:
        from app.core.security import JWTBlacklist
        await JWTBlacklist.init_redis()
        logger.info("===> [LIFESPAN] Redis initialisé")
    except Exception as e:
        logger.error(f"===> [LIFESPAN] Échec Redis: {e}")

    # 2. Moteur de monitoring temps réel
    try:
        from app.services.monitoring_engine import MonitoringEngine
        from app.core.database import database_session_scope
        
        # On s'assure d'exécuter l'instance proprement
        engine = MonitoringEngine.instance()
        await engine.demarrer(database_session_scope)
        logger.info("===> [LIFESPAN] MonitoringEngine démarré")
    except Exception as exc:
        logger.error(f"===> [LIFESPAN] Échec MonitoringEngine: {exc}")

    logger.info("===> [LIFESPAN] Application prête à recevoir des requêtes !")
    yield
    
    logger.info("===> [LIFESPAN] Arrêt de l'application...")
    try:
        await MonitoringEngine.instance().arreter()
    except Exception:
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Application FastAPI (CRUCIAL : Déclarée AVANT d'y attacher les middlewares)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
app = FastAPI(
    title       = "FONCIER+ — API Nationale v1.0.9",
    description = "Plateforme nationale de gestion foncière — République du Niger",
    version     = "1.0.9",
    lifespan    = lifespan
)


# ── Middleware X-Request-ID ───────────────────────────────────
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
            pipe.zadd(key, {f"{now}_{time.monotonic()}": now})
            pipe.expire(key, cls.WINDOW_SEC + 10) # 10s de marge
            
            results = await pipe.execute()
            count = results[1]
            
            if count >= limit:
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
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from app.api.v1.endpoints.monitoring       import router as monitoring_router
from app.api.v1.endpoints.risk             import router as risk_router
from app.api.v1.endpoints.dashboard        import router as dashboard_router
from app.api.v1.endpoints.ccfm_v3          import router as ccfm_v3_router

app.include_router(monitoring_router, prefix="/api/v1")

# ── Routeur DAR — monté sous chaque module ──────────────────
from app.api.v1.endpoints.dar import router as _dar_router_template
from fastapi import APIRouter as _APIRouter

_dar_modules = {
    "urbanisme": "/urbanisme/dar",
    "cadastre": "/cadastre/dar",
    "ccfm": "/ccfm/dar",
    "domaine": "/domaine/dar",
    "notaire": "/notaire/dar",
    "banque": "/banque/dar",
    "justice": "/justice/dar",
    "commune": "/commune/dar",
    "annf": "/annf/dar",
    "bgu": "/bgu/dar",
    "journal_officiel": "/jo/dar",
}
for _dar_prefix in _dar_modules.values():
    _dar_copy = _APIRouter(prefix=_dar_prefix, tags=[f'DAR — {_dar_prefix}'])
    for _route in _dar_router_template.routes:
        _dar_copy.routes.append(_route)
    app.include_router(_dar_copy, prefix='/v1')

app.include_router(risk_router,   prefix="/api/v1")
app.include_router(dashboard_router,  prefix="/api/v1")
app.include_router(ccfm_v3_router)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Routers dynamiques via ServiceRegistry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from app.core.service_registry import ServiceRegistry

_ALREADY_LOADED = {"monitoring", "risk", "dashboard", "ccfm_v3"}

for _router, _prefix in ServiceRegistry.get_routers():
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
    ServiceRegistry.active_modules()
)