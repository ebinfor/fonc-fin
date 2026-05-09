from app.services.workflow_orchestrator import ANNFArchiveService
"""
FONCIER+ — Module Domaine COMPLET (Gestion du Domaine Foncier Public)
Module indépendant — 100 % de couverture des 22 tables migration 012.

WF11 — Expropriation pour utilité publique
WF12 — Régularisation foncière
WF13 — Gestion des conflits fonciers
WF14 — Lotissement étendu (réserves, attribution lots, financement)
WF15 — Zones spéciales (création, règles, infractions, contrôles)
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user

router = APIRouter(prefix="/domaine", tags=["Domaine — Foncier public"])

ROLES_DOM   = ["ADMIN", "DIRECTEUR_DOMAINE", "AGENT_DOMAINE"]
ROLES_DIR   = ["ADMIN", "DIRECTEUR_DOMAINE", "AGENT_DOMAINE"]
ROLES_LARGE = ["ADMIN", "DIRECTEUR_DOMAINE", "AGENT_DOMAINE",
               "DIRECTEUR_URBANISME", "AUDITEUR"]


# ══════════════════════════════════════════════════════════════════
# SCHÉMAS
# ══════════════════════════════════════════════════════════════════

# WF11
class ProjetPublicIn(BaseModel):
    nom_projet: str = Field(..., min_length=3)
    type_projet: str = Field(...,
        description="route|barrage|ecole|hopital|infrastructure_autre")
    arrete_dut_ref: str
    date_dut: datetime
    budget_fcfa: Optional[float] = None
    date_fin_prevue: Optional[datetime] = None

class ExproprierIn(BaseModel):
    projet_id: str
    parcelle_id: str
    proprietaire_id: str
    surface_expropriee_m2: float = Field(..., gt=0)
    est_partielle: bool = False

class EvaluationIn(BaseModel):
    valeur_officielle_fcfa: float = Field(..., gt=0)
    valeur_marche_fcfa: Optional[float] = Field(None, gt=0,
        description='Valeur de marché en FCFA — doit être > 0')
    indemnite_proposee_fcfa: float = Field(..., gt=0)
    expert_evaluateur: str

class PaiementIn(BaseModel):
    evaluation_id: str
    montant_verse_fcfa: float = Field(..., gt=0)
    mode_paiement: str = Field(..., description="virement|cheque|numeraire")
    reference_paiement: str

class ContestationExpIn(BaseModel):
    contestataire_id: str
    motif: str = Field(..., min_length=10)
    type_contestation: str = Field(...,
        description="indemnisation_insuffisante|utilite_non_prouvee|procedure_irreguliere")

# WF12
class DemandeRegIn(BaseModel):
    occupant_id: str
    surface_m2: float = Field(..., gt=0)
    date_occupation: Optional[datetime] = None
    geom_wkt: Optional[str] = None

class EnqueteIn(BaseModel):
    """Enquête terrain sur une régularisation."""
    date_enquete:       datetime = Field(...,
        description="Date de l'enquête")
    occupation_confirmee: bool   = Field(...,
        description="L'occupation est-elle confirmée sur le terrain ?")
    conflit_detecte:    bool     = Field(default=False)
    observations:       Optional[str] = Field(None, min_length=5)
    recommandation:     str      = Field(...,
        pattern="^(valider|rejeter|approfondir)$")

class ArreteRegIn(BaseModel):
    """Arrêté de régularisation foncière."""
    numero_arrete: str      = Field(..., min_length=3,
        description="Numéro officiel de l'arrêté de régularisation")
    date_arrete:   datetime = Field(...,
        description="Date de signature de l'arrêté")

class AttributionPropIn(BaseModel):
    """Attribution d'une propriété régularisée."""
    arrete_reg_id:   str = Field(..., min_length=3,
        description="UUID de l'arrêté de régularisation")
    beneficiaire_id: str = Field(..., min_length=3,
        description="UUID du bénéficiaire (right_holder)")
    parcelle_id:     str = Field(..., min_length=3,
        description="UUID de la parcelle attribuée")

class ConflitFoncierIn(BaseModel):
    type_conflit: str = Field(...,
        description="limite_parcelle|propriete_disputee|heritage_conteste|"
                    "occupation_illegale|fraude_cadastrale|litige_usage")
    description: str = Field(..., min_length=10)
    autorite_saisie: str = Field(default="domaine",
        description="ccfm|justice|domaine|commune")
    parcelles_ids: List[str] = Field(default=[],
        description="UUIDs des parcelles impliquées")

class MediationIn(BaseModel):
    """Résultat d'une médiation foncière."""
    date_mediation:  datetime     = Field(...,
        description="Date de la séance de médiation")
    accord_trouve:   bool         = Field(default=False,
        description="Un accord a-t-il été trouvé ?")
    termes_accord:   Optional[str] = Field(None, min_length=10,
        description="Termes de l'accord — min 10 caractères si accord trouvé")

class DecisionConflitIn(BaseModel):
    type_decision: str = Field(...,
        description="mediation_reussie|jugement|classement|accord_amiable")
    dispositif: str = Field(..., min_length=10)
    beneficiaire_id: Optional[str] = None
    gel_leve: bool = False

# WF14
class EtudeTechniqueIn(BaseModel):
    lotissement_id: str
    bureau_etudes: str
    surface_nette_m2: Optional[float] = Field(None, ge=0,
        description='Surface en m² — doit être >= 0')
    surface_voirie_m2: Optional[float] = Field(None, ge=0,
        description='Surface en m² — doit être >= 0')
    surface_equipements_m2: Optional[float] = Field(None, ge=0,
        description='Surface en m² — doit être >= 0')
    densite_prevue: Optional[int] = None

class ReservePubliqueIn(BaseModel):
    lotissement_id: str
    type_reserve: str = Field(...,
        description="voirie|ecole|sante|marche|mosquee|espace_vert|autre")
    surface_m2: float = Field(..., gt=0)
    gestionnaire: Optional[str] = None
    geom_wkt: Optional[str] = None

class AttributionLotIn(BaseModel):
    lot_id: str
    beneficiaire_id: str
    acte_communal_ref: str
    prix_fcfa: Optional[float] = Field(None, ge=0,
        description='Prix d attribution en FCFA — 0 si gratuit')
    paiement_ref: Optional[str] = None

class FinancementIn(BaseModel):
    lotissement_id: str
    bailleur: str
    type_financement: str = Field(...,
        description="budget_etat|pret_bancaire|fonds_propres|aide_internationale")
    montant_fcfa: float = Field(..., gt=0)
    date_accord: Optional[datetime] = None

