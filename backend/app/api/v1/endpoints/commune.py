"""
FONCIER+ — Module Commune COMPLET
Module indépendant avec deux sous-modules :
  • DAD — Direction des Affaires Domaniales (guichet + dépôts + WF12)
  • DAR — Direction des Archives (liaison vers ANNF)

Tables couvertes :
  communes, recepisse_depot (021), mandat_elu (020),
  workflow_form_schema (021), demande_regularisation (012)
  + vue consolidée commune

Workflows portés :
  WF12 — Régularisation foncière (initié depuis la commune)
"""
import hashlib
import uuid
from datetime import date, datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user
from app.models.parcellaire import Commune, Region

router = APIRouter(prefix="/commune", tags=["Commune — DAD & DAR"])

ROLES_COMMUNE = ["ADMIN", "ADMIN_COMMUNE", "MAIRE", "AGENT_COMMUNE"]
ROLES_DIR     = ["ADMIN", "ADMIN_COMMUNE", "MAIRE"]
ROLES_READ    = ["ADMIN", "ADMIN_COMMUNE", "MAIRE", "AGENT_COMMUNE",
                 "AUDITEUR", "DIRECTEUR_URBANISME", "DIRECTEUR_DOMAINE"]


# ══════════════════════════════════════════════════════════════════
# SCHÉMAS
# ══════════════════════════════════════════════════════════════════

class DepotIn(BaseModel):
    commune_id: str
    type_demande: str = Field(...,
        description="regularisation|attribution|ccfm|rnaf|"
                    "contestation|lotissement|information")
    demandeur_nom: str = Field(..., min_length=2)
    demandeur_cni: str = Field(..., min_length=3)
    description: str = Field(..., min_length=10)
    dossier_rattache_id: Optional[str] = None


class SuiviDepotIn(BaseModel):
    statut_suivi: str = Field(..., min_length=3,
        description="Statut : recu|en_instruction|transmis|complete|rejete|clos")
    note: Optional[str] = None
    transmis_a: Optional[str] = None
    module_destinataire: Optional[str] = None


class MandatEluIn(BaseModel):
    user_id: str
    commune_id: str
    fonction: str = Field(default="maire",
        description="maire|adjoint|conseiller|president_conseil_regional")
    date_debut_mandat: date
    date_fin_mandat: date
    acte_election_ref: str = Field(..., min_length=3)
    proces_verbal_url: Optional[str] = None


class CommuneUpdateIn(BaseModel):
    nom_commune: Optional[str] = Field(None, min_length=2,
        description='Nom officiel de la commune')
    type_commune: Optional[str] = Field(None,
        description="urbaine|rurale|periurbaine")


# ══════════════════════════════════════════════════════════════════
# TABLEAU DE BORD COMMUNE
# ══════════════════════════════════════════════════════════════════


class DepotOut(BaseModel):
    id: str; numero_recepisse: Optional[str]=None; type_depot: Optional[str]=None
    statut: Optional[str]=None; region: Optional[str]=None
    class Config: from_attributes=True

