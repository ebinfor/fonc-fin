"""
FONCIER+ v109 — Routes CCFM v3 (adapté architecture v108)
Certificat de Conformité Foncière Mixte

Préfixe : /api/v3/ccfm
Auth    : JWT via get_current_user (security.py v108)
DB      : SQLAlchemy async (database.py v108)
         + AsyncpgCompat adapter pour ccfm_service.py (asyncpg natif)

Workflow 7 étapes :
  1. POST /demandes                        → Guichetière — enregistrement + PDF A4
  2. POST /demandes/{id}/verification-auto → Système     — RNAF + BGU → Ticket
  3. POST /demandes/{id}/ordre-mission     → Secrétaire  — ordre + ticket joint
  4a.POST /demandes/{id}/demarrer-visite   → Topographe  — début mission
  4b.POST /demandes/{id}/fiche-constat     → Topographe  — fiche vérification
  5. POST /demandes/{id}/rapport-appreciation → Système  — rapport directeur
  6. POST /demandes/{id}/valider           → Directeur   — signature + PDF A5
  7a.POST /demandes/{id}/preparer-retrait  → Archiviste  — préparer retrait
  7b.POST /demandes/{id}/archiver          → Archiviste  — ANNF + blockchain
  7c.POST /demandes/{id}/marquer-retrait   → Archiviste  — retrait confirmé
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from pydantic import BaseModel, Field

logger = logging.getLogger("foncier.ccfm_v3")

router = APIRouter(
    prefix="/api/v3/ccfm",
    tags=["CCFM — Certificat de Conformité Foncière Mixte"],
)

# Rôles CCFM
_GUICHETIERE  = ["ADMIN", "AGENT_CCFM", "GUICHETIER_CCFM"]
_SECRETAIRE   = ["ADMIN", "AGENT_CCFM", "CHEF_CCFM", "SECRETAIRE_CCFM"]
_TOPOGRAPHE   = ["ADMIN", "TOPOGRAPHE", "GEOMETRE"]
_DIRECTEUR    = ["ADMIN", "DIRECTEUR_URBANISME", "SECRETAIRE_GENERAL"]
_ARCHIVISTE   = ["ADMIN", "ARCHIVISTE_ANNF", "RESPONSABLE_ANNF"]
_SYSTEM       = ["ADMIN"]
_ALL          = ["ADMIN", "AGENT_CCFM", "CHEF_CCFM", "TOPOGRAPHE", "GEOMETRE",
                 "DIRECTEUR_URBANISME", "SECRETAIRE_GENERAL", "ARCHIVISTE_ANNF",
                 "RESPONSABLE_ANNF", "NOTAIRE", "BANQ_AGENT", "AUDITEUR"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Adaptateur SQLAlchemy → interface asyncpg pour CCFMWorkflowService
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class _AsyncpgCompat:
    """
    Adaptateur minimal qui expose pool.acquire() compatible asyncpg
    depuis une session SQLAlchemy async.

    CCFMWorkflowService utilise :
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(SQL, *params)
            rows = await conn.fetch(SQL, *params)
            await conn.execute(SQL, *params)

    Cet adaptateur traduit vers SQLAlchemy text() + mappings().
    """
    def __init__(self, db: AsyncSession):
        self._db = db

    def acquire(self):
        return _ConnCompat(self._db)


class _ConnCompat:
    """Context manager émulant asyncpg.Connection."""
    def __init__(self, db: AsyncSession):
        self._db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def fetchrow(self, sql: str, *args) -> Optional[dict]:
        from sqlalchemy import text
        # Convertir positional $1,$2 → :p1,:p2 (SQLAlchemy)
        q, params = _convert_sql(sql, args)
        r = await self._db.execute(text(q), params)
        row = r.mappings().first()
        return dict(row) if row else None

    async def fetch(self, sql: str, *args) -> list:
        from sqlalchemy import text
        q, params = _convert_sql(sql, args)
        r = await self._db.execute(text(q), params)
        return [dict(row) for row in r.mappings().all()]

    async def execute(self, sql: str, *args) -> None:
        from sqlalchemy import text
        q, params = _convert_sql(sql, args)
        await self._db.execute(text(q), params)


def _convert_sql(sql: str, args: tuple) -> tuple[str, dict]:
    """Convertit $1,$2… → :p1,:p2… et crée le dict params."""
    params = {}
    result = sql
    for i, val in enumerate(args, 1):
        result = result.replace(f"${i}", f":p{i}")
        params[f"p{i}"] = val
    return result, params


def _get_service(db: AsyncSession) -> "CCFMWorkflowService":
    from app.services.ccfm.ccfm_service import CCFMWorkflowService
    pool_compat = _AsyncpgCompat(db)
    return CCFMWorkflowService(pool=pool_compat, cache=None)


def _user_id(user: dict) -> str:
    return str(user.get("id", ""))


def _handle_error(e: Exception, ctx: str = "") -> None:
    if isinstance(e, ValueError):
        raise HTTPException(400, str(e))
    if isinstance(e, PermissionError):
        raise HTTPException(403, str(e))
    logger.error("CCFM %s: %s", ctx, e, exc_info=True)
    raise HTTPException(500, f"Erreur interne : {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ÉTAPE 1 — ENREGISTREMENT DEMANDE (Guichetière)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


# ════════════════════════════════════════════════════════════════
# SCHEMAS PYDANTIC — ccfm_v3 (P0-2)
# ════════════════════════════════════════════════════════════════

class VerificationAutoIn(BaseModel):
    """Étape 2 — Déclencher la vérification automatique RNAF+BGU+GPS.
    Lance la vérification de conformité : RNAF publié au JO + BGU scellée
    + cohérence coordonnées GPS. Produit un ticket hash SHA-256.
    """
    force_refresh: bool = Field(default=False,
        description="Forcer recalcul même si ticket valide < 24h")
    timeout_sec:   int  = Field(default=30, ge=5, le=120,
        description="Timeout de la vérification automatique en secondes")

class DemarrerVisiteIn(BaseModel):
    """Étape 4a — Topographe : marquer le début de la visite terrain."""
    notes_depart:   Optional[str]   = Field(None, min_length=3,
        description="Notes de départ — min 3 caractères")
    gps_depart_lat: Optional[float] = Field(None, ge=-90,  le=90,
        description="Latitude GPS départ — WGS84")
    gps_depart_lon: Optional[float] = Field(None, ge=-180, le=180,
        description="Longitude GPS départ — WGS84")

class FicheConstatV3In(BaseModel):
    superficie_mesuree_m2: float          = Field(..., gt=0)
    gps_mesure_latitude:   Optional[float] = None
    gps_mesure_longitude:  Optional[float] = None
    concordance_gps:       Optional[bool]  = None
    jo_publie:             bool            = False
    arrete_numero:         Optional[str]   = None
    arrete_autorite:       Optional[str]   = None
    titre_foncier_numero:  Optional[str]   = None
    titre_authentique:     Optional[bool]  = None
    decision:              str             = Field(..., min_length=1)
    motif_refus:           Optional[str]   = None
    observations:          Optional[str]   = None
    topographe_nom:        str             = Field(..., min_length=3)
    photos_urls:           list            = Field(default_factory=list)

class RapportAppreciationV3In(BaseModel):
    verdict_global:      str  = Field(..., min_length=1)
    points_conformite:   list = Field(default_factory=list)
    anomalies_detectees: list = Field(default_factory=list)
    commentaire_auto:    Optional[str] = None

class PreparerRetraitV3In(BaseModel):
    """Étape 8 — Notifier le bénéficiaire pour retrait du certificat."""
    canal_notification: str           = Field(default="SMS",
        pattern="^(SMS|EMAIL|GUICHET)$")
    telephone_contact:  Optional[str] = Field(None, min_length=8, max_length=20)
    email_contact:      Optional[str] = Field(None, min_length=5)
    message_custom:     Optional[str] = Field(None, min_length=10)

class ArchiverV3In(BaseModel):
    """Étape 7 — Archiviste : sceller le certificat dans l'ANNF."""
    observations:        Optional[str] = Field(None, min_length=5)
    confirmer_integrite: bool          = Field(default=True,
        description="Vérifier le hash SHA-256 du PDF avant archivage")

