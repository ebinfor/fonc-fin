"""
FONCIER+ — Module Utilisateurs (Sprint D)
Routes : liste, création, activation/désactivation, profil.
Complémente app/api/v1/endpoints/auth.py (login/refresh/me).
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user, hash_password
from app.models.users import User

router = APIRouter(prefix="/admin/users", tags=["Utilisateurs — Admin"])

ROLES_VALIDES = {
    "ADMIN", "MINISTRE_URBANISME",
    "ADMIN_CADASTRE", "DIRECTEUR_CADASTRE", "CHEF_SERVICE_CADASTRE",
    "GEOMETRE", "TOPOGRAPHE", "SECRETARIAT_CADASTRE",
    "DIRECTEUR_URBANISME", "CHEF_URBANISME", "AGENT_URBANISME",
    "ADMIN_COMMUNE", "MAIRE", "AGENT_COMMUNE",
    "CHEF_CCFM", "AGENT_CCFM", "NOTAIRE",
    "BANQ_DIRECTEUR", "BANQ_AGENT",
    "JUGE_FONCIER", "GREFFIER", "HUISSIER",
    "DIRECTEUR_DOMAINE", "AGENT_DOMAINE",
    "EDITEUR_JO", "RESPONSABLE_BGU",
    "AUDITEUR", "ARCHIVISTE_ANNF", "RESPONSABLE_ANNF",
}


class UserCreateIn(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    role: str
    region: Optional[str] = "NIA"


class UserUpdateIn(BaseModel):
    """Modifier le rôle, la région ou le statut d'un utilisateur."""
    role:   Optional[str]  = Field(None, min_length=3,
        description="Nouveau rôle RBAC — doit être dans ROLES_VALIDES")
    region: Optional[str]  = Field(None,
        pattern="^(NIA|ZDR|MAR|TAH|AGZ|DOS|TLW|DIL)$",
        description="Code région — NIA|ZDR|MAR|TAH|AGZ|DOS|TLW|DIL")
    actif:  Optional[bool] = Field(None,
        description="Activer ou désactiver le compte")

class UserOut(BaseModel):
    id: str
    email: str
    role: str
    region: Optional[str]
    actif: bool

    class Config:
        from_attributes = True


@router.get("/", response_model=list[UserOut])
async def lister_utilisateurs(
    role: Optional[str] = Query(None),
    actif: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN"])),
):
    """Liste tous les utilisateurs. Réservé ADMIN.
    Note : ScopeFilter non appliqué — users.py est réservé ADMIN (vision nationale).
    """
    from app.core.scope_filter import ScopeFilter as _SF  # noqa — documentation
    # Les utilisateurs ADMIN ont vision nationale — pas de filtre région
    filters = []
    if role:
        filters.append(User.role == role)
    if actif is not None:
        filters.append(User.actif == actif)
    q = select(User)
    if filters:
        q = q.where(and_(*filters))
    q = q.order_by(User.email).offset((page - 1) * limit).limit(limit)
    r = await db.execute(q)
    return [UserOut(id=str(u.id), email=u.email, role=u.role,
                    region=u.region, actif=u.actif)
            for u in r.scalars().all()]


@router.post("/", status_code=201, response_model=UserOut)
async def creer_utilisateur(
    payload: UserCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN"])),
):
    """Crée un utilisateur avec un rôle RBAC validé. Réservé ADMIN."""
    if payload.role not in ROLES_VALIDES:
        raise HTTPException(422, f"Rôle '{payload.role}' invalide. "
                            f"Rôles valides : {sorted(ROLES_VALIDES)}")
    r = await db.execute(select(User).where(User.email == payload.email))
    if r.scalar_one_or_none():
        raise HTTPException(409, f"Email {payload.email} déjà utilisé")

    user = User(
        email=str(payload.email),
        password_hash=hash_password(payload.password),
        role=payload.role,
        region=payload.region,
        actif=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserOut(id=str(user.id), email=user.email, role=user.role,
                   region=user.region, actif=user.actif)


@router.get("/{user_id}", response_model=UserOut)
async def get_utilisateur(
    user_id: str, db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN"])),
):
    r = await db.execute(select(User).where(User.id == user_id))
    user = r.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")
    return UserOut(id=str(user.id), email=user.email, role=user.role,
                   region=user.region, actif=user.actif)


@router.patch("/{user_id}", response_model=UserOut)
async def modifier_utilisateur(
    user_id: str, payload: UserUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN"])),
):
    """Modifie rôle, région ou statut actif. Réservé ADMIN."""
    r = await db.execute(select(User).where(User.id == user_id))
    user = r.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")
    if payload.role:
        if payload.role not in ROLES_VALIDES:
            raise HTTPException(422, f"Rôle '{payload.role}' invalide")
        user.role = payload.role
    if payload.region is not None:
        user.region = payload.region
    if payload.actif is not None:
        user.actif = payload.actif
    await db.commit()
    await db.refresh(user)
    return UserOut(id=str(user.id), email=user.email, role=user.role,
                   region=user.region, actif=user.actif)