@router.get("/tableau-de-bord/{commune_id}", response_model=dict)
async def tableau_de_bord_commune(
    commune_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_COMMUNE + ["AUDITEUR"])),
):
    """Vue consolidée d'une commune : dépôts, mandats, régularisations."""
    stats: dict = {}

    # Données de la commune
    r = await db.execute(select(Commune).where(Commune.id == commune_id))
    commune = r.scalar_one_or_none()
    if not commune:
        raise HTTPException(404, "Commune introuvable")

    queries = {
        "depots_aujourd_hui":
            "SELECT COUNT(*) FROM recepisse_depot "
            "WHERE date_depot::date = CURRENT_DATE",
        "depots_en_attente":
            "SELECT COUNT(*) FROM recepisse_depot "
            "WHERE statut_suivi IS NULL OR statut_suivi = 'recu'",
        "regularisations_initiees":
            "SELECT COUNT(*) FROM demande_regularisation "
            "WHERE statut = 'depot'",
        "maire_actif":
            "SELECT COUNT(*) FROM mandat_elu "
            "WHERE commune_id = :cid AND statut = 'en_cours' AND fonction = 'maire'",
        "mandats_actifs":
            "SELECT COUNT(*) FROM mandat_elu "
            "WHERE commune_id = :cid AND statut = 'en_cours'",
    }
    for key, sql in queries.items():
        try:
            params = {"cid": commune_id} if ":cid" in sql else {}
            r = await db.execute(text(sql), params)
            stats[key] = r.scalar() or 0
        except Exception:
            stats[key] = 0

    return {
        "acteur": current_user.email,
        "role": current_user.role,
        "commune": {
            "id": str(commune.id),
            "code": commune.code_commune,
            "nom": commune.nom_commune,
            "type": commune.type_commune,
        },
        "sous_modules": ["DAD — Affaires Domaniales", "DAR — Archives"],
        "stats": stats,
        "horodatage": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════
# RÉFÉRENTIEL COMMUNES
# ══════════════════════════════════════════════════════════════════

@router.get("/")
async def lister_communes(
    region_id: Optional[str] = Query(None),
    type_commune: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Liste toutes les communes du Niger, filtrable par région."""
    from sqlalchemy import and_
    filters = []
    if region_id:
        filters.append(Commune.region_id == region_id)
    if type_commune:
        filters.append(Commune.type_commune == type_commune)

    q = select(Commune)
    if filters:
        q = q.where(and_(*filters))
    q = q.order_by(Commune.nom_commune).offset((page-1)*limit).limit(limit)
        # Filtre acteur — ScopeFilter
    try:
        from app.core.scope_filter import ScopeFilter as _SF
        _scope = _SF.from_user(current_user)
        if not _scope.peut_voir_national:
            if _scope.region:
                where += " AND c.region = :_scope_region"
                params["_scope_region"] = _scope.region
            elif _scope.entite_id:
                where += " AND c.region = :_scope_entite"
                params["_scope_entite"] = _scope.entite_id
    except Exception:
        pass
    r = await db.execute(q)
    return [{
        "id": str(c.id),
        "code_commune": c.code_commune,
        "nom_commune": c.nom_commune,
        "type_commune": c.type_commune,
        "region_id": str(c.region_id),
    } for c in r.scalars().all()]


@router.get("/{commune_id}")
async def get_commune(
    commune_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Détail d'une commune."""
    r = await db.execute(select(Commune).where(Commune.id == commune_id))
    c = r.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Commune introuvable")
    # Récupérer le maire actif
    try:
        r_maire = await db.execute(text("""
            SELECT u.email, m.date_debut_mandat, m.date_fin_mandat, m.acte_election_ref
            FROM mandat_elu m
            JOIN users u ON u.id = m.user_id
            WHERE m.commune_id = :cid
              AND m.statut = 'en_cours'
              AND m.fonction = 'maire'
            LIMIT 1
        """), {"cid": commune_id})
        maire = r_maire.mappings().first()
    except Exception:
        maire = None

    return {
        "id": str(c.id),
        "code_commune": c.code_commune,
        "nom_commune": c.nom_commune,
        "type_commune": c.type_commune,
        "region_id": str(c.region_id),
        "maire_actif": dict(maire) if maire else None,
    }


# ══════════════════════════════════════════════════════════════════
# SOUS-MODULE DAD — Guichet & Dépôts citoyens
# ══════════════════════════════════════════════════════════════════

@router.post("/depots", status_code=201)
async def deposer_demande_communale(
    payload: DepotIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_COMMUNE)),
):
    """Dépôt d'une demande au guichet communal (sous-module DAD).

    Types de demandes acceptés :
    regularisation | attribution | ccfm | rnaf |
    contestation | lotissement | information

    Génère un numéro récépissé REC-YYYY-NNNNNNN atomique.
    """
    TYPES = {"regularisation","attribution","ccfm","rnaf",
             "contestation","lotissement","information"}
    if payload.type_demande not in TYPES:
        raise HTTPException(422, f"type_demande : {TYPES}")

    # Numéro récépissé atomique
    r = await db.execute(
        text("SELECT generer_numero_officiel('REC', 'seq_recepisse')")
    )
    num_recepisse = r.scalar()

    # Numéro dossier maître
    r2 = await db.execute(
        text("SELECT generer_numero_officiel('DOS', 'seq_dossier_maitre')")
    )
    num_dossier = r2.scalar()

    try:
        await db.execute(text("""
            INSERT INTO recepisse_depot
                (id, numero_recepisse, module, type_demande,
                 demandeur_nom, demandeur_cni,
                 guichet_depot_id, dossier_rattache_id, date_depot)
            VALUES
                (gen_random_uuid(), :num, 'commune', :type,
                 :nom, :cni,
                 :uid, :dossier, NOW())
        """), {
            "num": num_recepisse,
            "type": payload.type_demande,
            "nom": payload.demandeur_nom,
            "cni": payload.demandeur_cni,
            "uid": str(current_user.id),
            "dossier": payload.dossier_rattache_id,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, f"Erreur dépôt : {str(e)[:200]}")

    return {
        "numero_recepisse": num_recepisse,
        "numero_dossier": num_dossier,
        "commune_id": payload.commune_id,
        "type_demande": payload.type_demande,
        "demandeur_nom": payload.demandeur_nom,
        "sous_module": "DAD",
        "message": "Récépissé généré — conserver ce numéro pour le suivi",
    }


@router.get("/depots/", response_model=list,
    summary="Liste depots")
async def lister_depots(
    commune_id: Optional[str] = Query(None),
    type_demande: Optional[str] = Query(None),
    statut_suivi: Optional[str] = Query(None),
    date_debut: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_fin: Optional[str] = Query(None, description="YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Liste les dépôts avec filtres avancés (sous-module DAD)."""
    where = "WHERE module='commune'"
    params: dict = {"l": limit, "o": (page-1)*limit}
    if type_demande:
        where += " AND type_demande=:td"; params["td"] = type_demande
    if statut_suivi:
        where += " AND statut_suivi=:ss"; params["ss"] = statut_suivi
    if date_debut:
        where += " AND date_depot::date >= :dd"; params["dd"] = date_debut
    if date_fin:
        where += " AND date_depot::date <= :df"; params["df"] = date_fin

    try:

        # Filtre acteur — ScopeFilter
        try:
            from app.core.scope_filter import ScopeFilter as _SF
            _scope = _SF.from_user(current_user)
            if not _scope.peut_voir_national:
                if _scope.region:
                    where += " AND d.region = :_scope_region"
                    params["_scope_region"] = _scope.region
                elif _scope.entite_id:
                    where += " AND d.commune_id = :_scope_entite"
                    params["_scope_entite"] = _scope.entite_id
        except Exception:
            pass
        r = await db.execute(text(
            f"SELECT * FROM recepisse_depot {where} "
            "ORDER BY date_depot DESC LIMIT :l OFFSET :o"
        ), params)
        rows = [dict(row) for row in r.mappings().all()]
        # Comptage total
        r_count = await db.execute(text(
            f"SELECT COUNT(*) FROM recepisse_depot {where}"), params)
        total = r_count.scalar() or 0
        return {"total": total, "page": page, "items": rows}
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.get("/depots/{numero_recepisse}", response_model=dict)
async def get_depot_recepisse(
    numero_recepisse: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Consulte un dépôt par son numéro de récépissé."""
    try:
        r = await db.execute(
            text("SELECT * FROM recepisse_depot WHERE numero_recepisse=:num"),
            {"num": numero_recepisse}
        )
        row = r.mappings().first()
        if not row:
            raise HTTPException(404, f"Récépissé {numero_recepisse} introuvable")
        return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.patch("/depots/{numero_recepisse}/suivi")
async def mettre_a_jour_suivi(
    numero_recepisse: str,
    payload: SuiviDepotIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_COMMUNE)),
):
    """Met à jour le statut de suivi d'un dépôt (DAD)."""
    STATUTS = {"recu","en_instruction","transmis","complete","rejete","clos"}
    if payload.statut_suivi not in STATUTS:
        raise HTTPException(422, f"statut_suivi : {STATUTS}")
    try:
        await db.execute(text("""
            UPDATE recepisse_depot
            SET statut_suivi = :s,
                note_suivi   = :note,
                transmis_a   = :ta,
                updated_at   = NOW()
            WHERE numero_recepisse = :num
        """), {
            "s": payload.statut_suivi,
            "note": payload.note,
            "ta": payload.transmis_a or payload.module_destinataire,
            "num": numero_recepisse,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {
        "numero_recepisse": numero_recepisse,
        "statut_suivi": payload.statut_suivi,
        "transmis_a": payload.transmis_a,
    }


@router.get("/depots/{numero_recepisse}/historique", response_model=dict)
async def historique_depot(
    numero_recepisse: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Historique complet d'un dépôt : dossier lié + workflow si existant."""
    try:
        r = await db.execute(
            text("SELECT * FROM recepisse_depot WHERE numero_recepisse=:num"),
            {"num": numero_recepisse}
        )
        depot = r.mappings().first()
        if not depot:
            raise HTTPException(404, f"Récépissé {numero_recepisse} introuvable")

        result = {"depot": dict(depot), "workflow": None, "regularisation": None}

        # Chercher workflow lié
        if depot.get("dossier_rattache_id"):
            try:
                r_wf = await db.execute(text("""
                    SELECT wi.id, wi.statut, wi.etape_courante_code,
                           wi.numero_dossier_maitre, wd.type_workflow
                    FROM workflow_instance wi
                    JOIN workflow_definition wd ON wd.id = wi.definition_id
                    WHERE wi.id = :did
                """), {"did": depot["dossier_rattache_id"]})
                wf = r_wf.mappings().first()
                result["workflow"] = dict(wf) if wf else None
            except Exception:
                pass

        # Chercher régularisation liée (si type_demande = regularisation)
        if depot.get("type_demande") == "regularisation":
            try:
                r_reg = await db.execute(text("""
                    SELECT id, statut, surface_m2, created_at
                    FROM demande_regularisation
                    WHERE cree_par = :uid
                    ORDER BY created_at DESC LIMIT 1
                """), {"uid": depot.get("guichet_depot_id")})
                reg = r_reg.mappings().first()
                result["regularisation"] = dict(reg) if reg else None
            except Exception:
                pass

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.post("/depots/{numero_recepisse}/initier-workflow")
async def initier_workflow_depuis_depot(
    numero_recepisse: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_DIR)),
):
    """Initie le workflow approprié depuis un dépôt (DAD → WF12 Régularisation).

    Selon le type_demande :
    - regularisation → démarre WF12
    - ccfm           → redirige vers module CCFM
    - autres         → crée un workflow_instance générique
    """
    r = await db.execute(
        text("SELECT * FROM recepisse_depot WHERE numero_recepisse=:num"),
        {"num": numero_recepisse}
    )
    depot = r.mappings().first()
    if not depot:
        raise HTTPException(404, f"Récépissé {numero_recepisse} introuvable")

    type_wf_map = {
        "regularisation": "REGULARISATION",
        "attribution":    "REGULARISATION",
        "contestation":   "CONFLIT_FONCIER",
        "lotissement":    "LOTISSEMENT",
    }
    type_wf = type_wf_map.get(depot["type_demande"])
    if not type_wf:
        return {
            "message": f"Type '{depot['type_demande']}' — rediriger vers le module approprié",
            "module_cible": depot["type_demande"],
        }

    # Récupérer la définition du workflow
    try:
        r_def = await db.execute(text(
            "SELECT id FROM workflow_definition WHERE type_workflow=:t LIMIT 1"
        ), {"t": type_wf})
        def_row = r_def.first()
        if not def_row:
            raise HTTPException(400, f"Workflow {type_wf} non trouvé en base")

        instance_id = str(uuid.uuid4())
        await db.execute(text("""
            INSERT INTO workflow_instance
                (id, definition_id, statut, demarre_par, created_at)
            VALUES
                (:id, :def, 'en_cours', :uid, NOW())
        """), {"id": instance_id, "def": str(def_row[0]),
               "uid": str(current_user.id)})

        # Lier le récépissé au workflow
        await db.execute(text("""
            UPDATE recepisse_depot
            SET dossier_rattache_id = :wid
            WHERE numero_recepisse = :num
        """), {"wid": instance_id, "num": numero_recepisse})

        await db.commit()
        return {
            "workflow_instance_id": instance_id,
            "type_workflow": type_wf,
            "statut": "en_cours",
            "numero_recepisse": numero_recepisse,
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])


# ══════════════════════════════════════════════════════════════════
# SOUS-MODULE DAD — Mandats des élus
# ══════════════════════════════════════════════════════════════════

@router.post("/mandats", status_code=201)
async def enregistrer_mandat(
    payload: MandatEluIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN", "DIRECTEUR_URBANISME", "MINISTRE_URBANISME"]
    )),
):
    """Enregistre un mandat d'élu.

    Contrainte : 1 seul maire actif par commune
    (uq_maire_commune_actif dans migration 020).
    Trigger fn_verifier_mandat_maire bloque les workflows
    lancés par un maire sans mandat valide.
    """
    FONCTIONS = {"maire","adjoint","conseiller","president_conseil_regional"}
    if payload.fonction not in FONCTIONS:
        raise HTTPException(422, f"fonction : {FONCTIONS}")
    if payload.date_fin_mandat <= payload.date_debut_mandat:
        raise HTTPException(422, "date_fin_mandat doit être postérieure à date_debut_mandat")

    try:
        await db.execute(text("""
            INSERT INTO mandat_elu
                (id, user_id, commune_id, fonction,
                 date_debut_mandat, date_fin_mandat,
                 acte_election_ref, proces_verbal_url,
                 statut, created_at)
            VALUES
                (gen_random_uuid(), :uid, :cid, :fonc,
                 :debut, :fin, :acte, :pv,
                 'en_cours', NOW())
        """), {
            "uid": payload.user_id,
            "cid": payload.commune_id,
            "fonc": payload.fonction,
            "debut": payload.date_debut_mandat,
            "fin": payload.date_fin_mandat,
            "acte": payload.acte_election_ref,
            "pv": payload.proces_verbal_url,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "uq_maire_commune_actif" in str(e):
            raise HTTPException(409,
                "Commune a déjà un maire en cours de mandat "
                "(contrainte uq_maire_commune_actif)")
        raise HTTPException(400, str(e)[:200])

    return {
        "commune_id": payload.commune_id,
        "fonction": payload.fonction,
        "statut": "en_cours",
        "acte_election_ref": payload.acte_election_ref,
        "message": "Trigger fn_verifier_mandat_maire activé sur workflow_instance",
    }


@router.get("/mandats/{commune_id}", response_model=dict)
async def lister_mandats_commune(
    commune_id: str,
    statut: str = Query(default="en_cours"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ + ["NOTAIRE"])),
):
    """Liste les mandats d'une commune (maire actif, adjoints, conseillers)."""
    try:
        r = await db.execute(text("""
            SELECT m.id, m.fonction, m.statut,
                   m.date_debut_mandat, m.date_fin_mandat,
                   m.acte_election_ref, u.email
            FROM mandat_elu m
            JOIN users u ON u.id = m.user_id
            WHERE m.commune_id = :cid AND m.statut = :s
            ORDER BY m.fonction, m.date_debut_mandat
        """), {"cid": commune_id, "s": statut})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


@router.patch("/mandats/{mandat_id}/terminer")
async def terminer_mandat(
    mandat_id: str,
    motif: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN", "DIRECTEUR_URBANISME", "MINISTRE_URBANISME"]
    )),
):
    """Clôture ou révoque un mandat d'élu."""
    try:
        await db.execute(text("""
            UPDATE mandat_elu
            SET statut = CASE WHEN :motif IS NOT NULL THEN 'revoque' ELSE 'termine' END,
                motif_revocation = :motif,
                date_revocation  = CURRENT_DATE
            WHERE id = :id
        """), {"id": mandat_id, "motif": motif})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {
        "mandat_id": mandat_id,
        "statut": "revoque" if motif else "termine",
    }


# ══════════════════════════════════════════════════════════════════
# SOUS-MODULE DAR — Direction des Archives (liaison ANNF)
# ══════════════════════════════════════════════════════════════════

@router.get("/dar/{commune_id}/archives", response_model=dict)
async def lister_archives_commune(
    commune_id: str,
    type_document: Optional[str] = Query(None,
        description="arrete|regularisation|lotissement|mandat|recepisse"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ + [
        "ARCHIVISTE_ANNF", "RESPONSABLE_ANNF"
    ])),
):
    """Archives de la commune (sous-module DAR).
    Interroge annf_archive_links pour les documents archivés.
    """
    try:
        where = "WHERE 1=1"
        params: dict = {"cid": commune_id, "l": limit, "o": (page-1)*limit}
        if type_document:
            where += " AND type_entite = :td"; params["td"] = type_document

        r = await db.execute(text(f"""
            SELECT al.id, al.entite_id, al.type_entite,
                   al.sha256_contenu, al.scelle_at, al.archive_par
            FROM annf_archive_links al
            WHERE al.source_module = 'commune'
              {where.replace('WHERE 1=1','')}
            ORDER BY al.scelle_at DESC
            LIMIT :l OFFSET :o
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception:
        # Fallback si annf_archive_links n'a pas source_module
        return []


@router.post("/dar/{commune_id}/archiver", status_code=201)
async def archiver_document_commune(
    commune_id: str,
    type_document: str = Query(...,
        description="arrete|regularisation|lotissement|mandat"),
    entite_id: str = Query(..., description="UUID de l'entité à archiver"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "ARCHIVISTE_ANNF", "ADMIN_COMMUNE", "MAIRE"])),
):
    """Archive un document de la commune vers l'ANNF (sous-module DAR)."""
    sha = hashlib.sha256(
        f"{commune_id}:{type_document}:{entite_id}:{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()
    try:
        await db.execute(text("""
            INSERT INTO annf_archive_links
                (id, entite_id, type_entite, source_module,
                 sha256_contenu, scelle_at, archive_par)
            VALUES
                (gen_random_uuid(), :eid, :type, 'commune',
                 :sha, NOW(), :uid)
        """), {
            "eid": entite_id, "type": type_document,
            "sha": sha, "uid": str(current_user.id),
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {
        "entite_id": entite_id,
        "type_document": type_document,
        "sha256": sha,
        "sous_module": "DAR",
        "message": "Document archivé vers ANNF",
    }


# ══════════════════════════════════════════════════════════════════
# FORMULAIRES (workflow_form_schema migration 021)
# ══════════════════════════════════════════════════════════════════

@router.get("/formulaires/{workflow_code}", response_model=dict)
async def get_schema_formulaire(
    workflow_code: str,
    etape: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Retourne le schéma JSON du formulaire d'une étape de workflow.
    Utilisé par le frontend pour construire dynamiquement les formulaires.
    """
    try:
        where = "WHERE workflow_code=:wf AND is_active=true"
        params: dict = {"wf": workflow_code.upper()}
        if etape:
            where += " AND etape=:etape"
            params["etape"] = etape

        r = await db.execute(text(
            f"SELECT * FROM workflow_form_schema {where} "
            "ORDER BY version DESC LIMIT 1"
        ), params)
        row = r.mappings().first()
        if not row:
            raise HTTPException(404,
                f"Formulaire {workflow_code}"
                f"{f'/{etape}' if etape else ''} introuvable")
        return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.get("/formulaires/{workflow_code}/toutes-etapes", response_model=dict)
async def get_tous_schemas_workflow(
    workflow_code: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Retourne tous les schémas de formulaires d'un workflow."""
    try:
        r = await db.execute(text("""
            SELECT etape, json_schema, required_fields, validation_rules, version
            FROM workflow_form_schema
            WHERE workflow_code=:wf AND is_active=true
            ORDER BY etape, version DESC
        """), {"wf": workflow_code.upper()})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


# ══════════════════════════════════════════════════════════════════
# STATISTIQUES COMMUNALES
# ══════════════════════════════════════════════════════════════════

@router.get("/stats/national", response_model=list,
    summary="Liste stats/national")
async def stats_communes_national(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN", "DIRECTEUR_URBANISME", "AUDITEUR", "MINISTRE_URBANISME"]
    )),
):
    """Statistiques nationales des communes."""
    stats: dict = {}
    try:
        r = await db.execute(text("""
            SELECT type_commune, COUNT(*) nb
            FROM communes GROUP BY type_commune ORDER BY nb DESC
        """))
        stats["communes_par_type"] = {row.type_commune: row.nb for row in r}
    except Exception:
        stats["communes_par_type"] = {}

    try:
        r = await db.execute(text("""
            SELECT r.nom_commune AS region,
                   COUNT(c.id) AS nb_communes
            FROM regions r
            LEFT JOIN communes c ON c.region_id = r.id
            GROUP BY r.nom_commune
            ORDER BY nb_communes DESC
        """))
        stats["communes_par_region"] = [dict(row) for row in r.mappings().all()]
    except Exception:
        stats["communes_par_region"] = []

    try:
        r = await db.execute(text(
            "SELECT COUNT(*) FROM communes"
        ))
        stats["total_communes"] = r.scalar() or 0
    except Exception:
        stats["total_communes"] = 0

    try:
        r = await db.execute(text("""
            SELECT COUNT(DISTINCT commune_id) FROM mandat_elu
            WHERE statut='en_cours' AND fonction='maire'
        """))
        stats["communes_avec_maire"] = r.scalar() or 0
    except Exception:
        stats["communes_avec_maire"] = 0

    return stats


@router.get("/stats/activite-depots", response_model=list,
    summary="Liste stats/activite depots")
async def stats_activite_depots(
    nb_jours: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN", "DIRECTEUR_URBANISME", "AUDITEUR", "ADMIN_COMMUNE", "MAIRE"]
    )),
):
    """Activité des dépôts sur les N derniers jours."""
    try:
        r = await db.execute(text("""
            SELECT type_demande,
                   COUNT(*) AS nb_depots,
                   COUNT(*) FILTER (WHERE statut_suivi = 'clos') AS nb_clos,
                   COUNT(*) FILTER (WHERE statut_suivi IS NULL) AS nb_en_attente
            FROM recepisse_depot
            WHERE module = 'commune'
              AND date_depot >= NOW() - (:nb || ' days')::INTERVAL
            GROUP BY type_demande
            ORDER BY nb_depots DESC
        """), {"nb": nb_jours})
        return {
            "periode_jours": nb_jours,
            "par_type": [dict(row) for row in r.mappings().all()]
        }
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


# ══════════════════════════════════════════════════════════════════
# VÉRIFICATION CCFM OBLIGATOIRE — Module COMMUNE
# Conforme à l'obligation réglementaire DNCD :
# tout acte foncier exige un certificat CCFM signé sur la parcelle.
# ══════════════════════════════════════════════════════════════════

@router.get("/verifier-ccfm/{parcelle_id}", response_model=dict)
async def verifier_ccfm_commune(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Vérifie le certificat CCFM d'une parcelle depuis le module COMMUNE.
    Obligatoire avant toute action transactionnelle sur la parcelle.

    Retourne : valide, nus, statut, sha256_verif, qr_code_url,
               demandeur_nom, nicad, surface_m2, message, parcelle_gele
    """
    from app.services.ccfm_verification import verifier_ccfm as _vcfm
    return await _vcfm(db=db, parcelle_id=parcelle_id)


@router.post("/verifier-ccfm-qr")
async def verifier_ccfm_qr_commune(
    qr_code: str = Query(...,
        description="Données QR CODE scannées depuis le certificat CCFM"),
    parcelle_id: Optional[str] = Query(None,
        description="UUID parcelle optionnel — cross-check QR vs parcelle"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Vérification CCFM par QR CODE depuis le module COMMUNE.
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
async def ccfm_gate_commune(
    parcelle_id: str,
    action: str = Query("operation",
        description="Nom de l'action : vente, hypotheque, litige, regularisation…"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","MAIRE","AGENT_COMMUNE","ADMIN_COMMUNE", "MAIRE"])),
):
    """Gate CCFM — Bloque si CCFM absent, non signé, contesté ou parcelle gelée.
    À appeler avant tout acte transactionnel en module COMMUNE.
    """
    from app.services.ccfm_verification import exiger_ccfm_valide
    ok, message = await exiger_ccfm_valide(
        db=db, parcelle_id=parcelle_id, contexte=f"COMMUNE/{action}"
    )
    if not ok:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CCFM_GATE_FAILED",
                "message": message,
                "parcelle_id": parcelle_id,
                "action": action,
                "module": "COMMUNE",
                "correction": "Émettre et signer un CCFM via POST /v1/ccfm/demandes",
            }
        )
    return {
        "gate_ok": True,
        "parcelle_id": parcelle_id,
        "message": message,
        "module": "COMMUNE",
        "action": action,
    }