class MarquerRetraitV3In(BaseModel):
    """Fin de workflow — Confirmer le retrait physique du certificat."""
    mode_retrait:             str           = Field(default="PRESENTIEL",
        pattern="^(PRESENTIEL|COURRIER)$")
    piece_identite_presentee: Optional[str] = Field(None, min_length=3,
        description="Type et numéro de la pièce d'identité")
    agent_remettant:          Optional[str] = Field(None, min_length=3)
    observations:             Optional[str] = Field(None, min_length=5)

class DemandeCCFMOut(BaseModel):
    """Schema de sortie minimal pour les listes CCFM."""
    ccfm_id:           str
    nus:               str
    nom_prenom:        str
    localite:          str
    lot:               str
    parcelle:          str
    etat:              str
    date_enregistrement: Optional[str] = None
    topographe_assigne: Optional[str] = None

    class Config:
        from_attributes = True


@router.post("/demandes", status_code=201)
async def enregistrer_demande(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(_GUICHETIERE)),
):
    """
    Étape 1 — Guichetière enregistre la demande CCFM.
    Paiement 50 000 FCFA. NUS généré automatiquement.
    Génère le formulaire PDF A4 (remis au demandeur).
    """
    svc = _get_service(db)
    try:
        result = await svc.enregistrer_demande(payload, guichetiere_id=_user_id(user))
        return result
    except Exception as e:
        _handle_error(e, "enregistrer_demande")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ÉTAPE 2 — VÉRIFICATION AUTO RNAF + BGU (Système)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/demandes/{ccfm_id}/verification-auto")