# WF15
class ZoneSpecialeIn(BaseModel):
    code_zone: str = Field(..., min_length=3)
    nom_zone: str = Field(..., min_length=3)
    type_zone: str = Field(...,
        description="industrielle|agricole|miniere|forestiere|protegee|"
                    "militaire|touristique|corridor_ecologique|autre")
    autorite_gestionnaire: str
    arrete_ref: str
    surface_ha: Optional[float] = Field(None, gt=0,
        description='Surface en hectares — doit être > 0')
    geom_wkt: Optional[str] = None

class RegleZoneIn(BaseModel):
    """Règle de zonage applicable dans une zone spéciale."""
    zone_id:            str           = Field(..., min_length=3,
        description="UUID de la zone spéciale")
    code_regle:         str           = Field(..., min_length=2, max_length=20,
        description="Code unique de la règle (ex: RZ-NIA-001)")
    libelle:            str           = Field(..., min_length=5,
        description="Libellé descriptif de la règle")
    type_regle:         str           = Field(...,
        pattern="^(CONSTRUCTION|USAGE|SURFACE|HAUTEUR|RETRAIT|AUTRE)$")
    operations_visees:  Optional[List[str]] = None

class InfractionZoneIn(BaseModel):
    """Signaler une infraction dans une zone réglementée."""
    zone_id:     str = Field(..., description="UUID de la zone spéciale concernée")
    regle_id:    str = Field(..., description="UUID de la règle violée")
    description: str = Field(..., min_length=10,
        description="Description de l'infraction — min 10 caractères")
    parcelle_id: Optional[str] = Field(None, description="UUID de la parcelle en infraction")
    gravite:     str = Field(default="MODEREE",
        description="Gravité : MINEURE|MODEREE|GRAVE|CRITIQUE")
    date_constat: Optional[str] = Field(None, description="Date du constat YYYY-MM-DD")


async def tableau_de_bord_domaine(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_DOM + ["AUDITEUR"])),
):
    """Vue consolidée de toutes les opérations domaniales."""
    stats: dict = {}
    queries = {
        "projets_actifs":
            "SELECT COUNT(*) FROM projet_public WHERE statut='actif'",
        "expropriations_en_cours":
            "SELECT COUNT(*) FROM expropriation "
            "WHERE statut NOT IN ('archive','transfert')",
        "indemnisations_en_attente":
            "SELECT COUNT(*) FROM expropriation e "
            "LEFT JOIN paiement_indemnisation p ON p.expropriation_id=e.id "
            "WHERE e.statut='indemnisation' AND p.id IS NULL",
        "regularisations_en_cours":
            "SELECT COUNT(*) FROM demande_regularisation "
            "WHERE statut NOT IN ('archive','rejetee','attribution')",
        "conflits_fonciers_actifs":
            "SELECT COUNT(*) FROM conflit_foncier "
            "WHERE statut NOT IN ('resolu','archive')",
        "zones_speciales_actives":
            "SELECT COUNT(*) FROM zone_speciale WHERE statut='active'",
        "infractions_non_resolues":
            "SELECT COUNT(*) FROM infraction_zone WHERE statut='detectee'",
        "reserves_non_affectees":
            "SELECT COUNT(*) FROM reserve_publique WHERE est_affectee=false",
    }
    for key, sql in queries.items():
        try:
            r = await db.execute(text(sql))
            stats[key] = r.scalar() or 0
        except Exception:
            stats[key] = 0
    return {
        "acteur": current_user.email,
        "role": current_user.role,
        "module": "Domaine — Foncier public",
        "stats": stats,
        "horodatage": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════
# WF11 — EXPROPRIATION POUR UTILITÉ PUBLIQUE
# ══════════════════════════════════════════════════════════════════

@router.post("/projets-publics", status_code=201)
async def creer_projet_public(
    payload: ProjetPublicIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_URBANISME","MINISTRE_URBANISME","DIRECTEUR_DOMAINE", "AGENT_DOMAINE"]
    )),
):
    """Déclare un projet d'utilité publique — déclenche WF11."""
    proj_id = str(uuid.uuid4())
    sha = _sha(proj_id, payload.arrete_dut_ref, payload.nom_projet)
    try:
        await db.execute(text("""
            INSERT INTO projet_public
                (id,nom_projet,type_projet,arrete_dut_ref,
                 date_dut,budget_fcfa,date_fin_prevue,sha256_dut,statut,created_at)
            VALUES
                (:id,:nom,:type,:ref,:dut,:budget,:fin,:sha,'actif',NOW())
        """), {"id":proj_id,"nom":payload.nom_projet,"type":payload.type_projet,
               "ref":payload.arrete_dut_ref,"dut":payload.date_dut,
               "budget":payload.budget_fcfa,"fin":payload.date_fin_prevue,"sha":sha})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"id":proj_id,"nom_projet":payload.nom_projet,"sha256_dut":sha,"statut":"actif"}


@router.get("/projets-publics", response_model=list)
async def lister_projets(
    statut: str = Query(default="actif"),
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_LARGE + ["JUGE_FONCIER"])),
):
    try:
        r = await db.execute(text(
            "SELECT * FROM projet_public WHERE statut=:s "
            "ORDER BY created_at DESC LIMIT :l OFFSET :o"
        ), {"s":statut,"l":limit,"o":(page-1)*limit})
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


@router.post("/expropriations", status_code=201)
async def ouvrir_expropriation(
    payload: ExproprierIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_DOMAINE","DIRECTEUR_URBANISME", "AGENT_DOMAINE"]
    )),
):
    """Ouvre un dossier d'expropriation.
    Trigger tg_te1_expropriation_gel gèle automatiquement la parcelle.
    """
    exp_id = str(uuid.uuid4())
    sha = _sha(exp_id, payload.projet_id, payload.parcelle_id)
    try:
        await db.execute(text("""
            INSERT INTO expropriation
                (id,projet_id,parcelle_id,proprietaire_id,statut,
                 surface_expropriee_m2,est_partielle,sha256_dossier,cree_par,created_at)
            VALUES
                (:id,:proj,:parc,:prop,'initiee',
                 :surf,:part,:sha,:uid,NOW())
        """), {"id":exp_id,"proj":payload.projet_id,"parc":payload.parcelle_id,
               "prop":payload.proprietaire_id,"surf":payload.surface_expropriee_m2,
               "part":payload.est_partielle,"sha":sha,"uid":str(current_user.id)})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"id":exp_id,"statut":"initiee","sha256_dossier":sha,
            "message":"Parcelle gelée automatiquement (tg_te1_expropriation_gel)"}


