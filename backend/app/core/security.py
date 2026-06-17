import logging
import os
from typing import Optional, List, Union
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

_log = logging.getLogger("security")

# ── SCHÉMAS DE SÉCURITÉ (COMPATIBILITÉ) ─────────────────
_bearer = HTTPBearer(auto_error=False)
security_scheme = _bearer  

# ── CONSTANTES DE RÔLES SYSTÈME (CORRIGÉES EN LISTES) ───
ROLES_BANQUES = ["BANQUE", "BANQUE_ADMIN", "OPERATEUR_BANQUE"]
ROLES_NOTAIRES = ["NOTAIRE", "NOTAIRE_ADMIN"]
ROLES_READ_ALL = ["ADMIN", "SUPERADMIN", "MINISTERE", "DIRECTION_NATIONALE", "AUDITEUR"]

class CurrentUser:
    """Objet utilisateur sécurisé compatible avec les attributs et les dictionnaires."""
    def __init__(self, payload: dict):
        self.id = payload.get("sub")
        self.role = payload.get("role")
        self.region = payload.get("region", "NATIONAL")
        self.payload = payload

    def dict(self):
        return {"id": self.id, "role": self.role, "region": self.region, **self.payload}

    def __getitem__(self, item):
        if item == "id":
            return self.id
        return self.payload.get(item)


# ── RÉSOLUTION DE LA CLÉ SECRÈTE ────────────────────────
def _get_jwt_secret() -> str:
    """Récupère dynamiquement la clé de signature sans générer d'AttributeError."""
    from app.core.config import settings
    for attr in ["JWT_SECRET", "jwt_secret", "SECRET_KEY", "secret_key"]:
        val = getattr(settings, attr, None)
        if val:
            return str(val)
    return os.getenv("JWT_SECRET") or os.getenv("SECRET_KEY", "fallback-static-key-dev-mfa")


# ── HACHAGE DES MOTS DE PASSE (PASSLIB) ──────────────────
def hash_password(password: str) -> str:
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    return pwd_context.verify(plain_password, hashed_password)


# ── GÉNÉRATION ET DÉCODAGE DES TOKENS ───────────────────
def create_access_token(user_id: str, role: str, region: str = "NATIONAL", expires_delta = None, additional_claims: dict = None, **kwargs) -> str:
    import time
    import uuid
    from datetime import timedelta
    from jose import jwt as jose_jwt
    
    if expires_delta:
        if isinstance(expires_delta, timedelta):
            expire_time = int(time.time() + expires_delta.total_seconds())
        else:
            expire_time = int(time.time() + int(expires_delta))
    else:
        expire_time = int(time.time()) + 7200  # 2 heures par défaut

    payload = {
        "sub":    str(user_id),
        "role":   str(role),
        "region": str(region) if region else "NATIONAL",
        "iat":    int(time.time()),
        "exp":    expire_time,
        "jti":    uuid.uuid4().hex,
    }

    if additional_claims and isinstance(additional_claims, dict):
        payload.update(additional_claims)

    return jose_jwt.encode(payload, _get_jwt_secret(), algorithm="HS256")


def create_refresh_token(user_id: str) -> str:
    import time
    import uuid
    from jose import jwt as jose_jwt
    
    payload = {
        "sub":    str(user_id),
        "iat":    int(time.time()),
        "exp":    int(time.time()) + 604800,  # 7 jours
        "jti":    uuid.uuid4().hex,
    }
    return jose_jwt.encode(payload, _get_jwt_secret(), algorithm="HS256")


def decode_token(token: str) -> dict:
    from jose import jwt as jose_jwt
    return jose_jwt.decode(token, _get_jwt_secret(), algorithms=["HS256"])


# ── DÉPENDANCES FASTAPI CONTRÔLE D'ACCÈS ────────────────
async def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> CurrentUser:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de sécurité manquant ou invalide."
        )
    token = credentials.credentials
    try:
        payload = decode_token(token)
        if await JWTBlacklist.est_blackliste(payload.get("jti")):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expirée ou token révoqué."
            )
        return CurrentUser(payload)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Session invalide : {str(e)}"
        )


def require_role(allowed_roles: Union[str, List[str]]):
    if isinstance(allowed_roles, str):
        allowed_roles = [allowed_roles]

    def dependency(current_user: CurrentUser = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Droits d'accès insuffisants pour effectuer cette opération."
            )
        return current_user
    return dependency


# ── BLACKLIST JWT (REDIS CORRIGÉ) ───────────────────────
class JWTBlacklist:
    """Blacklist JWT connectée à Redis avec fallback transparent en mémoire."""
    _redis = None
    _in_memory = set()

    @classmethod
    async def init_redis(cls) -> None:
        try:
            import redis.asyncio as aioredis
            from app.core.config import get_settings
            s = get_settings()
            
            if not getattr(s, "REDIS_URL", None):
                cls._redis = None
                return

            cls._redis = await aioredis.from_url(
                s.REDIS_URL, 
                encoding="utf-8", 
                decode_responses=True,
                socket_timeout=2.0,
                socket_connect_timeout=2.0
            )
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