async def lancer_verification_auto(
    ccfm_id: UUID,
    payload: VerificationAutoIn = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(_SYSTEM + _SECRETAIRE)),
):
    """
    Étape 2 — Vérification automatique RNAF + BGU.
    Génère le ticket de réponse avec coordonnées GPS.
    → TICKET_EMIS ou VERIFICATION_AUTO_KO
    """
    svc = _get_service(db)
    try:
        return await svc.lancer_verification_auto(str(ccfm_id))
    except Exception as e:
        _handle_error(e, "verification_auto")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ÉTAPE 3 — ORDRE DE MISSION (Secrétaire)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/demandes/{ccfm_id}/ordre-mission")
async def emettre_ordre_mission(
    ccfm_id: UUID,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(_SECRETAIRE)),
):
    """
    Étape 3 — Secrétaire émet l'ordre de mission.
    Le ticket RNAF+BGU est joint automatiquement.
    Topographe et Huissier assignés.
    """
    svc = _get_service(db)
    try:
        data = dict(payload) if payload else {}
        data["ccfm_id"] = str(ccfm_id)
        return await svc.emettre_ordre_mission(
            data, secretaire_id=_user_id(user)
        )
    except Exception as e:
        _handle_error(e, "ordre_mission")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ÉTAPE 4a — DÉMARRAGE VISITE TERRAIN (Topographe)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/demandes/{ccfm_id}/demarrer-visite", status_code=201)
async def demarrer_visite(
    ccfm_id: UUID,
    payload: DemarrerVisiteIn = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(_TOPOGRAPHE)),
):
    """Étape 4a — Topographe démarre la visite terrain. → VISITE_EN_COURS"""
    svc = _get_service(db)
    try:
        return await svc.demarrer_visite(str(ccfm_id), topographe_id=_user_id(user))
    except Exception as e:
        _handle_error(e, "demarrer_visite")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ÉTAPE 4b — FICHE DE CONSTAT / VÉRIFICATION ADMIN (Topographe)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/demandes/{ccfm_id}/fiche-constat", status_code=201)
