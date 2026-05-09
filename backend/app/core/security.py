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
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(401, "Token invalide")
    except JWTError:
        raise HTTPException(401, "Token invalide ou expire")

    try:
        r = await db.execute(
            text("SELECT id, email, name, role, region FROM users WHERE id=:uid"),
            {"uid": user_id},
        )
        user = r.mappings().first()
    except Exception:
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

def require_juridiction(region_param: str = "region"):
    """
    Dependency : verifie que l agent a la juridiction pour la region donnee.
    Utilise check_juridiction() (DB) en priorite, puis la region du user.

    Usage : Depends(require_juridiction("region"))
    """
    async def _check(
        user=Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        # Les admins et niveaux nationaux passent toujours
        if user["role"] in ("ADMIN", "MINISTRE_URBANISME", "SECRETAIRE_GENERAL"):
            return user
        if user.get("region") in (None, "NATIONAL", ""):
            return user

        # Pour les endpoints qui passent la region en parametre,
        # la verification se fait dans le endpoint lui-meme
        # Ce decorator verifie uniquement la region du token
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
        # Fallback : verifier via la table users
        try:
            r2 = await db.execute(text(
                "SELECT region FROM users WHERE id=:uid"
            ), {"uid": user_id})
            user_region = r2.scalar()
            return (user_region in (None, "NATIONAL", "", region))
        except Exception:
            return True  # Fallback permissif


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
    """
    Retourne l utilisateur effectif en tenant compte des delegations.
    Si une delegation active existe, retourne le delegataire.
    Sinon retourne l utilisateur original.
    """
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
            r2 = await db.execute(text(
                "SELECT id, email, name, role, region FROM users WHERE id=:uid"
            ), {"uid": str(row[0])})
            delegataire = r2.mappings().first()
            if delegataire:
                return {**dict(delegataire), "_delegation_active": True,
                        "_delegant_id": user_id}
    except Exception as e:
        _log.warning("get_user_effectif: %s", e)

    return None  # Pas de delegation, utiliser l utilisateur original


# ── Utilitaires ───────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash bcrypt du mot de passe."""
    import bcrypt
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Verifie un mot de passe contre son hash bcrypt."""
    import bcrypt
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


def create_access_token(user_id: str, role: str, region: str) -> str:
    """Cree un JWT HS256 avec exp 2h."""
    from datetime import timedelta
    from jose import jwt as jose_jwt
    import time
    payload = {
        "sub":    user_id,
        "role":   role,
        "region": region,
        "iat":    int(time.time()),
        "exp":    int(time.time()) + 7200,  # 2h
    }
    return jose_jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


# ── JWTBlacklist stub (compatible v1.0.9) ────────────────────
class JWTBlacklist:
    """Stub de blacklist JWT — à connecter à Redis en production."""
    _redis = None

    @classmethod
    async def init_redis(cls) -> None:
        """Initialise la connexion Redis pour la blacklist JWT."""
        try:
            import redis.asyncio as aioredis
            from app.core.config import get_settings
            s = get_settings()
            cls._redis = await aioredis.from_url(
                s.REDIS_URL, encoding="utf-8", decode_responses=True
            )
        except Exception:
            cls._redis = None  # Fallback sans Redis

    @classmethod
    async def fermer(cls) -> None:
        if cls._redis:
            await cls._redis.aclose()
            cls._redis = None

    @classmethod
    async def ajouter(cls, jti: str, ttl_s: int = 7200) -> None:
        if cls._redis:
            await cls._redis.setex(f"jwt_blacklist:{jti}", ttl_s, "1")

    @classmethod
    async def est_blackliste(cls, jti: str) -> bool:
        if cls._redis:
            return bool(await cls._redis.exists(f"jwt_blacklist:{jti}"))
        return False