@router.get("/expropriations", response_model=list)
async def lister_expropriations(
    projet_id: Optional[str] = Query(None),
    statut: Optional[str] = Query(None),
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ROLES_LECTURE_DOMAINE)),
):
    """Liste les expropriations filtrees par region de l agent."""
    from app.core.scope_filter import ScopeFilter
    scope = ScopeFilter.from_user(current_user)

    where  = "WHERE 1=1"
    params: dict = {"l": limit, "o": (page - 1) * limit}

    if projet_id:
        where += " AND e.projet_id = :pid::uuid"
        params["pid"] = projet_id

    if statut:
        where += " AND e.statut::TEXT = :st"
        params["st"] = statut

    # Filtre region
    if not scope.peut_voir_national and scope.region:
        where += " AND e.region = :_scope_region"
        params["_scope_region"] = scope.region

    try:
        r = await db.execute(text(f"""
            SELECT e.id, e.parcelle_id, e.type_expropriation::TEXT, e.statut::TEXT,
                   e.date_ouverture, e.motif, e.region,
                   p.nicad, p.lot, p.parcelle
            FROM expropriations e
            LEFT JOIN parcelles p ON p.id = e.parcelle_id
            {where}
            ORDER BY e.date_ouverture DESC
            LIMIT :l OFFSET :o
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _l
        _l.getLogger("domaine").warning("lister_expropriations: %s", e)
        return []


@router.get("/expropriations/{exp_id}", response_model=dict)
async def get_expropriation(
    exp_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_LARGE + ["JUGE_FONCIER"])),
):
    try:
        r = await db.execute(text("SELECT * FROM expropriation WHERE id=:id"),{"id":exp_id})
        row = r.mappings().first()
        if not row: raise HTTPException(404,"Expropriation introuvable")
        return dict(row)
    except HTTPException: raise
    except Exception as e: raise HTTPException(500, str(e)[:200])


@router.patch("/expropriations/{exp_id}/statut")
async def avancer_expropriation(
    exp_id: str,
    nouveau_statut: str = Query(...,
        description="evaluation|negociation|decision_officielle|"
                    "indemnisation|transfert|archive|contestee"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_DOMAINE","DIRECTEUR_URBANISME",
         "JUGE_FONCIER","MINISTRE_URBANISME", "AGENT_DOMAINE"]
    )),
):
    STATUTS = {"evaluation","negociation","decision_officielle",
               "indemnisation","transfert","archive","contestee"}
    if nouveau_statut not in STATUTS:
        raise HTTPException(422, f"Statut invalide : {STATUTS}")
    try:
        await db.execute(text(
            "UPDATE expropriation SET statut=:s,updated_at=NOW() WHERE id=:id"
        ), {"s":nouveau_statut,"id":exp_id})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"id":exp_id,"statut":nouveau_statut}


@router.post("/expropriations/{exp_id}/evaluation", status_code=201)
async def creer_evaluation(
    exp_id: str, payload: EvaluationIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","DIRECTEUR_DOMAINE","DIRECTEUR_URBANISME", "AGENT_DOMAINE"])),
):
    """Évaluation officielle de l'indemnisation — étape 3 WF11."""
    eval_id = str(uuid.uuid4())
    sha = _sha(eval_id, exp_id, str(payload.indemnite_proposee_fcfa))
    try:
        await db.execute(text("""
            INSERT INTO evaluation_indemnisation
                (id,expropriation_id,valeur_officielle_fcfa,valeur_marche_fcfa,
                 indemnite_proposee_fcfa,expert_evaluateur,sha256_evaluation,
                 date_evaluation,evalue_par)
            VALUES
                (:id,:eid,:voff,:vmarch,:ind,:exp,:sha,NOW(),:uid)
        """), {"id":eval_id,"eid":exp_id,"voff":payload.valeur_officielle_fcfa,
               "vmarch":payload.valeur_marche_fcfa,"ind":payload.indemnite_proposee_fcfa,
               "exp":payload.expert_evaluateur,"sha":sha,"uid":str(current_user.id)})
        await db.execute(text(
            "UPDATE expropriation SET statut='evaluation',updated_at=NOW() WHERE id=:id"
        ), {"id":exp_id})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"id":eval_id,"sha256":sha,"indemnite_proposee_fcfa":payload.indemnite_proposee_fcfa}


@router.post("/expropriations/{exp_id}/paiement", status_code=201)
async def enregistrer_paiement(
    exp_id: str, payload: PaiementIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_DIR)),
):
    """Paiement de l'indemnisation (SHA-256 tracé)."""
    if payload.mode_paiement not in ("virement","cheque","numeraire"):
        raise HTTPException(422, "mode_paiement : virement|cheque|numeraire")
    sha = _sha(payload.reference_paiement, str(payload.montant_verse_fcfa))
    try:
        await db.execute(text("""
            INSERT INTO paiement_indemnisation
                (id,expropriation_id,evaluation_id,montant_verse_fcfa,
                 mode_paiement,reference_paiement,sha256_paiement,date_paiement,verse_par)
            VALUES
                (gen_random_uuid(),:eid,:evid,:montant,:mode,:ref,:sha,NOW(),:uid)
        """), {"eid":exp_id,"evid":payload.evaluation_id,
               "montant":payload.montant_verse_fcfa,"mode":payload.mode_paiement,
               "ref":payload.reference_paiement,"sha":sha,"uid":str(current_user.id)})
        await db.execute(text(
            "UPDATE expropriation SET statut='indemnisation',updated_at=NOW() WHERE id=:id"
        ), {"id":exp_id})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"reference_paiement":payload.reference_paiement,
            "montant_fcfa":payload.montant_verse_fcfa,"sha256":sha}


@router.post("/expropriations/{exp_id}/contestation", status_code=201)
async def contester_expropriation(
    exp_id: str, payload: ContestationExpIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_DOMAINE","JUGE_FONCIER","NOTAIRE", "AGENT_DOMAINE"]
    )),
):
    """Contestation judiciaire de l'expropriation."""
    TYPES = {"indemnisation_insuffisante","utilite_non_prouvee","procedure_irreguliere"}
    if payload.type_contestation not in TYPES:
        raise HTTPException(422, f"type_contestation : {TYPES}")
    try:
        await db.execute(text("""
            INSERT INTO contestation_expropriation
                (id,expropriation_id,contestataire_id,motif,type_contestation,
                 statut,cree_par,created_at)
            VALUES
                (gen_random_uuid(),:eid,:cid,:motif,:type,'deposee',:uid,NOW())
        """), {"eid":exp_id,"cid":payload.contestataire_id,
               "motif":payload.motif,"type":payload.type_contestation,
               "uid":str(current_user.id)})
        await db.execute(text(
            "UPDATE expropriation SET statut='contestee',updated_at=NOW() WHERE id=:id"
        ), {"id":exp_id})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"expropriation_id":exp_id,"type_contestation":payload.type_contestation,
            "statut":"deposee"}


