"""
Filtrage acteur : module admin/public — ScopeFilter non applicable.
FONCIER+ — Auth JWT"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import (create_access_token, create_refresh_token,
    decode_token, get_current_user, verify_password)
from app.models.users import User

# ── Rate-limit en mémoire pour auth (fallback si Redis absent) ──
import time
from collections import defaultdict as _dd
_login_attempts: dict = _dd(list)
_LOGIN_MAX   = 5    # tentatives max
_LOGIN_WIN   = 300  # fenêtre 5 minutes


def _check_login_rate(ip: str) -> bool:
    """Retourne True si l'IP peut tenter un login."""
    now = time.monotonic()
    dq  = _login_attempts[ip]
    # Purger les entrées expirées
    while dq and dq[0] < now - _LOGIN_WIN:
        dq.pop(0)
    if len(dq) >= _LOGIN_MAX:
        return False
    dq.append(now)
    return True

router = APIRouter(prefix="/auth", tags=["Auth"])

class LoginIn(BaseModel): email: EmailStr; password: str
class TokenOut(BaseModel): access_token: str; refresh_token: str; token_type: str = "bearer"; role: str

@router.post("/login", response_model=TokenOut)
async def login(p: LoginIn, db: AsyncSession = Depends(get_db)):
    try:
        # Rate-limit par IP
        _ip = getattr(request.client, 'host', 'unknown') if hasattr(request, 'client') else 'unknown'
        if not _check_login_rate(_ip):
            raise HTTPException(status_code=429,
                detail='Trop de tentatives de connexion. Attendez 5 minutes.')
        r = await db.execute(select(User).where(User.email == p.email, User.actif == True))
        u = r.scalar_one_or_none()
        if not u or not verify_password(p.password, u.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants incorrects")
        return TokenOut(access_token=create_access_token(str(u.id), u.role),
                        refresh_token=create_refresh_token(str(u.id)), role=u.role)
    except Exception as e:
        import logging as _l
        _l.getLogger('auth').warning('login: %s', e)
        raise HTTPException(status_code=500,
            detail=str(e)[:200])

@router.get("/me", response_model=list)
async def me(cu=Depends(get_current_user)):
    return {"id": str(cu.id), "email": cu.email, "role": cu.role, "region": cu.region}
