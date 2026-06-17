"""
FONCIER+ — Module CCFM v1 (Standardisé & Unifié avec CCFM v3)
=============================================================
Toutes les requêtes de l'ancien namespace /v1/ccfm sont désormais déléguées
au service central unique CCFMWorkflowService.
Cela garantit une source de vérité unique pour la machine d'état CCFM,
élimine le code SQL dupliqué et standardise les payloads et les réponses.
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import require_role, get_current_user
from app.api.v1.endpoints.ccfm_v3 import _AsyncpgCompat, _get_service, _user_id, _handle_error

router = APIRouter(
    prefix="/ccfm",
    tags=["CCFM -- Certificat de Conformite Fonciere Mixte"],
)

# Roles CCFM
_GUICHET    = ["ADMIN", "CHEF_CCFM", "AGENT_CCFM", "GUICHETIER_CCFM"]
_SECRETAIRE = ["ADMIN", "CHEF_CCFM", "SECRETAIRE_CCFM", "SECRETAIRE_GENERAL"]
_TOPOGRAPHE = ["ADMIN", "TOPOGRAPHE", "GEOMETRE"]
_DIRECTEUR  = ["ADMIN", "DIRECTEUR_URBANISME", "CHEF_CCFM"]
_ARCHIVISTE = ["ADMIN", "ARCHIVISTE_ANNF", "RESPONSABLE_ANNF", "CHEF_CCFM"]
_LECTURE    = ["ADMIN", "CHEF_CCFM", "AGENT_CCFM", "AUDITEUR",
               "DIRECTEUR_URBANISME", "NOTAIRE", "BANQ_DIRECTEUR",
               "BANQ_AGENT", "JUGE_FONCIER", "GREFFIER"]

class DemandeIn(BaseModel):
    nom_prenom:         str = Field(..., min_length=3)
    nip_passeport:      str = Field(..., min_length=3)
    date_naissance:     str = Field(..., description="YYYY-MM-DD")
    lieu_naissance:     str = Field(..., min_length=2)
    adresse:            str = Field(..., min_length=5)
    telephone:          str = Field(..., min_length=8, max_length=20)
    email:              Optional[str] = None
    localite:           str = Field(..., min_length=2)
    lot:                str = Field(..., min_length=1)
    parcelle:           str = Field(..., min_length=1)
    superficie_m2:      float = Field(..., gt=0)
    mode_paiement:      str = Field(..., description="AIRTEL_MONEY|MOOV_MONEY|BANQUE|TRESOR_PUBLIC")
    reference_paiement: str = Field(..., min_length=3)
    montant_paye:       float = Field(default=50000.0, ge=50000)
    numero_arrete:      Optional[str] = None
    nicad_code:         Optional[str] = None

class OrdreMissionIn(BaseModel):
    ccfm_id:            str
    topographe_id:      str
    topographe_nom:     str
    date_prevue_visite: Optional[str] = None
    instructions:       Optional[str] = None

class FicheConstatIn(BaseModel):
    ccfm_id:               str
    superficie_mesuree_m2: float = Field(..., gt=0)
    gps_mesure_latitude:   Optional[float] = None
    gps_mesure_longitude:  Optional[float] = None
    concordance_gps:       Optional[bool]  = None
    jo_publie:             bool  = False
    arrete_numero:         Optional[str]   = None
    arrete_autorite:       Optional[str]   = None
    titre_foncier_numero:  Optional[str]   = None
    titre_authentique:     Optional[bool]  = None
    decision:              str   = Field(..., description="AUTORISATION ou REFUS")
    motif_refus:           Optional[str]   = None
    observations:          Optional[str]   = None
    topographe_nom:        str

class RapportIn(BaseModel):
    ccfm_id:             str
    verdict_global:      str   = Field(..., description="CONFORME|A_VERIFIER|NON_CONFORME")
    points_conformite:   List[str] = []
    anomalies_detectees: List[str] = []

class SignerIn(BaseModel):
    directeur_nom:  str  = Field(..., min_length=3)
    huissier_nom:   str  = Field(default="A renseigner")
    observations:   Optional[str] = None

class ArchiverIn(BaseModel):
    observations: Optional[str] = None

async def _get_ccfm_id_by_nus(db: AsyncSession, nus: str) -> str:
    r = await db.execute(text("SELECT ccfm_id FROM demandes_ccfm WHERE nus = :nus"), {"nus": nus})
    cid = r.scalar()
    if not cid:
        raise HTTPException(404, f"NUS {nus} introuvable")
    return str(cid)

@router.post("/demandes", status_code=201)
async def creer_demande(
    payload: DemandeIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_GUICHET)),
):
    svc = _get_service(db)
    try:
        res = await svc.enregistrer_demande(payload.model_dump(), guichetiere_id=str(current_user.id))
        return {
            "nus": res["nus"], "etat": res["etat"], "sha256": res.get("hash_ccfm", ""),
            "message": "Demande enregistree -- recepisse PDF en generation",
            "prochaine_etape": "Verification automatique RNAF+BGU (systeme)",
        }
    except Exception as e:
        _handle_error(e, "creer_demande")

@router.post("/demandes/{nus}/ticket")
async def generer_ticket_rnaf_bgu(
    nus: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN", "CHEF_CCFM", "AGENT_CCFM"])),
):
    cid = await _get_ccfm_id_by_nus(db, nus)
    svc = _get_service(db)
    try:
        res = await svc.lancer_verification_auto(cid)
        return {
            "nus": nus, "etat": res["etat"],
            "rnaf_conforme": res["ticket"]["rnaf_conforme"],
            "bgu_conforme": res["ticket"]["bgu_conforme"],
            "gps": {"lat": res["ticket"]["gps_lat"], "lon": res["ticket"]["gps_lon"]} if res["ticket"]["gps_lat"] else None,
            "ticket_hash": res["ticket"]["ticket_hash"],
            "prochaine_etape": "Secretaire emet l ordre de mission" if res["etat"] == "TICKET_EMIS" else "Dossier rejete",
        }
    except Exception as e:
        _handle_error(e, "generer_ticket_rnaf_bgu")

@router.post("/ordres-mission", status_code=201)
async def emettre_ordre_mission(
    payload: OrdreMissionIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SECRETAIRE)),
):
    svc = _get_service(db)
    try:
        res = await svc.emettre_ordre_mission(payload.model_dump(), secretaire_id=str(current_user.id))
        return {
            "numero_ordre": res["numero_ordre"], "ccfm_id": res["ccfm_id"],
            "destinataire": payload.topographe_nom,
            "ticket_joint": res["ticket_joint"],
            "message": "Ordre de mission emis avec ticket RNAF+BGU+GPS joint",
        }
    except Exception as e:
        _handle_error(e, "emettre_ordre_mission")

@router.post("/fiches-constat", status_code=201)
async def deposer_fiche_constat(
    payload: FicheConstatIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_TOPOGRAPHE)),
):
    svc = _get_service(db)
    try:
        res = await svc.deposer_fiche_constat(payload.model_dump())
        return {
            "ccfm_id": res["ccfm_id"], "decision": payload.decision,
            "ecart_superficie_pct": res["ecart_pct"], "concordance_gps": res["concordance_gps"],
            "hash_fiche": res["hash_fiche"],
            "message": "Fiche de constat deposee -- rapport en attente de generation",
        }
    except Exception as e:
        _handle_error(e, "deposer_fiche_constat")

@router.post("/rapports-appreciation", status_code=201)
async def generer_rapport_appreciation(
    payload: RapportIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN", "CHEF_CCFM", "AGENT_CCFM"])),
):
    svc = _get_service(db)
    try:
        res = await svc.generer_rapport_appreciation(payload.ccfm_id)
        return {
            "ccfm_id": res["ccfm_id"],
            "verdict_global": res["verdict"],
            "rapport_hash": res["rapport_hash"],
            "message": "Rapport soumis au directeur pour signature",
        }
    except Exception as e:
        _handle_error(e, "generer_rapport_appreciation")

@router.post("/demandes/{nus}/signer")
async def signer_ccfm(
    nus: str,
    payload: SignerIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_DIRECTEUR)),
):
    cid = await _get_ccfm_id_by_nus(db, nus)
    svc = _get_service(db)
    try:
        res = await svc.valider_et_signer({
            "ccfm_id": cid, "approuve": True, "observations": payload.observations
        }, directeur_id=str(current_user.id))
        return {
            "nus": nus, "reference_ccfm": res.get("reference_ccfm", f"CCFM/{nus}"),
            "author": payload.directeur_nom, "date_signature": "NOW",
            "etat": res["etat"], "hash_ccfm": res["hash"], "qr_code_url": res["qr_code"],
            "signe_par": payload.directeur_nom,
            "message": "Certificat signe -- PDF A5 en generation",
        }
    except Exception as e:
        _handle_error(e, "signer_ccfm")

@router.post("/demandes/{nus}/archiver", status_code=201)
async def archiver_ccfm(
    nus: str,
    payload: ArchiverIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ARCHIVISTE)),
):
    cid = await _get_ccfm_id_by_nus(db, nus)
    svc = _get_service(db)
    try:
        await svc.preparer_retrait(cid, archiviste_id=str(current_user.id))
        res = await svc.archiver(cid, archiviste_id=str(current_user.id))
        return {
            "nus": nus, "etat": "RETRAIT_EN_ATTENTE",
            "annf_code": res["annf_code"], "tx_blockchain": res["tx"],
            "message": "Archive dans l ANNF -- pret pour retrait beneficiaire",
        }
    except Exception as e:
        _handle_error(e, "archiver_ccfm")

@router.get("/verifier/{nus}", response_model=dict)
async def verifier_par_nus(
    nus: str,
    db: AsyncSession = Depends(get_db),
):
    svc = _get_service(db)
    try:
        res = await svc.verifier_ccfm(nus=nus)
        if not res.get("valide"):
            raise HTTPException(404, res.get("message", "CCFM non valide ou introuvable"))
        return res
    except Exception as e:
        _handle_error(e, "verifier_par_nus")

@router.get("/verifier-qr", response_model=dict)
async def verifier_par_qr(
    qr_code: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    svc = _get_service(db)
    try:
        nus = qr_code.split("|")[1] if "|" in qr_code else qr_code
        res = await svc.verifier_ccfm(nus=nus)
        if not res.get("valide"):
            raise HTTPException(400, res.get("message", "CCFM non valide"))
        return res
    except Exception as e:
        _handle_error(e, "verifier_par_qr")

@router.post("/verifier-parcelle")
async def verifier_par_parcelle(
    parcelle_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    svc = _get_service(db)
    try:
        return await svc.verifier_ccfm(parcelle=parcelle_id)
    except Exception as e:
        _handle_error(e, "verifier_par_parcelle")

@router.get("/ccfm-gate/{parcelle_id}", response_model=dict)
async def ccfm_gate(
    parcelle_id: str,
    action: str = Query("operation"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_LECTURE)),
):
    svc = _get_service(db)
    try:
        res = await svc.verifier_ccfm(parcelle=parcelle_id)
        if not res.get("valide"):
            raise HTTPException(422, {
                "code": "CCFM_GATE_FAILED", "message": "Aucun CCFM valide trouve pour cette parcelle",
                "correction": "POST /v1/ccfm/demandes",
            })
        return {"gate_ok": True, "parcelle_id": parcelle_id, "message": "CCFM Valide"}
    except Exception as e:
        _handle_error(e, "ccfm_gate")

@router.get("/tableau-de-bord", response_model=dict)
async def tableau_de_bord(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_LECTURE)),
):
    from app.api.v1.endpoints.ccfm_v3 import get_dashboard
    return await get_dashboard(db, current_user)

@router.get("/demandes", response_model=list)
async def lister_demandes(
    etat: Optional[str]    = Query(None),
    localite: Optional[str] = Query(None),
    page: int              = Query(1,  ge=1),
    limit: int             = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_LECTURE)),
):
    from app.api.v1.endpoints.ccfm_v3 import lister_demandes as _list_v3
    return await _list_v3(etat=etat, localite=localite, page=page, limit=limit, db=db, current_user=current_user)

@router.get("/demandes/{nus}", response_model=dict)
async def get_demande(
    nus: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_LECTURE)),
):
    cid = await _get_ccfm_id_by_nus(db, nus)
    from app.api.v1.endpoints.ccfm_v3 import get_demande as _get_v3
    return await _get_v3(ccfm_id=cid, db=db, user=current_user)

@router.get("/dar/archives", response_model=list)
async def archives_dar(
    page: int  = Query(1,  ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_LECTURE + ["ARCHIVISTE_ANNF"])),
):
    """Archives DAR du module CCFM."""
    try:
        r = await db.execute(text("""
            SELECT id, ida, type_archive, entite_id, sha256_snapshot, scelle, created_at
            FROM annf_archive_links WHERE entite_table='demandes_ccfm'
            ORDER BY created_at DESC LIMIT :l OFFSET :o
        """), {"l": limit, "o": (page - 1) * limit})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("dar_archives_ccfm: %s", e)
        return []


@router.post("/dar/archiver", status_code=201)
async def archiver_dar(
    entite_id:    str = Query(...),
    entite_table: str = Query(default="demandes_ccfm"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ARCHIVISTE)),
):
    """Archive un document CCFM vers l ANNF national."""
    try:
        from app.services.workflow_orchestrator import ANNFArchiveService
        from app.models.workflows import TypeArchiveANNF
        archive = await ANNFArchiveService.archiver(
            db=db, type_archive=TypeArchiveANNF.CCFM,
            entite_id=entite_id, entite_table=entite_table,
            snapshot={"module": "ccfm"},
            archive_par_id=str(current_user.id),
        )
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"ida": archive.ida if hasattr(archive, "ida") else None, "module": "ccfm"}


@router.get("/dar/integrite", response_model=dict)
async def integrite_dar(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_LECTURE + ["AUDITEUR"])),
):
    """Integrite des archives CCFM dans l ANNF."""
    try:
        r = await db.execute(text("""
            SELECT COUNT(*) FILTER (WHERE scelle=true)  AS nb_scelles,
                   COUNT(*) FILTER (WHERE scelle=false) AS nb_en_attente,
                   COUNT(*)                             AS nb_total
            FROM annf_archive_links WHERE entite_table='demandes_ccfm'
        """))
        row = r.mappings().first()
        return dict(row) if row else {"nb_scelles": 0, "nb_en_attente": 0, "nb_total": 0}
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("integrite_dar_ccfm: %s", e)
        return {"nb_scelles": 0, "nb_en_attente": 0, "nb_total": 0}
