"""
FONCIER+ — Endpoint Droits Fonciers
Branche DroitVersionService et TransfertService.
"""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user
from app.models.droits_fonciers import TypeTransfert
from app.services.droits_service import DroitVersionService

router = APIRouter(prefix="/droits", tags=["Droits fonciers"])


@router.get("/parcelle/{rnp_parcelle_id}/actifs", response_model=dict)
async def get_droits_actifs(
    rnp_parcelle_id: str, db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "ADMIN_CADASTRE", "DIRECTEUR_CADASTRE",
        "NOTAIRE", "BANQ_DIRECTEUR", "AUDITEUR",
    ])),
):
    """Retourne les droits de propriété actifs sur une parcelle."""
    droits = await DroitVersionService.get_droits_actifs(db, rnp_parcelle_id)
    if not droits:
        raise HTTPException(404, "Aucun droit actif trouvé")
    return {"rnp_parcelle_id": rnp_parcelle_id, "droits": droits}


@router.get("/parcelle/{rnp_parcelle_id}/historique", response_model=dict)
async def get_historique_droits(
    rnp_parcelle_id: str, db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "ADMIN_CADASTRE", "DIRECTEUR_CADASTRE",
        "NOTAIRE", "AUDITEUR",
    ])),
):
    """Retourne l'historique complet des droits d'une parcelle."""
    return await DroitVersionService.get_historique_complet(db, rnp_parcelle_id)


class TransfertIn(BaseModel):
    rnp_parcelle_id: str
    rnaf_id: str
    ancien_titulaire_id: str
    nouveau_titulaire_id: str
    pourcentage_transfere: float = Field(..., gt=0, le=100)
    type_transfert: str
    notaire_id: Optional[str] = None
    acte_reference: Optional[str] = None


@router.post("/transfert")
async def effectuer_transfert(
    payload: TransfertIn, db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN", "NOTAIRE", "DIRECTEUR_CADASTRE"])),
):
    """Effectue un transfert de propriété via DroitVersionService."""
    try:
        # ── ScopeFilter ──
        try:
            from app.core.scope_filter import ScopeFilter as _SF
            _sc = _SF.from_user(current_user)
            if not _sc.peut_voir_national:
                if _sc.region:
                    where += ' AND d.region = :_scope_region'
                    params['_scope_region'] = _sc.region
        except Exception: pass
        type_t = TypeTransfert(payload.type_transfert)
    except ValueError:
        await db.rollback()
        raise HTTPException(422, f"Type de transfert inconnu : {payload.type_transfert}")
    try:
        transfer, new_droit = await DroitVersionService.effectuer_transfert(
            db=db,
            rnp_parcelle_id=payload.rnp_parcelle_id,
            rnaf_id=payload.rnaf_id,
            ancien_titulaire_id=payload.ancien_titulaire_id,
            nouveau_titulaire_id=payload.nouveau_titulaire_id,
            pourcentage_transfere=payload.pourcentage_transfere,
            type_transfert=type_t,
            date_transfert=datetime.utcnow(),
            enregistre_par_id=str(current_user.id),
            notaire_id=payload.notaire_id,
            acte_reference=payload.acte_reference,
        )
    except ValueError as e:
        await db.rollback()
        raise HTTPException(422, str(e))
    await db.commit()
    return {"transfer_id": str(transfer.id),
            "nouveau_droit_id": str(new_droit.id),
            "sha256": new_droit.sha256_version}