# ══════════════════════════════════════════════════════════════════
# WF12 — RÉGULARISATION FONCIÈRE
# ══════════════════════════════════════════════════════════════════

@router.post("/regularisations", status_code=201)
async def deposer_regularisation(
    payload: DemandeRegIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","AGENT_COMMUNE","ADMIN_COMMUNE","MAIRE","AGENT_DOMAINE"]
    )),
):
    """Dépôt d'une demande de régularisation (étape 1 WF12).
    Trigger tg_te2_regularisation_anti_double bloque les doublons.
    """
    reg_id = str(uuid.uuid4())
    sha = _sha(reg_id, payload.occupant_id, str(payload.surface_m2))
    try:
        await db.execute(text("""
            INSERT INTO demande_regularisation
                (id,occupant_id,surface_m2,date_occupation,geom,
                 statut,sha256_dossier,cree_par,created_at)
            VALUES
                (:id,:occ,:surf,:docc,:geom,'depot',:sha,:uid,NOW())
        """), {"id":reg_id,"occ":payload.occupant_id,"surf":payload.surface_m2,
               "docc":payload.date_occupation,"geom":payload.geom_wkt,
               "sha":sha,"uid":str(current_user.id)})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"id":reg_id,"statut":"depot","sha256_dossier":sha}


@router.get("/regularisations", response_model=list)
async def lister_regularisations(
    statut: Optional[str] = Query(None),
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_LARGE + [
        "AGENT_COMMUNE","ADMIN_COMMUNE","MAIRE","TOPOGRAPHE"
    ])),
):
    where = "WHERE 1=1"
    params: dict = {"l":limit,"o":(page-1)*limit}
    if statut: where += " AND statut=:s"; params["s"]=statut
    try:
        r = await db.execute(text(
            f"SELECT * FROM demande_regularisation {where} "
            "ORDER BY created_at DESC LIMIT :l OFFSET :o"), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


@router.post("/regularisations/{reg_id}/enquete", status_code=201)
async def soumettre_enquete(
    reg_id: str, payload: EnqueteIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","TOPOGRAPHE","GEOMETRE","AGENT_DOMAINE"]
    )),
):
    """Rapport d'enquête terrain (étape 2 WF12)."""
    sha = _sha(reg_id, str(payload.occupation_confirmee), payload.recommandation)
    try:
        await db.execute(text("""
            INSERT INTO enquete_fonciere
                (id,regularisation_id,enqueteur_id,date_enquete,
                 occupation_confirmee,conflit_detecte,observations,
                 recommandation,sha256_rapport,created_at)
            VALUES
                (gen_random_uuid(),:rid,:uid,:date,:occ,:conf,:obs,:reco,:sha,NOW())
        """), {"rid":reg_id,"uid":str(current_user.id),"date":payload.date_enquete,
               "occ":payload.occupation_confirmee,"conf":payload.conflit_detecte,
               "obs":payload.observations,"reco":payload.recommandation,"sha":sha})
        await db.execute(text(
            "UPDATE demande_regularisation SET statut='enquete',updated_at=NOW() WHERE id=:id"
        ), {"id":reg_id})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"regularisation_id":reg_id,"recommandation":payload.recommandation,"sha256":sha}


@router.post("/regularisations/{reg_id}/arrete", status_code=201)
async def emettre_arrete_reg(
    reg_id: str, payload: ArreteRegIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","MAIRE","ADMIN_COMMUNE","DIRECTEUR_DOMAINE", "AGENT_DOMAINE"]
    )),
):
    """Arrêté de régularisation — étape 4 WF12."""
    sha = _sha(payload.numero_arrete, reg_id)
    try:
        await db.execute(text("""
            INSERT INTO arrete_regularisation
                (id,regularisation_id,numero_arrete,date_arrete,signe_par,sha256_arrete)
            VALUES
                (gen_random_uuid(),:rid,:num,:date,:uid,:sha)
            ON CONFLICT (regularisation_id) DO UPDATE
                SET numero_arrete=EXCLUDED.numero_arrete,
                    date_arrete=EXCLUDED.date_arrete,
                    sha256_arrete=EXCLUDED.sha256_arrete
        """), {"rid":reg_id,"num":payload.numero_arrete,
               "date":payload.date_arrete,"uid":str(current_user.id),"sha":sha})
        await db.execute(text(
            "UPDATE demande_regularisation SET statut='arrete',updated_at=NOW() WHERE id=:id"
        ), {"id":reg_id})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"numero_arrete":payload.numero_arrete,"sha256":sha}


@router.post("/regularisations/{reg_id}/attribution", status_code=201)
async def attribuer_titre(
    reg_id: str, payload: AttributionPropIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_CADASTRE","DIRECTEUR_DOMAINE", "AGENT_DOMAINE"]
    )),
):
    """Attribution du titre de propriété — étape finale WF12."""
    attr_id = str(uuid.uuid4())
    sha = _sha(attr_id, payload.beneficiaire_id, payload.parcelle_id)
    try:
        await db.execute(text("""
            INSERT INTO attribution_propriete
                (id,arrete_reg_id,beneficiaire_id,parcelle_id,
                 sha256_titre,attribue_par,date_attribution)
            VALUES
                (:id,:ar,:ben,:parc,:sha,:uid,NOW())
        """), {"id":attr_id,"ar":payload.arrete_reg_id,
               "ben":payload.beneficiaire_id,"parc":payload.parcelle_id,
               "sha":sha,"uid":str(current_user.id)})
        await db.execute(text("""
            UPDATE demande_regularisation
            SET statut='attribution',parcelle_id=:pid,updated_at=NOW() WHERE id=:id
        """), {"pid":payload.parcelle_id,"id":reg_id})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"id":attr_id,"parcelle_id":payload.parcelle_id,"sha256_titre":sha}


# ══════════════════════════════════════════════════════════════════
# WF13 — GESTION DES CONFLITS FONCIERS
# ══════════════════════════════════════════════════════════════════