async def deposer_fiche_constat(
    ccfm_id: UUID,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(_TOPOGRAPHE)),
):
    """
    Étape 4b — Topographe dépose la Fiche de Vérification Administrative Foncière.
    Contient : mesures terrain + 8 sections administratives (§1-§8).
    → FICHE_CONSTAT_DEPOSEE
    """
    svc = _get_service(db)
    try:
        data = dict(payload) if payload else {}
        data["ccfm_id"] = str(ccfm_id)
        data["topographe_id"] = _user_id(user)
        return await svc.deposer_fiche_constat(data)
    except Exception as e:
        _handle_error(e, "fiche_constat")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ÉTAPE 5 — RAPPORT D'APPRÉCIATION (Système automatique)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/demandes/{ccfm_id}/rapport-appreciation")
async def generer_rapport_appreciation(
    ccfm_id: UUID,
    payload: RapportAppreciationV3In = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(_SYSTEM + _DIRECTEUR)),
):
    """
    Étape 5 — Système compile ticket + fiche → rapport d'appréciation.
    Auto-transition vers ATTENTE_SIGNATURE_DIRECTEUR.
    """
    svc = _get_service(db)
    try:
        return await svc.generer_rapport_appreciation(str(ccfm_id))
    except Exception as e:
        _handle_error(e, "rapport_appreciation")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ÉTAPE 6 — VALIDATION ET SIGNATURE DIRECTEUR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/demandes/{ccfm_id}/valider")
async def valider_et_signer(
    ccfm_id: UUID,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(_DIRECTEUR)),
):
    """
    Étape 6 — Directeur consulte le rapport et signe le certificat.
    Génère le PDF A5 officiel + QR code de vérification.
    → SIGNE_DIRECTEUR
    """
    svc = _get_service(db)
    try:
        data = dict(payload) if payload else {}
        data["ccfm_id"] = str(ccfm_id)
        return await svc.valider_et_signer(
            data, directeur_id=_user_id(user)
        )
    except Exception as e:
        _handle_error(e, "valider_et_signer")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ÉTAPE 7 — ARCHIVAGE (Archiviste)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/demandes/{ccfm_id}/preparer-retrait")
async def preparer_retrait(
    ccfm_id: UUID,
    payload: PreparerRetraitV3In = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(_ARCHIVISTE)),
):
    """Étape 7a — Archiviste prépare le retrait physique du certificat. → RETRAIT_EN_ATTENTE"""
    svc = _get_service(db)
    try:
        return await svc.preparer_retrait(str(ccfm_id), archiviste_id=_user_id(user))
    except Exception as e:
        _handle_error(e, "preparer_retrait")


@router.post("/demandes/{ccfm_id}/archiver")
async def archiver(
    ccfm_id: UUID,
    payload: ArchiverV3In = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(_ARCHIVISTE)),
):
    """Étape 7b — Archivage ANNF + hash blockchain. → ARCHIVE"""
    svc = _get_service(db)
    try:
        return await svc.archiver(str(ccfm_id), archiviste_id=_user_id(user))
    except Exception as e:
        _handle_error(e, "archiver")


@router.post("/demandes/{ccfm_id}/marquer-retrait")
async def marquer_retrait(
    ccfm_id: UUID,
    payload: MarquerRetraitV3In = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(_ARCHIVISTE)),
):
    """Étape 7c — Archiviste confirme le retrait physique du certificat. → RETIRE"""
    svc = _get_service(db)
    try:
        return await svc.marquer_retrait(str(ccfm_id))
    except Exception as e:
        _handle_error(e, "marquer_retrait")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONSULTATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/demandes", response_model=list,
    summary="Lister les demandes CCFM filtrees par acteur")
