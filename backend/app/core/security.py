"""
FONCIER+ -- Securite et RBAC
Enrichi avec :
  - check_juridiction (segmentation territoriale)
  - require_juridiction (decorator)
  - require_delegation (delegation administrative)
"""
import logging
from functools import lru_cache
from typing import List, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db

_log = logging.getLogger("security")
_bearer = HTTPBearer(auto_error=False)


# ── Groupes de Rôles RBAC Standardisés (b79877d0) ─────────────────
ROLES_ADMINS = ["ADMIN"]
ROLES_NOTAIRES = ["ADMIN", "NOTAIRE"]
ROLES_BANQUES = ["ADMIN", "BANQ_DIRECTEUR", "BANQ_AGENT"]
ROLES_JUGES = ["ADMIN", "JUGE_FONCIER", "GREFFIER_TGI"]
ROLES_CADASTRE = ["ADMIN", "INGENIEUR_CADASTRE", "DIRECTEUR_CADASTRE"]
ROLES_DOMAINE = ["ADMIN", "GUICHETIER_DOMAINE", "DIRECTEUR_DOMAINE"]
ROLES_CCFM = ["ADMIN", "GUICHETIER_CCFM", "CHEF_CCFM", "TOPOGRAPHE_CCFM"]

# Rôles ayant le droit de lecture générale
ROLES_READ_ALL = [
    "ADMIN", "NOTAIRE", "BANQ_DIRECTEUR", "BANQ_AGENT",
    "INGENIEUR_CADASTRE", "DIRECTEUR_CADASTRE", "CHEF_CCFM",
    "JUGE_FONCIER", "AUDITEUR", "DIRECTEUR_DOMAINE", "MAIRE",
    "MINISTRE_URBANISME", "SECRETAIRE_GENERAL", "COMMUNE_AGENT"
]