@router.post("/conflits-fonciers", status_code=201)
async def declarer_conflit_foncier(
    payload: ConflitFoncierIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_DOMAINE","AGENT_DOMAINE",
         "MAIRE","JUGE_FONCIER","AGENT_COMMUNE"]
    )),
):
    """Déclare un conflit foncier (étape 1 WF13).
    Trigger tg_te3_conflit_gel_parcelles gèle les parcelles concernées.
    """
    TYPES = {"limite_parcelle","propriete_disputee","heritage_conteste",
             "occupation_illegale","fraude_cadastrale","litige_usage"}
    if payload.type_conflit not in TYPES:
        raise HTTPException(422, f"type_conflit : {TYPES}")
    conf_id = str(uuid.uuid4())
    sha = _sha(conf_id, payload.type_conflit, payload.description[:50])
    try:
        await db.execute(text("""
            INSERT INTO conflit_foncier
                (id,type_conflit,statut,description,autorite_saisie,
                 sha256_dossier,cree_par,created_at)
            VALUES
                (:id,:type,'declare',:desc,:auto,:sha,:uid,NOW())
        """), {"id":conf_id,"type":payload.type_conflit,"desc":payload.description,
               "auto":payload.autorite_saisie,"sha":sha,"uid":str(current_user.id)})
        # Associer les parcelles
        for parc_id in payload.parcelles_ids:
            try:
                await db.execute(text("""
                    INSERT INTO parcelle_conflit (id,conflit_id,parcelle_id,role_dans_conflit)
                    VALUES (gen_random_uuid(),:cid,:pid,'contestee')
                    ON CONFLICT (conflit_id,parcelle_id) DO NOTHING
                """), {"cid":conf_id,"pid":parc_id})
            except Exception:
                pass
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"id":conf_id,"type_conflit":payload.type_conflit,"statut":"declare",
            "sha256_dossier":sha,
            "message":"Parcelles gelées automatiquement (tg_te3_conflit_gel_parcelles)"}


@router.get("/conflits-fonciers", response_model=list)
async def lister_conflits_fonciers(
    statut: Optional[str] = Query(None),
    type_conflit: Optional[str] = Query(None),
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_LARGE + [
        "JUGE_FONCIER","MAIRE","HUISSIER","GREFFIER"
    ])),
):
    where = "WHERE 1=1"
    params: dict = {"l":limit,"o":(page-1)*limit}
    if statut: where += " AND statut=:s"; params["s"]=statut
    if type_conflit: where += " AND type_conflit=:t"; params["t"]=type_conflit
    try:
        r = await db.execute(text(
            f"SELECT * FROM conflit_foncier {where} "
            "ORDER BY created_at DESC LIMIT :l OFFSET :o"), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


@router.post("/conflits-fonciers/{conf_id}/mediation", status_code=201)
async def soumettre_mediation(
    conf_id: str, payload: MediationIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_DOMAINE","HUISSIER","JUGE_FONCIER", "AGENT_DOMAINE"]
    )),
):
    """Procès-verbal de médiation (étape 2 WF13)."""
    sha = _sha(conf_id, str(payload.accord_trouve), str(payload.date_mediation))
    try:
        await db.execute(text("""
            INSERT INTO mediation_conflit
                (id,conflit_id,mediateur_id,date_mediation,
                 accord_trouve,termes_accord,sha256_pv,created_at)
            VALUES
                (gen_random_uuid(),:cid,:uid,:date,:acc,:termes,:sha,NOW())
        """), {"cid":conf_id,"uid":str(current_user.id),"date":payload.date_mediation,
               "acc":payload.accord_trouve,"termes":payload.termes_accord,"sha":sha})
        await db.execute(text(
            "UPDATE conflit_foncier SET statut='mediation' WHERE id=:id"
        ), {"id":conf_id})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"conflit_id":conf_id,"accord_trouve":payload.accord_trouve,"sha256_pv":sha}


@router.post("/conflits-fonciers/{conf_id}/decision", status_code=201)
async def rendre_decision_conflit(
    conf_id: str, payload: DecisionConflitIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","JUGE_FONCIER","DIRECTEUR_DOMAINE", "AGENT_DOMAINE"]
    )),
):
    """Décision finale sur le conflit foncier (étape 3 WF13)."""
    TYPES = {"mediation_reussie","jugement","classement","accord_amiable"}
    if payload.type_decision not in TYPES:
        raise HTTPException(422, f"type_decision : {TYPES}")
    sha = _sha(conf_id, payload.type_decision, payload.dispositif[:50])
    try:
        await db.execute(text("""
            INSERT INTO decision_conflit
                (id,conflit_id,type_decision,beneficiaire_id,dispositif,
                 gel_leve,sha256_decision,date_decision,rendue_par)
            VALUES
                (gen_random_uuid(),:cid,:type,:ben,:disp,
                 :gel,:sha,NOW(),:uid)
            ON CONFLICT (conflit_id) DO UPDATE
                SET type_decision=EXCLUDED.type_decision,
                    dispositif=EXCLUDED.dispositif,
                    sha256_decision=EXCLUDED.sha256_decision
        """), {"cid":conf_id,"type":payload.type_decision,
               "ben":payload.beneficiaire_id,"disp":payload.dispositif,
               "gel":payload.gel_leve,"sha":sha,"uid":str(current_user.id)})
        nouveau_statut = "resolu" if payload.type_decision != "jugement" else "decision"
        await db.execute(text(
            "UPDATE conflit_foncier SET statut=:s, date_resolution=NOW() WHERE id=:id"
        ), {"s":nouveau_statut,"id":conf_id})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"conflit_id":conf_id,"type_decision":payload.type_decision,
            "statut":nouveau_statut,"sha256":sha}


# ══════════════════════════════════════════════════════════════════
# WF14 — LOTISSEMENT ÉTENDU
# ══════════════════════════════════════════════════════════════════

@router.post("/etudes-techniques", status_code=201)
async def creer_etude_technique(
    payload: EtudeTechniqueIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_DOMAINE","GEOMETRE","TOPOGRAPHE","DIRECTEUR_CADASTRE", "AGENT_DOMAINE"]
    )),
):
    """Étude technique d'un lotissement — étape 2 WF14."""
    sha = _sha(payload.lotissement_id, payload.bureau_etudes)
    try:
        await db.execute(text("""
            INSERT INTO etude_technique_lotissement
                (id,lotissement_id,bureau_etudes,topographe_id,
                 surface_nette_m2,surface_voirie_m2,surface_equipements_m2,
                 densite_prevue,sha256_rapport,created_at)
            VALUES
                (gen_random_uuid(),:lid,:bureau,:topo,
                 :nette,:voirie,:equip,:dens,:sha,NOW())
            ON CONFLICT (lotissement_id) DO UPDATE
                SET bureau_etudes=EXCLUDED.bureau_etudes,
                    sha256_rapport=EXCLUDED.sha256_rapport
        """), {"lid":payload.lotissement_id,"bureau":payload.bureau_etudes,
               "topo":str(current_user.id),
               "nette":payload.surface_nette_m2,"voirie":payload.surface_voirie_m2,
               "equip":payload.surface_equipements_m2,"dens":payload.densite_prevue,
               "sha":sha})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"lotissement_id":payload.lotissement_id,"sha256_rapport":sha}


