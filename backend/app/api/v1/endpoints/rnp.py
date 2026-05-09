"""
FONCIER+ — Module RNP (WF2)
Routes : création lien RNP→RNAF, statuts, synchronisation.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scope_filter import ScopeFilter  # noqa — ADMIN only, vision nationale
from app.core.database import get_db
from app.core.security import require_role, get_current_user
from app.models.workflows import RNPParcelleLink, StatutRNP, StatutRNAF

router = APIRouter(prefix="/cadastre/rnp", tags=["Cadastre — RNP — Registre Parcellaire"])


class RNPLinkIn(BaseModel):
    """Créer le lien RNP-RNAF (WF2 — Registre National Parcellaire)."""
    rnp_demande_id: str           = Field(..., min_length=3,
        description="Identifiant de la demande RNP")
    rnaf_id:        str           = Field(..., min_length=3,
        description="UUID du RNAF — doit être au statut PUBLIE")
    parcelle_id:    Optional[str] = Field(None,
        description="UUID de la parcelle (optionnel si NICAD pas encore attribué)")
    type_droit:     str           = Field(default="PROPRIETE",
        description="Type de droit : PROPRIETE|USUFRUIT|NUDA_PROPRIETE|SUPERFICIE")


@router.post("/liens", status_code=201)
async def creer_lien_rnp_rnaf(
    payload: RNPLinkIn, db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "ADMIN_CADASTRE", "GEOMETRE", "CHEF_SERVICE_CADASTRE",
    ])),
):
    """WF2 — Crée le lien entre un RNP et un RNAF publié.

    Règle : RNAF doit être au statut PUBLIE pour qu'un RNP soit créé.
    """
    # Vérifier statut RNAF
    r = await db.execute(
        text("SELECT statut FROM rnaf_workflow WHERE rnaf_id = :rid LIMIT 1"),
        {"rid": payload.rnaf_id}
    )
    row = r.mappings().first()
    if not row:
        raise HTTPException(404, "RNAF introuvable")
    if row["statut"] != StatutRNAF.PUBLIE.value:
        await db.rollback()
        raise HTTPException(400,
            f"RNAF au statut '{row['statut']}' — doit être PUBLIE avant création RNP")

    # CheckConstraint ck_rnp_actif_necessite_rnaf_publie sera validé en base
    lien = RNPParcelleLink(
        rnp_demande_id=payload.rnp_demande_id,
        rnaf_id=payload.rnaf_id,
        parcelle_id=payload.parcelle_id or None,
        statut_rnp=StatutRNP.PROVISOIRE,
        statut_rnaf_au_moment=StatutRNAF.PUBLIE,
    )
    db.add(lien)
    await db.commit()
    return {"id": str(lien.id), "statut_rnp": lien.statut_rnp,
            "rnaf_id": payload.rnaf_id}


@router.get("/liens/{rnp_demande_id}", response_model=dict)
async def get_lien_rnp(
    rnp_demande_id: str, db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "ADMIN_CADASTRE", "DIRECTEUR_CADASTRE",
        "GEOMETRE", "NOTAIRE", "CHEF_CCFM", "AUDITEUR",
    ])),
):
    r = await db.execute(
        select(RNPParcelleLink).where(RNPParcelleLink.rnp_demande_id == rnp_demande_id)
    )
    lien = r.scalar_one_or_none()
    if not lien:
        raise HTTPException(404, "Lien RNP introuvable")
    return {"id": str(lien.id), "statut_rnp": lien.statut_rnp,
            "rnaf_id": str(lien.rnaf_id),
            "parcelle_id": str(lien.parcelle_id) if lien.parcelle_id else None,
            "derniere_sync": lien.derniere_sync_at.isoformat()}