async def lister_demandes(
    etat:      Optional[str] = Query(None),
    localite:  Optional[str] = Query(None),
    search:    Optional[str] = Query(None),
    page:      int            = Query(1, ge=1),
    limit:     int            = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Liste les demandes CCFM filtrées par acteur :
      Guichetiere  -> ses propres enregistrements (DEMANDE_RECUE...)
      Secretaire   -> demandes avec ticket emis (TICKET_EMIS...)
      Topographe   -> demandes qui lui sont assignees
      Directeur    -> demandes en attente de signature
      Archiviste   -> demandes signees a archiver
      Admin/Chef   -> tout
    """
    from app.core.scope_filter import ScopeFilter
    scope = ScopeFilter.from_user(current_user)
    role  = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", "")

    # Filtre d etat par role si aucun etat specifie
    etat_defaut = {
        "GUICHETIER_CCFM": "DEMANDE_RECUE,VERIFICATION_AUTO,TICKET_EMIS,VERIFICATION_AUTO_KO",
        "SECRETAIRE_CCFM": "TICKET_EMIS,ORDRE_MISSION_EMIS,ATTENTE_VISITE_TERRAIN",
        "TOPOGRAPHE":      "ORDRE_MISSION_EMIS,ATTENTE_VISITE_TERRAIN,VISITE_EN_COURS,FICHE_CONSTAT_DEPOSEE",
        "GEOMETRE":        "ORDRE_MISSION_EMIS,ATTENTE_VISITE_TERRAIN,VISITE_EN_COURS,FICHE_CONSTAT_DEPOSEE",
        "DIRECTEUR_URBANISME": "ATTENTE_SIGNATURE_DIRECTEUR,RAPPORT_APPRECIATION",
        "ARCHIVISTE_ANNF": "SIGNE_DIRECTEUR,RETRAIT_EN_ATTENTE,ARCHIVE,RETIRE",
    }
    if not etat and role in etat_defaut:
        etat = etat_defaut[role]

    where  = "WHERE 1=1"
    params: dict = {"l": limit, "o": (page - 1) * limit}

    if etat:
        etats = [e.strip() for e in etat.split(",")]
        placeholders = ",".join(f":e{i}" for i in range(len(etats)))
        where += f" AND c.etat::TEXT IN ({placeholders})"
        for i, e in enumerate(etats):
            params[f"e{i}"] = e

    if localite:
        where += " AND c.localite = :loc"
        params["loc"] = localite

    if search:
        where += " AND (c.nus ILIKE :s OR c.nom_prenom ILIKE :s OR c.nip_passeport ILIKE :s)"
        params["s"] = f"%{search}%"

    # Filtre par acteur
    if not scope.peut_voir_national:
        uid = scope.user_id
        if role == "GUICHETIER_CCFM":
            where += " AND c.enregistre_par = :_scope_agent"
            params["_scope_agent"] = uid
        elif role in ("TOPOGRAPHE", "GEOMETRE"):
            where += " AND c.topographe_assigne = :_scope_topo"
            params["_scope_topo"] = uid
        elif role == "ARCHIVISTE_ANNF":
            where += " AND c.etat::TEXT IN ('SIGNE_DIRECTEUR','RETRAIT_EN_ATTENTE','ARCHIVE','RETIRE')"
        elif scope.region:
            where += " AND c.localite = :_scope_region"
            params["_scope_region"] = scope.region

    try:
        r = await db.execute(text(f"""
            SELECT c.ccfm_id, c.nus, c.reference_ccfm, c.nom_prenom,
                   c.localite, c.lot, c.parcelle, c.superficie_m2,
                   c.etat::TEXT, c.date_enregistrement, c.date_signature,
                   c.topographe_assigne, c.enregistre_par
            FROM demandes_ccfm c
            {where}
            ORDER BY c.date_enregistrement DESC
            LIMIT :l OFFSET :o
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        try:
            await conn.execute("ROLLBACK")
        except Exception: pass
        import logging as _l
        _l.getLogger("ccfm_v3").warning("lister_demandes: %s", e)
        return []


@router.get("/demandes/{ccfm_id}", response_model=dict,
    summary="Detail d une demande CCFM")
async def get_demande(
    ccfm_id: UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(_ALL)),
):
    """Détail complet d'une demande : fiche + rapport + historique."""
    svc = _get_service(db)
    try:
        result = await svc.get_demande(str(ccfm_id))
        if not result:
            raise HTTPException(404, f"Demande CCFM {ccfm_id} introuvable")
        return result
    except HTTPException:
        raise
    except Exception as e:
        _handle_error(e, "get_demande")


