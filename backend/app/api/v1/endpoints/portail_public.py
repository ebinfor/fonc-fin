"""
Filtrage acteur : module admin/public — ScopeFilter non applicable.

FONCIER+ — Portail public de vérification citoyenne (sans authentification)
Branche PortailPublicService.
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends
from app.core.database import get_db
from app.services.workflow_orchestrator import PortailPublicService

router = APIRouter(prefix="/verify", tags=["Portail public"])


@router.get("/ccfm/{nicad}")
# PUBLIC: QR code CCFM — sans auth intentionnel (portail citoyen)
async def verifier_parcelle_publique(
    nicad: str, db: AsyncSession = Depends(get_db)
):
    """
    Accessible sans authentification.
    Retourne les données publiques d'une parcelle (statut, validité).
    Utilisé par les QR codes sur les certificats CCFM.
    """
    data = await PortailPublicService.get_statut_parcelle(db, nicad)
    if not data:
        raise HTTPException(404, f"Parcelle {nicad} introuvable ou non publique")
    return data


@router.get("/ccfm/{nicad}/historique")
# PUBLIC: historique public — sans auth intentionnel
async def historique_statuts_public(
    nicad: str, db: AsyncSession = Depends(get_db)
):
    """Historique public des statuts d'une parcelle."""
    return await PortailPublicService.get_historique_statuts(db, nicad)