@router.post("/reserves-publiques", status_code=201)
async def enregistrer_reserve_publique(
    payload: ReservePubliqueIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_DOMAINE","DIRECTEUR_URBANISME","DIRECTEUR_CADASTRE", "AGENT_DOMAINE"]
    )),
):
    """Enregistre une réserve publique dans un lotissement."""
    TYPES = {"voirie","ecole","sante","marche","mosquee","espace_vert","autre"}
    if payload.type_reserve not in TYPES:
        raise HTTPException(422, f"type_reserve : {TYPES}")
    res_id = str(uuid.uuid4())
    sha = _sha(res_id, payload.lotissement_id, payload.type_reserve)
    try:
        await db.execute(text("""
            INSERT INTO reserve_publique
                (id,lotissement_id,type_reserve,surface_m2,geom,
                 gestionnaire,est_affectee,sha256_reserve,created_at)
            VALUES
                (:id,:lid,:type,:surf,:geom,:gest,false,:sha,NOW())
        """), {"id":res_id,"lid":payload.lotissement_id,"type":payload.type_reserve,
               "surf":payload.surface_m2,"geom":payload.geom_wkt,
               "gest":payload.gestionnaire,"sha":sha})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"id":res_id,"type_reserve":payload.type_reserve,
            "surface_m2":payload.surface_m2,"sha256":sha}


@router.get("/reserves-publiques", response_model=list)
async def lister_reserves(
    lotissement_id: Optional[str] = Query(None),
    est_affectee: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_LARGE + ["GEOMETRE","MAIRE"])),
):
    where = "WHERE 1=1"
    params: dict = {}
    if lotissement_id: where += " AND lotissement_id=:lid"; params["lid"]=lotissement_id
    if est_affectee is not None: where += " AND est_affectee=:aff"; params["aff"]=est_affectee
    try:
        r = await db.execute(text(
            f"SELECT * FROM reserve_publique {where} ORDER BY created_at DESC"), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


@router.post("/attribution-lots", status_code=201)
async def attribuer_lot(
    payload: AttributionLotIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_DOMAINE","MAIRE","ADMIN_COMMUNE", "AGENT_DOMAINE"]
    )),
):
    """Attribution d'un lot de lotissement à un bénéficiaire."""
    sha = _sha(payload.lot_id, payload.beneficiaire_id, payload.acte_communal_ref)
    try:
        await db.execute(text("""
            INSERT INTO attribution_lot
                (id,lot_id,beneficiaire_id,acte_communal_ref,prix_fcfa,
                 paiement_ref,sha256_acte,attribue_par,date_attribution)
            VALUES
                (gen_random_uuid(),:lot,:ben,:acte,:prix,
                 :paiement,:sha,:uid,NOW())
        """), {"lot":payload.lot_id,"ben":payload.beneficiaire_id,
               "acte":payload.acte_communal_ref,"prix":payload.prix_fcfa,
               "paiement":payload.paiement_ref,"sha":sha,"uid":str(current_user.id)})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"lot_id":payload.lot_id,"beneficiaire_id":payload.beneficiaire_id,
            "sha256_acte":sha}


@router.post("/financements-lotissement", status_code=201)
async def enregistrer_financement(
    payload: FinancementIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_DOMAINE","DIRECTEUR_URBANISME", "AGENT_DOMAINE"]
    )),
):
    """Enregistre un financement de lotissement."""
    TYPES = {"budget_etat","pret_bancaire","fonds_propres","aide_internationale"}
    if payload.type_financement not in TYPES:
        raise HTTPException(422, f"type_financement : {TYPES}")
    sha = _sha(payload.lotissement_id, payload.bailleur, str(payload.montant_fcfa))
    try:
        await db.execute(text("""
            INSERT INTO financement_lotissement
                (id,lotissement_id,bailleur,type_financement,montant_fcfa,
                 statut,date_accord,sha256_contrat,created_at)
            VALUES
                (gen_random_uuid(),:lid,:bailleur,:type,:montant,
                 'accorde',:date,:sha,NOW())
        """), {"lid":payload.lotissement_id,"bailleur":payload.bailleur,
               "type":payload.type_financement,"montant":payload.montant_fcfa,
               "date":payload.date_accord,"sha":sha})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"lotissement_id":payload.lotissement_id,"bailleur":payload.bailleur,
            "montant_fcfa":payload.montant_fcfa,"sha256":sha}


# ══════════════════════════════════════════════════════════════════
# WF15 — ZONES SPÉCIALES
# ══════════════════════════════════════════════════════════════════

@router.post("/zones-speciales", status_code=201)
async def creer_zone_speciale(
    payload: ZoneSpecialeIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_URBANISME","MINISTRE_URBANISME","DIRECTEUR_DOMAINE", "AGENT_DOMAINE"]
    )),
):
    """Crée une zone spéciale (étape 1 WF15)."""
    TYPES = {"industrielle","agricole","miniere","forestiere","protegee",
             "militaire","touristique","corridor_ecologique","autre"}
    if payload.type_zone not in TYPES:
        raise HTTPException(422, f"type_zone : {TYPES}")
    zone_id = str(uuid.uuid4())
    sha = _sha(zone_id, payload.code_zone, payload.arrete_ref)
    try:
        await db.execute(text("""
            INSERT INTO zone_speciale
                (id,code_zone,nom_zone,type_zone,statut,autorite_gestionnaire,
                 geom,surface_ha,arrete_ref,sha256_arrete,created_at)
            VALUES
                (:id,:code,:nom,:type,'active',:aut,
                 :geom,:surf,:ref,:sha,NOW())
        """), {"id":zone_id,"code":payload.code_zone,"nom":payload.nom_zone,
               "type":payload.type_zone,"aut":payload.autorite_gestionnaire,
               "geom":payload.geom_wkt,"surf":payload.surface_ha,
               "ref":payload.arrete_ref,"sha":sha})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"id":zone_id,"code_zone":payload.code_zone,"statut":"active","sha256":sha}