@router.get("/demandes/{ccfm_id}/pdf", response_model=dict)
async def telecharger_pdf(
    ccfm_id: UUID,
    type: str = Query("certificat", regex="^(certificat|demande)$"),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(_ALL)),
):
    """
    Télécharge le PDF :
    - type=certificat → PDF A5 signé (généré étape 6)
    - type=demande    → Formulaire A4 (généré étape 1)
    """
    svc = _get_service(db)
    try:
        row = await svc.get_demande(str(ccfm_id))
        if not row:
            raise HTTPException(404, "Demande introuvable")
        field  = "pdf_url" if type == "certificat" else "pdf_demande_url"
        path   = row.get(field)
        if not path:
            raise HTTPException(404, f"PDF {type} non encore généré")
        import os
        if not os.path.exists(path):
            raise HTTPException(404, "Fichier PDF non trouvé sur le serveur")
        nus = row.get("nus", str(ccfm_id))
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=f"CCFM_{nus.replace('/', '-')}_{type}.pdf",
        )
    except HTTPException:
        raise
    except Exception as e:
        _handle_error(e, "telecharger_pdf")


@router.post("/demandes/{ccfm_id}/regenerer-pdf")
async def regenerer_pdf(
    ccfm_id: UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(_ARCHIVISTE + _DIRECTEUR)),
):
    """Régénère le PDF sans changer l'état du workflow."""
    svc = _get_service(db)
    try:
        return await svc.regenerer_pdf(str(ccfm_id))
    except Exception as e:
        _handle_error(e, "regenerer_pdf")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VÉRIFICATION INTER-MODULES (publique — gate Notaire/Banque/Justice)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/verifier", response_model=list)
async def verifier_ccfm_get(
    nus:      Optional[str] = Query(None),
    lot:      Optional[str] = Query(None),
    parcelle: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Gate publique (sans auth) — vérification inter-modules.
    Utilisé par : Notaire, Banque, Justice avant tout acte foncier.
    Retourne : { valide, statut, ccfm_id, reference_ccfm, date_signature }
    """
    if not nus and not (lot and parcelle):
        raise HTTPException(400, "Fournir 'nus' ou ('lot' + 'parcelle')")
    svc = _get_service(db)
    try:
        return await svc.verifier_ccfm(nus=nus, lot=lot, parcelle=parcelle)
    except Exception as e:
        _handle_error(e, "verifier_ccfm")


@router.post("/verifier")
async def verifier_ccfm_post(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """Variante POST de la vérification (body JSON)."""
    svc = _get_service(db)
    try:
        return await svc.verifier_ccfm(
            nus=payload.get("nus"),
            lot=payload.get("lot"),
            parcelle=payload.get("parcelle"),
        )
    except Exception as e:
        _handle_error(e, "verifier_ccfm_post")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# WORKSPACES PAR RÔLE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/workspaces/{role_slug}", response_model=dict)
async def get_workspace(
    role_slug: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Vue workspace filtrée par rôle.
    role_slug : guichetiere | secretaire | topographe | directeur | archiviste
    """
    ROLE_MAP = {
        "guichetiere": _GUICHETIERE,
        "secretaire":  _SECRETAIRE,
        "topographe":  _TOPOGRAPHE,
        "directeur":   _DIRECTEUR,
        "archiviste":  _ARCHIVISTE,
    }
    if role_slug not in ROLE_MAP:
        raise HTTPException(400, f"Rôle inconnu : {role_slug}")
    svc = _get_service(db)
    try:
        return await svc.get_workspace(role_slug, user_id=_user_id(user))
    except Exception as e:
        _handle_error(e, f"workspace_{role_slug}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MONITORING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/stats", response_model=list)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(_ALL)),
):
    """Statistiques globales : compteurs par état, délais moyens, taux de conformité."""
    svc = _get_service(db)
    try:
        return await svc.get_stats()
    except Exception as e:
        _handle_error(e, "get_stats")