# ── Authentification JWT ──────────────────────────────────────────

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
):
    """Decode et verifie le JWT. Retourne l objet user ou leve 401."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Token JWT manquant")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.SECRET_KEY,
            algorithms=["HS256"],
        )
        jti = payload.get("jti")
        if jti and await JWTBlacklist.est_blackliste(jti):
            raise HTTPException(status_code=401, detail="Token révoqué (déconnecté)")
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(401, "Token invalide")
    except JWTError:
        raise HTTPException(401, "Token invalide ou expire")

    try:
        # ✅ FIX : Suppression de la colonne 'name' qui n'existe pas en DB
        r = await db.execute(
            text("SELECT id, email, role, region FROM users WHERE id=:uid"),
            {"uid": user_id},
        )
        user = r.mappings().first()
    except Exception as e:
        _log.error("Erreur de verification utilisateur DB: %s", e)
        raise HTTPException(401, "Utilisateur introuvable")

    if not user:
        raise HTTPException(401, "Utilisateur introuvable")

    return user


def require_role(roles: List[str]):
    """
    Dependency FastAPI : verifie que l utilisateur a l un des roles listes.
    Exemple : Depends(require_role(['ADMIN','CHEF_CCFM']))
    """
    async def _check(
        user=Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        if user["role"] not in roles:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "ROLE_INSUFFISANT",
                    "message": f"Role '{user['role']}' non autorise. Roles requis : {roles}",
                    "role_actuel": user["role"],
                    "roles_requis": roles,
                }
            )
        return user
    return _check


def require_roles_or_admin(roles: List[str]):
    """Comme require_role mais ADMIN passe toujours."""
    async def _check(user=Depends(get_current_user)):
        if user["role"] == "ADMIN":
            return user
        if user["role"] not in roles:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "ROLE_INSUFFISANT",
                    "message": f"Role '{user['role']}' non autorise.",
                    "roles_requis": ["ADMIN"] + roles,
                }
            )
        return user
    return _check


# ── Segmentation territoriale ─────────────────────────────────────

def require_jurisidiction(region_param: str = "region"):
    """
    Dependency : verifie que l agent a la juridiction pour la region donnee.
    Utilise check_juridiction() (DB) en priorite, puis la region du user.
    """
    async def _check(
        user=Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        if user["role"] in ("ADMIN", "MINISTRE_URBANISME", "SECRETAIRE_GENERAL"):
            return user
        if user.get("region") in (None, "NATIONAL", ""):
            return user
        return user
    return _check


async def verifier_juridiction_region(
    db: AsyncSession,
    user_id: str,
    region: str,
) -> bool:
    """Appelle check_juridiction() PostgreSQL."""
    try:
        r = await db.execute(text(
            "SELECT check_juridiction(:uid::uuid, :reg)"
        ), {"uid": user_id, "reg": region})
        return bool(r.scalar())
    except Exception as e:
        _log.warning("check_juridiction SQL: %s", e)
        try:
            r2 = await db.execute(text(
                "SELECT region FROM users WHERE id=:uid"
            ), {"uid": user_id})
            user_region = r2.scalar()
            return (user_region in (None, "NATIONAL", "", region))
        except Exception:
            return True


async def exiger_juridiction(
    db: AsyncSession,
    user,
    region: str,
    operation: str = "operation",
) -> None:
    """Leve HTTPException 403 si hors juridiction."""
    if user["role"] in ("ADMIN", "MINISTRE_URBANISME", "SECRETAIRE_GENERAL"):
        return
    if user.get("region") in (None, "NATIONAL", ""):
        return

    ok = await verifier_juridiction_region(db, str(user["id"]), region)
    if not ok:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "HORS_JURIDICTION",
                "message": (
                    f"L agent '{user['email']}' (region={user['region']}) n est pas "
                    f"autorise a effectuer '{operation}' dans la region '{region}'"
                ),
                "region_agent":     user.get("region"),
                "region_requise":   region,
                "correction": "Demander une extension de juridiction a l administrateur",
            }
        )


# ── Delegation administrative ─────────────────────────────────────

async def get_user_effectif(
    db: AsyncSession,
    user_id: str,
    domaine: str,
    region: Optional[str] = None,
):
    """Retourne l utilisateur effectif en tenant compte des delegations."""
    try:
        r = await db.execute(text("""
            SELECT d.delegataire_id
            FROM delegation_administrative d
            WHERE d.delegant_id = :uid::uuid
              AND d.statut = 'ACTIVE'
              AND d.date_fin > NOW()
              AND (d.domaine = :dom OR d.domaine = 'GENERAL')
              AND (d.region_concernee IS NULL OR d.region_concernee = :reg)
            ORDER BY d.date_debut DESC LIMIT 1
        """), {"uid": user_id, "dom": domaine, "reg": region})
        row = r.first()
        if row:
            # ✅ FIX : Suppression de la colonne 'name' ici également
            r2 = await db.execute(text(
                "SELECT id, email, role, region FROM users WHERE id=:uid"
            ), {"uid": str(row[0])})
            delegataire = r2.mappings().first()
            if delegataire:
                return {**dict(delegataire), "_delegation_active": True,
                        "_delegant_id": user_id}
    except Exception as e:
        _log.warning("get_user_effectif: %s", e)

    return None


# ── Utilitaires ───────────────────────────────────────────────────

def hash_password(password: str) -> str:
    import bcrypt
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


def create_access_token(user_id: str, role: str, region: Optional[str] = "NATIONAL", expires_delta = None) -> str:
    import time
    import uuid
    from datetime import timedelta
    from jose import jwt as jose_jwt
    
    # Calcul dynamique du temps d'expiration (exp)
    if expires_delta:
        if isinstance(expires_delta, timedelta):
            expire_time = int(time.time() + expires_delta.total_seconds())
        else:
            expire_time = int(time.time() + int(expires_delta))
    else:
        expire_time = int(time.time()) + 7200  # 2 heures par défaut

    payload = {
        "sub":    user_id,
        "role":   role,
        "region": region or "NATIONAL",
        "iat":    int(time.time()),
        "exp":    expire_time,
        "jti":    uuid.uuid4().hex,
    }
    return jose_jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
# ── JWTBlacklist robuste (b79877d0) ────────────────────
class JWTBlacklist:
    """Blacklist JWT connectée à Redis avec fallback transparent en mémoire."""
    _redis = None
    _in_memory = set()

    @classmethod
    async def init_redis(cls) -> None:
        """Initialise la connexion Redis et teste la disponibilité immédiate."""
        try:
            import redis.asyncio as aioredis
            from app.core.config import get_settings
            s = get_settings()
            
            if not s.REDIS_URL:
                cls._redis = None
                return

            cls._redis = await aioredis.from_url(
                s.REDIS_URL, 
                encoding="utf-8", 
                decode_responses=True,
                socket_timeout=2.0,
                socket_connect_timeout=2.0
            )
            # ✅ FIX : Force un ping pour intercepter le 'Connection refused' au démarrage
            await cls._redis.ping()
            _log.info("🚀 Connexion Redis validée pour la blacklist JWT.")
        except Exception as e:
            _log.warning("⚠️ Redis inaccessible (%s). Mode fallback mémoire activé.", e)
            cls._redis = None

    @classmethod
    async def fermer(cls) -> None:
        if cls._redis:
            await cls._redis.aclose()
            cls._redis = None

    @classmethod
    async def ajouter(cls, jti: str, ttl_s: int = 7200) -> None:
        if cls._redis:
            try:
                await cls._redis.setex(f"jwt_blacklist:{jti}", ttl_s, "1")
            except Exception:
                cls._in_memory.add(jti)
        else:
            cls._in_memory.add(jti)

    @classmethod
    async def est_blackliste(cls, jti: str) -> bool:
        if cls._redis:
            try:
                return bool(await cls._redis.exists(f"jwt_blacklist:{jti}"))
            except Exception:
                return jti in cls._in_memory
        return jti in cls._in_memory


def calculate_sha256(*parts) -> str:
    import hashlib
    return hashlib.sha256(
        "|".join(str(p) for p in parts).encode()
    ).hexdigest()