@router.get("/zones-speciales", response_model=list)
async def lister_zones_speciales(
    type_zone: Optional[str] = Query(None),
    statut: str = Query(default="active"),
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_LARGE + [
        "GEOMETRE","NOTAIRE","CHEF_CCFM"
    ])),
):
    where = "WHERE statut=:s"
    params: dict = {"s":statut,"l":limit,"o":(page-1)*limit}
    if type_zone: where += " AND type_zone=:t"; params["t"]=type_zone
    try:
        r = await db.execute(text(
            f"SELECT * FROM zone_speciale {where} "
            "ORDER BY created_at DESC LIMIT :l OFFSET :o"), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


@router.post("/zones-speciales/{zone_id}/regles", status_code=201)
async def ajouter_regle_zone(
    zone_id: str, payload: RegleZoneIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_URBANISME","DIRECTEUR_DOMAINE", "AGENT_DOMAINE"]
    )),
):
    """Ajoute une règle d'usage à une zone spéciale."""
    TYPES = {"interdiction","obligation","restriction","autorisation_prealable"}
    if payload.type_regle not in TYPES:
        raise HTTPException(422, f"type_regle : {TYPES}")
    try:
        await db.execute(text("""
            INSERT INTO regle_zone
                (id,zone_id,code_regle,libelle,type_regle,
                 operations_visees,est_active,created_at)
            VALUES
                (gen_random_uuid(),:zid,:code,:lib,:type,
                 :ops::jsonb,true,NOW())
            ON CONFLICT (zone_id,code_regle) DO UPDATE
                SET libelle=EXCLUDED.libelle,type_regle=EXCLUDED.type_regle
        """), {"zid":zone_id,"code":payload.code_regle,"lib":payload.libelle,
               "type":payload.type_regle,
               "ops":json.dumps(payload.operations_visees or [])})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"zone_id":zone_id,"code_regle":payload.code_regle,
            "type_regle":payload.type_regle}


@router.get("/zones-speciales/{zone_id}/regles", response_model=dict)
async def lister_regles_zone(
    zone_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Règles d'usage d'une zone — accessible à tous les acteurs."""
    try:
        r = await db.execute(text(
            "SELECT * FROM regle_zone WHERE zone_id=:zid AND est_active=true "
            "ORDER BY code_regle"), {"zid":zone_id})
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


@router.post("/zones-speciales/{zone_id}/infractions", status_code=201)
async def signaler_infraction_zone(
    zone_id: str, payload: InfractionZoneIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_DOMAINE","AGENT_DOMAINE",
         "RESPONSABLE_BGU","DIRECTEUR_URBANISME"]
    )),
):
    """Signale une infraction aux règles d'une zone spéciale."""
    sha = _sha(zone_id, payload.regle_id, payload.description[:50])
    try:
        await db.execute(text("""
            INSERT INTO infraction_zone
                (id,zone_id,parcelle_id,regle_id,description,
                 statut,sha256_pv,date_detection,detecte_par)
            VALUES
                (gen_random_uuid(),:zid,:pid,:rid,:desc,
                 'detectee',:sha,NOW(),:uid)
        """), {"zid":zone_id,"pid":payload.parcelle_id,"rid":payload.regle_id,
               "desc":payload.description,"sha":sha,"uid":str(current_user.id)})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"zone_id":zone_id,"statut":"detectee","sha256_pv":sha}


@router.get("/zones-speciales/{zone_id}/infractions", response_model=dict)
async def lister_infractions_zone(
    zone_id: str,
    statut: str = Query(default="detectee"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_LARGE + ["RESPONSABLE_BGU"])),
):
    try:
        r = await db.execute(text("""
            SELECT * FROM infraction_zone
            WHERE zone_id=:zid AND statut=:s
            ORDER BY date_detection DESC
        """), {"zid":zone_id,"s":statut})
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


@router.patch("/zones-speciales/{zone_id}/infractions/{inf_id}/resoudre")
async def resoudre_infraction(
    zone_id: str, inf_id: str,
    sanction: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_DIR + ["DIRECTEUR_URBANISME"])),
):
    """Marque une infraction comme résolue avec sanction éventuelle."""
    try:
        await db.execute(text("""
            UPDATE infraction_zone
            SET statut='resolue', sanction=:sanction, resolue_at=NOW()
            WHERE id=:id AND zone_id=:zid
        """), {"id":inf_id,"zid":zone_id,"sanction":sanction})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"id":inf_id,"statut":"resolue"}


# ══════════════════════════════════════════════════════════════════
# VUES CONSOLIDÉES
# ══════════════════════════════════════════════════════════════════

@router.get("/parcelle/{parcelle_id}/situation-domaniale", response_model=dict)
async def situation_domaniale_parcelle(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_LARGE + [
        "NOTAIRE","CHEF_CCFM","BANQ_DIRECTEUR","JUGE_FONCIER"
    ])),
):
    """Situation domaniale complète d'une parcelle :
    expropriations, régularisations, conflits, zones spéciales.
    """
    result: dict = {"parcelle_id": parcelle_id}
    queries = {
        "expropriations": (
            "SELECT id,statut,surface_expropriee_m2,created_at "
            "FROM expropriation WHERE parcelle_id=:pid ORDER BY created_at DESC",
            {"pid": parcelle_id}
        ),
        "regularisations": (
            "SELECT id,statut,surface_m2,created_at "
            "FROM demande_regularisation WHERE parcelle_id=:pid ORDER BY created_at DESC",
            {"pid": parcelle_id}
        ),
        "conflits": (
            "SELECT cf.id,cf.type_conflit,cf.statut "
            "FROM conflit_foncier cf "
            "JOIN parcelle_conflit pc ON pc.conflit_id=cf.id "
            "WHERE pc.parcelle_id=:pid ORDER BY cf.created_at DESC",
            {"pid": parcelle_id}
        ),
        "zones_speciales": (
            "SELECT zs.code_zone,zs.nom_zone,zs.type_zone,pz.pct_surface_en_zone "
            "FROM zone_speciale zs "
            "JOIN parcelle_zone pz ON pz.zone_id=zs.id "
            "WHERE pz.parcelle_id=:pid AND zs.statut='active'",
            {"pid": parcelle_id}
        ),
    }
    for key, (sql, params) in queries.items():
        try:
            r = await db.execute(text(sql), params)
            result[key] = [dict(row) for row in r.mappings().all()]
        except Exception:
            result[key] = []
    return result


# ══════════════════════════════════════════════════════════════════
# SOUS-MODULE DAR — DOMAINE
# Direction des Archives — liaison automatique vers ANNF
# ══════════════════════════════════════════════════════════════════