@router.get("/dashboard", response_model=dict,
    summary="Tableau de bord CCFM filtre par acteur")
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Tableau de bord CCFM : KPIs filtrés selon le role de l acteur."""
    from app.core.scope_filter import ScopeFilter
    scope = ScopeFilter.from_user(current_user)
    role  = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", "")
    uid   = scope.user_id

    # Clause WHERE additionnelle selon l acteur
    extra = ""
    params: dict = {}
    if not scope.peut_voir_national:
        if role == "GUICHETIER_CCFM":
            extra  = "WHERE enregistre_par = :uid"
            params["uid"] = uid
        elif role in ("TOPOGRAPHE", "GEOMETRE"):
            extra  = "WHERE topographe_assigne = :uid"
            params["uid"] = uid
        elif role == "ARCHIVISTE_ANNF":
            extra  = "WHERE etat::TEXT IN ('SIGNE_DIRECTEUR','RETRAIT_EN_ATTENTE','ARCHIVE','RETIRE')"
        elif role in ("DIRECTEUR_URBANISME", "CHEF_CCFM"):
            extra  = "WHERE etat::TEXT IN ('ATTENTE_SIGNATURE_DIRECTEUR','RAPPORT_APPRECIATION','SIGNE_DIRECTEUR')"
        elif scope.region:
            extra  = "WHERE localite = :region"
            params["region"] = scope.region

    stats = {}
    for key, sql in {
        "total":       f"SELECT COUNT(*) FROM demandes_ccfm {extra}",
        "en_cours":    f"SELECT COUNT(*) FROM demandes_ccfm {extra + (' AND' if extra else 'WHERE')} etat::TEXT NOT IN ('ARCHIVE','RETIRE','REJETE')" if extra else
                       "SELECT COUNT(*) FROM demandes_ccfm WHERE etat::TEXT NOT IN ('ARCHIVE','RETIRE','REJETE')",
        "signes":      f"SELECT COUNT(*) FROM demandes_ccfm {extra + (' AND' if extra else 'WHERE')} etat::TEXT IN ('SIGNE_DIRECTEUR','ARCHIVE','RETRAIT_EN_ATTENTE','RETIRE')" if extra else
                       "SELECT COUNT(*) FROM demandes_ccfm WHERE etat::TEXT IN ('SIGNE_DIRECTEUR','ARCHIVE','RETRAIT_EN_ATTENTE','RETIRE')",
        "rejetes":     f"SELECT COUNT(*) FROM demandes_ccfm {extra + (' AND' if extra else 'WHERE')} etat::TEXT='REJETE'" if extra else
                       "SELECT COUNT(*) FROM demandes_ccfm WHERE etat::TEXT='REJETE'",
    }.items():
        try:
            r = await db.execute(text(sql), params)
            stats[key] = r.scalar() or 0
        except Exception:
            stats[key] = 0

    return {"stats": stats, "role": role, "scope": scope.filtres_actifs()}


@router.get("/health", response_model=list)
async def health_check(db: AsyncSession = Depends(get_db)):
    """Health check CCFM : DB + Redis."""
    try:
        from sqlalchemy import text
        await db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok", "module": "ccfm_v3"}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "db": str(e)[:80]}
        )