@router.get("/dar/archives", response_model=list)
async def lister_archives_dar_domaine(
    type_document: Optional[str] = Query(None,
        description="titre_domaine|expropriation|cession|lotissement_domaine|redevance"),
    scelle: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'DIRECTEUR_DOMAINE', 'AGENT_DOMAINE'] + [
        "ARCHIVISTE_ANNF","RESPONSABLE_ANNF","AUDITEUR"
    ])),
):
    """Archives du module Domaine (sous-module DAR).
    Retourne les archives liées à ce module depuis annf_archive_links.
    Filtrables par type de document et statut de scellement.
    """
    where = "WHERE entite_table LIKE :mod"
    params: dict = {
        "mod": f"%domaine%",
        "l": limit, "o": (page-1)*limit
    }
    if type_document:
        where += " AND type_archive=:td"; params["td"] = type_document
    if scelle is not None:
        where += " AND scelle=:sc"; params["sc"] = scelle
    try:
        r = await db.execute(text(f"""
            SELECT al.id, al.ida, al.type_archive,
                   al.entite_id, al.entite_table,
                   al.sha256_snapshot, al.scelle,
                   al.scelle_at, al.version, al.created_at
            FROM annf_archive_links al
            {where}
            ORDER BY al.created_at DESC
            LIMIT :l OFFSET :o
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


@router.post("/dar/archiver", status_code=201)
async def archiver_vers_annf_domaine(
    type_document: str = Query(...,
        description="titre_domaine|expropriation|cession|lotissement_domaine|redevance"),
    entite_id: str = Query(..., description="UUID de l'entité à archiver"),
    entite_table: str = Query(..., description="Nom de la table source"),
    snapshot: Optional[dict] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'DIRECTEUR_DOMAINE', 'ARCHIVISTE_ANNF'])),
):
    """Archive un document du module Domaine vers l'ANNF national.
    Crée une entrée dans annf_archive_links avec IDA atomique ANNF-YYYY-NNNNNNN.
    Liaison automatique DAR → ANNF.
    """
    from app.services.workflow_orchestrator import ANNFArchiveService
    from app.models.workflows import TypeArchiveANNF

    # Mapping type_document → TypeArchiveANNF
    type_map = {
        "rnaf": TypeArchiveANNF.RNAF,
        "rnp":  TypeArchiveANNF.RNP,
        "ccfm": TypeArchiveANNF.CCFM,
        "acte_notarie": TypeArchiveANNF.ACTE_NOTARIE,
        "decision_justice": TypeArchiveANNF.DECISION_JUSTICE,
        "bgu_scelle": TypeArchiveANNF.BGU_SCELLE,
        "plan_cadastral": TypeArchiveANNF.PLAN_CADASTRAL,
    }
    type_archive = type_map.get(type_document, TypeArchiveANNF.PLAN_CADASTRAL)

    snap = snapshot or {
        "entite_id": entite_id,
        "entite_table": entite_table,
        "module_source": "domaine",
        "type_document": type_document,
        "archive_par": current_user.email,
    }
    try:
        archive = await ANNFArchiveService.archiver(
            db=db,
            type_archive=type_archive,
            entite_id=entite_id,
            entite_table=entite_table,
            snapshot=snap,
            archive_par_id=str(current_user.id),
        )
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    return {
        "ida": archive.ida,
        "sha256": archive.sha256_snapshot,
        "module": "domaine",
        "sous_module": "DAR",
        "type_archive": type_document,
        "scelle": False,
        "message": "Archivé vers ANNF — scellement via POST /annf/{ida}/sceller",
    }


@router.get("/dar/integrite", response_model=list)
async def verifier_integrite_dar_domaine(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'DIRECTEUR_DOMAINE', 'AGENT_DOMAINE'] + ["AUDITEUR"])),
):
    """Vérifie l'intégrité des archives DAR du module Domaine.
    Retourne le compte d'archives scellées vs non scellées.
    """
    try:
        r = await db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE scelle=true)  AS nb_scelles,
                COUNT(*) FILTER (WHERE scelle=false) AS nb_en_attente,
                COUNT(*)                              AS nb_total
            FROM annf_archive_links
            WHERE entite_table LIKE :mod
        """), {"mod": f"%domaine%"})
        row = r.mappings().first()
        return dict(row) if row else {"nb_scelles":0,"nb_en_attente":0,"nb_total":0}
    except Exception:
        return {"nb_scelles":0,"nb_en_attente":0,"nb_total":0}


# ══════════════════════════════════════════════════════════════════
# VÉRIFICATION CCFM OBLIGATOIRE — Module DOMAINE
# Conforme à l'obligation réglementaire DNCD :
# tout acte foncier exige un certificat CCFM signé sur la parcelle.
# ══════════════════════════════════════════════════════════════════

@router.get("/verifier-ccfm/{parcelle_id}", response_model=dict)
async def verifier_ccfm_domaine(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Vérifie le certificat CCFM d'une parcelle depuis le module DOMAINE.
    Obligatoire avant toute action transactionnelle sur la parcelle.

    Retourne : valide, nus, statut, sha256_verif, qr_code_url,
               demandeur_nom, nicad, surface_m2, message, parcelle_gele
    """
    from app.services.ccfm_verification import verifier_ccfm as _vcfm
    return await _vcfm(db=db, parcelle_id=parcelle_id)


@router.post("/verifier-ccfm-qr")
async def verifier_ccfm_qr_domaine(
    qr_code: str = Query(...,
        description="Données QR CODE scannées depuis le certificat CCFM"),
    parcelle_id: Optional[str] = Query(None,
        description="UUID parcelle optionnel — cross-check QR vs parcelle"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Vérification CCFM par QR CODE depuis le module DOMAINE.
    4 formats QR supportés : URL, JSON, Pipe, NUS brut.
    Si parcelle_id fourni : vérifie que le QR correspond à la parcelle.
    """
    from app.services.ccfm_verification import verifier_ccfm as _vcfm
    result = await _vcfm(db=db, qr_code=qr_code)
    if parcelle_id and result.get("valide") and result.get("parcelle_id"):
        if result["parcelle_id"] != parcelle_id:
            return {
                **result,
                "valide": False,
                "message": (
                    f"QR CODE valide mais ne correspond pas à la parcelle {parcelle_id}. "
                    f"NUS {result['nus']} est lié à la parcelle {result['parcelle_id']}."
                ),
                "alerte_cross_check": True,
            }
    return result


@router.get("/ccfm-gate/{parcelle_id}", response_model=dict)
async def ccfm_gate_domaine(
    parcelle_id: str,
    action: str = Query("operation",
        description="Nom de l'action : vente, hypotheque, litige, regularisation…"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","DIRECTEUR_DOMAINE","AGENT_DOMAINE"])),
):
    """Gate CCFM — Bloque si CCFM absent, non signé, contesté ou parcelle gelée.
    À appeler avant tout acte transactionnel en module DOMAINE.
    """
    from app.services.ccfm_verification import exiger_ccfm_valide
    ok, message = await exiger_ccfm_valide(
        db=db, parcelle_id=parcelle_id, contexte=f"DOMAINE/{action}"
    )
    if not ok:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CCFM_GATE_FAILED",
                "message": message,
                "parcelle_id": parcelle_id,
                "action": action,
                "module": "DOMAINE",
                "correction": "Émettre et signer un CCFM via POST /v1/ccfm/demandes",
            }
        )
    return {
        "gate_ok": True,
        "parcelle_id": parcelle_id,
        "message": message,
        "module": "DOMAINE",
        "action": action,
    }
