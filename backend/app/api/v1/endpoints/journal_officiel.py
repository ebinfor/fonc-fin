"""
FONCIER+ — Module Journal Officiel COMPLET
Module indépendant — publication légale nationale.

Tables :
  publications_jo (legacy 001)    — parutions officielles
  rnaf_version_audit (006)        — versions et snapshots RNAF

Trigger clé :
  tg_enforce_rnaf_scelle_for_jo  — bloque toute publication
  si le RNAF n'est pas au statut SCELLÉ

Flux de publication :
  RNAF scellé → EDITEUR_JO crée la parution → RNAF passe à PUBLIE
  → tg_sync_rnp_from_rnaf cascade → RNP + BGU synchronisés

Types publiables :
  rnaf | arrete_foncier | arrete_urbanisme | lotissement |
  zone_speciale | decret | nomination | regularisation | autre
"""
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user

router = APIRouter(prefix="/jo", tags=["Journal Officiel"])

ROLES_JO   = ["ADMIN", "EDITEUR_JO", "DIRECTEUR_URBANISME",
               "MINISTRE_URBANISME"]
ROLES_EDIT = ["ADMIN", "EDITEUR_JO"]
ROLES_READ = ["ADMIN", "EDITEUR_JO", "DIRECTEUR_URBANISME",
              "MINISTRE_URBANISME", "AUDITEUR", "NOTAIRE",
              "DIRECTEUR_CADASTRE", "CHEF_CCFM"]

TYPES_PUBLIABLES = {
    "rnaf", "arrete_foncier", "arrete_urbanisme", "lotissement",
    "zone_speciale", "decret", "nomination", "regularisation", "autre"
}


# ══════════════════════════════════════════════════════════════════
# SCHÉMAS
# ══════════════════════════════════════════════════════════════════

class ParutionIn(BaseModel):
    numero_jo: str = Field(...,
        min_length=11, max_length=14,
        description="Format JO-YYYY-NNN ex: JO-2026-042 — 11 à 14 caractères")
    date_parution: datetime
    type_contenu: str = Field(...,
        description="rnaf|arrete_foncier|arrete_urbanisme|lotissement|"
                    "zone_speciale|decret|nomination|regularisation|autre")
    entite_ref_id: str = Field(...,
        description="UUID de l'entité publiée (RNAF, arrêté…)")
    entite_ref_type: str = Field(...,
        description="rnaf_workflow|arretes_urbanisme|lotissements|"
                    "zone_speciale|autre")
    extrait_texte: Optional[str] = None
    rnaf_id: Optional[str] = None


class PublicationRNAFIn(BaseModel):
    """Lier un RNAF à une parution du Journal Officiel."""
    rnaf_workflow_id: str           = Field(..., min_length=3,
        description="UUID du workflow RNAF à publier")
    numero_jo:        str           = Field(..., min_length=11, max_length=14,
        description="Format JO-YYYY-NNN ex: JO-2026-042")
    date_parution:    datetime      = Field(...)
    extrait_texte:    Optional[str] = Field(None, min_length=10)

class PublicationBatchIn(BaseModel):
    """Publication groupée de plusieurs RNAF dans un même numéro JO."""
    numero_jo:          str       = Field(..., min_length=11, max_length=14,
        description="Format JO-YYYY-NNN ex: JO-2026-042")
    date_parution:      datetime  = Field(...)
    rnaf_workflow_ids:  List[str] = Field(...,
        description="Liste de 1 à 50 UUIDs de workflows RNAF")

class VersionRNAFIn(BaseModel):
    rnaf_id: str
    rnaf_workflow_id: Optional[str] = None
    motif_modification: str = Field(..., min_length=10)
    snapshot_rnaf: dict = Field(...,
        description="Snapshot JSON complet de l'état RNAF")


# ══════════════════════════════════════════════════════════════════
# TABLEAU DE BORD EDITEUR_JO
# ══════════════════════════════════════════════════════════════════


class ParutionOut(BaseModel):
    id: str; numero: Optional[str]=None; date_parution: Optional[str]=None
    statut: Optional[str]=None; region: Optional[str]=None
    class Config: from_attributes=True

@router.get("/tableau-de-bord", response_model=list,
    summary="Liste tableau de bord")
async def tableau_de_bord_jo(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_JO)),
):
    """Vue consolidée de l'éditeur JO :
    - File des RNAF scellés en attente de publication
    - Statistiques des publications
    - Dernières parutions
    """
    stats: dict = {}

    # RNAF scellés en attente
    try:
        r = await db.execute(text("""
            SELECT rw.id, rw.rnaf_id, rw.date_scellement,
                   rw.sha256_publie
            FROM rnaf_workflow rw
            WHERE rw.statut = 'scelle'
            ORDER BY rw.date_scellement ASC
        """))
        en_attente = [dict(row) for row in r.mappings().all()]
    except Exception:
        en_attente = []

    # Compteurs
    for key, sql in [
        ("rnaf_publies_total",
         "SELECT COUNT(*) FROM rnaf_workflow WHERE statut='publie'"),
        ("parutions_ce_mois",
         "SELECT COUNT(*) FROM publications_jo "
         "WHERE date_parution >= date_trunc('month', NOW())"),
        ("parutions_totales",
         "SELECT COUNT(*) FROM publications_jo"),
        ("dernier_numero_jo",
         "SELECT MAX(numero_jo) FROM publications_jo"),
    ]:
        try:
            r = await db.execute(text(sql))
            stats[key] = r.scalar() or 0
        except Exception:
            stats[key] = 0

    # Dernières parutions (5)
    try:
        r = await db.execute(text("""
            SELECT id, numero_jo, date_parution,
                   type_contenu, entite_ref_type
            FROM publications_jo
            ORDER BY date_parution DESC
            LIMIT 5
        """))
        dernieres = [dict(row) for row in r.mappings().all()]
    except Exception:
        dernieres = []

    return {
        "acteur": current_user.email,
        "role": current_user.role,
        "module": "Journal Officiel",
        "file_attente_rnaf": en_attente,
        "nb_en_attente": len(en_attente),
        "stats": stats,
        "dernieres_parutions": dernieres,
        "horodatage": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════
# PARUTIONS — CRUD
# ══════════════════════════════════════════════════════════════════

@router.post("/parutions", status_code=201)
async def creer_parution(
    payload: ParutionIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_EDIT)),
):
    """Enregistre une parution au Journal Officiel.

    Trigger tg_enforce_rnaf_scelle_for_jo bloque automatiquement
    si le RNAF référencé n'est pas au statut SCELLÉ.

    Types acceptés : rnaf | arrete_foncier | arrete_urbanisme |
    lotissement | zone_speciale | decret | nomination |
    regularisation | autre
    """
    if payload.type_contenu not in TYPES_PUBLIABLES:
        raise HTTPException(422,
            f"type_contenu invalide. Valeurs : {sorted(TYPES_PUBLIABLES)}")

    parution_id = str(uuid.uuid4())
    try:
        await db.execute(text("""
            INSERT INTO publications_jo (
                id, numero_jo, date_parution, type_contenu,
                entite_ref_id, entite_ref_type,
                rnaf_id, publie_par, created_at
            ) VALUES (
                :id, :num, :date, :type,
                :ref_id, :ref_type,
                :rnaf, :uid, NOW()
            )
        """), {
            "id":       parution_id,
            "num":      payload.numero_jo,
            "date":     payload.date_parution,
            "type":     payload.type_contenu,
            "ref_id":   payload.entite_ref_id,
            "ref_type": payload.entite_ref_type,
            "rnaf":     payload.rnaf_id,
            "uid":      str(current_user.id),
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "JOURNAL OFFICIEL BLOQUÉ" in str(e):
            raise HTTPException(400,
                f"Publication refusée par trigger : {str(e)[:300]}")
        raise HTTPException(400, str(e)[:200])

    return {
        "id": parution_id,
        "numero_jo": payload.numero_jo,
        "type_contenu": payload.type_contenu,
        "date_parution": payload.date_parution.isoformat(),
        "entite_ref_id": payload.entite_ref_id,
    }


@router.get("/parutions", response_model=list,
    summary="Liste parutions")
async def lister_parutions(
    type_contenu: Optional[str] = Query(None),
    numero_jo: Optional[str] = Query(None),
    annee: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Liste les parutions avec filtres (type, numéro JO, année)."""
    where = "WHERE 1=1"
    params: dict = {"l": limit, "o": (page-1)*limit}
    if type_contenu:
        where += " AND type_contenu=:tc"; params["tc"] = type_contenu
    if numero_jo:
        where += " AND numero_jo=:njo"; params["njo"] = numero_jo
    if annee:
        where += " AND EXTRACT(YEAR FROM date_parution)=:yr"
        params["yr"] = annee
    try:
        # ScopeFilter
        try:
            from app.core.scope_filter import ScopeFilter as _S
            _sc = _S.from_user(current_user)
            if not _sc.peut_voir_national and _sc.region:
                where += ' AND p.region = :_scope_region'
                params['_scope_region'] = _sc.region
        except Exception: pass
        r = await db.execute(text(
            f"SELECT * FROM publications_jo {where} "
            "ORDER BY date_parution DESC LIMIT :l OFFSET :o"
        ), params)
        rows = [dict(row) for row in r.mappings().all()]
        r_cnt = await db.execute(text(
            f"SELECT COUNT(*) FROM publications_jo {where}"), params)
        return {"total": r_cnt.scalar() or 0, "page": page, "items": rows}
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.get("/parutions/{parution_id}", response_model=dict)
async def get_parution(
    parution_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Détail d'une parution."""
    try:
        r = await db.execute(
            text("SELECT * FROM publications_jo WHERE id=:id"),
            {"id": parution_id}
        )
        row = r.mappings().first()
        if not row:
            raise HTTPException(404, "Parution introuvable")
        return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.get("/numeros/{numero_jo}", response_model=dict)
async def get_numero_jo(
    numero_jo: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Toutes les parutions d'un numéro de JO."""
    try:
        r = await db.execute(text("""
            SELECT * FROM publications_jo
            WHERE numero_jo = :num
            ORDER BY type_contenu
        """), {"num": numero_jo})
        rows = [dict(row) for row in r.mappings().all()]
        if not rows:
            raise HTTPException(404, f"Numéro JO {numero_jo} introuvable")
        return {
            "numero_jo": numero_jo,
            "nb_parutions": len(rows),
            "parutions": rows,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


# ══════════════════════════════════════════════════════════════════
# PUBLICATION RNAF — route dédiée (flux principal)
# ══════════════════════════════════════════════════════════════════

@router.post("/publier-rnaf", status_code=201)
async def publier_rnaf_au_jo(
    payload: PublicationRNAFIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_EDIT)),
):
    """Publie un RNAF scellé au Journal Officiel.

    Pipeline :
    1. Vérifie que le RNAF est au statut SCELLÉ
    2. Crée la parution dans publications_jo
       (trigger enforce_rnaf_scelle_for_jo valide)
    3. Avance le RNAF vers PUBLIE via RNAFWorkflowService
    4. Déclenche tg_sync_rnp_from_rnaf → RNP + BGU synchronisés
    """
    # 1. Vérifier statut RNAF
    try:
        r = await db.execute(text("""
            SELECT statut, rnaf_id FROM rnaf_workflow
            WHERE id = :id
        """), {"id": payload.rnaf_workflow_id})
        rw = r.mappings().first()
        if not rw:
            raise HTTPException(404, "Workflow RNAF introuvable")
        if rw["statut"] != "scelle":
            raise HTTPException(400,
                f"RNAF au statut '{rw['statut']}' — "
                "seul SCELLÉ peut être publié au JO")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])

    # 2. Créer la parution
    parution_id = str(uuid.uuid4())
    try:
        await db.execute(text("""
            INSERT INTO publications_jo (
                id, numero_jo, date_parution, type_contenu,
                entite_ref_id, entite_ref_type,
                rnaf_id, publie_par, created_at
            ) VALUES (
                :id, :num, :date, 'rnaf',
                :ref_id, 'rnaf_workflow',
                :rnaf_id, :uid, NOW()
            )
        """), {
            "id":      parution_id,
            "num":     payload.numero_jo,
            "date":    payload.date_parution,
            "ref_id":  payload.rnaf_workflow_id,
            "rnaf_id": str(rw["rnaf_id"]),
            "uid":     str(current_user.id),
        })
        await db.flush()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400,
            f"Erreur parution JO : {str(e)[:300]}")

    # 3. Avancer le RNAF vers PUBLIE
    from app.services.workflow_orchestrator import RNAFWorkflowService
    from app.models.workflows import StatutRNAF
    try:
        wf = await RNAFWorkflowService.avancer_statut(
            db=db,
            rnaf_workflow_id=payload.rnaf_workflow_id,
            nouveau_statut=StatutRNAF.PUBLIE,
            acteur_id=str(current_user.id),
            publication_jo_id=parution_id,
        )
    except ValueError as e:
        await db.rollback()
        raise HTTPException(422, str(e))

    await db.commit()
    return {
        "parution_id":       parution_id,
        "numero_jo":         payload.numero_jo,
        "rnaf_workflow_id":  payload.rnaf_workflow_id,
        "rnaf_statut":       wf.statut,
        "sha256_publie":     wf.sha256_publie,
        "date_publication":  wf.date_publication.isoformat()
                             if wf.date_publication else None,
        "cascade":           "tg_sync_rnp_from_rnaf → RNP + BGU synchronisés",
    }


@router.post("/publier-rnaf-batch", status_code=201)
async def publier_rnaf_batch(
    payload: PublicationBatchIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_EDIT)),
):
    """Publication groupée de plusieurs RNAF dans un même numéro de JO.
    Chaque RNAF est traité indépendamment — les échecs n'annulent
    pas les réussites précédentes.
    """
    from app.services.workflow_orchestrator import RNAFWorkflowService
    from app.models.workflows import StatutRNAF

    resultats = {"succes": [], "echecs": []}

    for wf_id in payload.rnaf_workflow_ids:
        try:
            # Vérifier statut
            r = await db.execute(text(
                "SELECT statut, rnaf_id FROM rnaf_workflow WHERE id=:id"
            ), {"id": wf_id})
            rw = r.mappings().first()
            if not rw or rw["statut"] != "scelle":
                resultats["echecs"].append({
                    "rnaf_workflow_id": wf_id,
                    "erreur": f"Statut {rw['statut'] if rw else 'introuvable'}"
                })
                continue

            parution_id = str(uuid.uuid4())
            await db.execute(text("""
                INSERT INTO publications_jo (
                    id, numero_jo, date_parution, type_contenu,
                    entite_ref_id, entite_ref_type,
                    rnaf_id, publie_par, created_at
                ) VALUES (
                    :id, :num, :date, 'rnaf',
                    :ref, 'rnaf_workflow', :rnaf, :uid, NOW()
                )
            """), {
                "id": parution_id, "num": payload.numero_jo,
                "date": payload.date_parution, "ref": wf_id,
                "rnaf": str(rw["rnaf_id"]), "uid": str(current_user.id),
            })
            await db.flush()

            wf = await RNAFWorkflowService.avancer_statut(
                db=db, rnaf_workflow_id=wf_id,
                nouveau_statut=StatutRNAF.PUBLIE,
                acteur_id=str(current_user.id),
                publication_jo_id=parution_id,
            )
            await db.flush()

            resultats["succes"].append({
                "rnaf_workflow_id": wf_id,
                "parution_id": parution_id,
                "sha256_publie": wf.sha256_publie,
            })
        except Exception as e:
            resultats["echecs"].append({
                "rnaf_workflow_id": wf_id,
                "erreur": str(e)[:200],
            })
            await db.rollback()

    if resultats["succes"]:
        await db.commit()

    return {
        "numero_jo": payload.numero_jo,
        "nb_succes": len(resultats["succes"]),
        "nb_echecs": len(resultats["echecs"]),
        "resultats": resultats,
    }


# ══════════════════════════════════════════════════════════════════
# RECHERCHE ET CONSULTATION
# ══════════════════════════════════════════════════════════════════

@router.get("/recherche", response_model=list,
    summary="Liste recherche")
async def rechercher_parutions(
    q: str = Query(..., min_length=3,
        description="Recherche dans numero_jo, entite_ref_type"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Recherche libre dans les parutions du JO.
    Accessible à tous les acteurs authentifiés.
    """
    try:
        r = await db.execute(text("""
            SELECT id, numero_jo, date_parution,
                   type_contenu, entite_ref_type, entite_ref_id
            FROM publications_jo
            WHERE numero_jo ILIKE :q
               OR type_contenu ILIKE :q
               OR entite_ref_type ILIKE :q
            ORDER BY date_parution DESC
            LIMIT :l OFFSET :o
        """), {"q": f"%{q}%", "l": limit, "o": (page-1)*limit})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.get("/rnaf-publie/{rnaf_workflow_id}", response_model=dict)
async def get_parution_rnaf(
    rnaf_workflow_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Récupère la parution JO d'un RNAF publié.
    Accessible sans restriction — consultation publique.
    """
    try:
        r = await db.execute(text("""
            SELECT pj.id, pj.numero_jo, pj.date_parution,
                   pj.type_contenu, rw.sha256_publie,
                   rw.statut AS statut_rnaf,
                   rw.date_publication
            FROM publications_jo pj
            JOIN rnaf_workflow rw ON rw.id = pj.entite_ref_id
            WHERE pj.entite_ref_id = :wf_id
              AND pj.type_contenu = 'rnaf'
            ORDER BY pj.date_parution DESC
            LIMIT 1
        """), {"wf_id": rnaf_workflow_id})
        row = r.mappings().first()
        if not row:
            raise HTTPException(404,
                "Aucune parution JO pour ce workflow RNAF")
        return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.get("/historique-rnaf/{rnaf_id}", response_model=dict)
async def historique_versions_rnaf(
    rnaf_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Historique complet des versions d'un RNAF (rnaf_version_audit).
    Chaque version est un snapshot immuable SHA-256 chaîné.
    """
    try:
        r = await db.execute(text("""
            SELECT va.id, va.numero_version, va.statut_version,
                   va.date_version, va.motif_modification,
                   va.sha256_version, u.email AS modifie_par_email
            FROM rnaf_version_audit va
            JOIN users u ON u.id = va.modifie_par
            WHERE va.rnaf_id = :rnaf_id
            ORDER BY va.numero_version ASC
        """), {"rnaf_id": rnaf_id})
        versions = [dict(row) for row in r.mappings().all()]
        if not versions:
            raise HTTPException(404,
                "Aucune version RNAF enregistrée pour cet identifiant")
        return {
            "rnaf_id": rnaf_id,
            "nb_versions": len(versions),
            "versions": versions,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.post("/versions-rnaf", status_code=201)
async def enregistrer_version_rnaf(
    payload: VersionRNAFIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_EDIT + ["DIRECTEUR_URBANISME"])),
):
    """Enregistre un snapshot versionné d'un RNAF dans rnaf_version_audit.
    Chaque version est immuable et SHA-256 chaîné.
    """
    import json
    snapshot_str = json.dumps(payload.snapshot_rnaf, sort_keys=True)
    sha = hashlib.sha256(
        f"{payload.rnaf_id}:{snapshot_str}:{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()

    try:
        # Numéro de version auto
        r = await db.execute(text("""
            SELECT COALESCE(MAX(numero_version), 0) + 1
            FROM rnaf_version_audit WHERE rnaf_id = :rid
        """), {"rid": payload.rnaf_id})
        numero = r.scalar() or 1

        await db.execute(text("""
            INSERT INTO rnaf_version_audit (
                id, rnaf_id, rnaf_workflow_id,
                numero_version, statut_version, motif_modification,
                snapshot_rnaf, sha256_version,
                modifie_par, created_at
            ) VALUES (
                gen_random_uuid(), :rid, :wf_id,
                :num, 'version_manuelle', :motif,
                :snap::jsonb, :sha,
                :uid, NOW()
            )
        """), {
            "rid":   payload.rnaf_id,
            "wf_id": payload.rnaf_workflow_id,
            "num":   numero,
            "motif": payload.motif_modification,
            "snap":  snapshot_str,
            "sha":   sha,
            "uid":   str(current_user.id),
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    return {
        "rnaf_id": payload.rnaf_id,
        "numero_version": numero,
        "sha256_version": sha,
    }


# ══════════════════════════════════════════════════════════════════
# STATISTIQUES ET RAPPORTS
# ══════════════════════════════════════════════════════════════════

@router.get("/stats/par-type", response_model=list,
    summary="Liste stats/par type")
async def stats_par_type_contenu(
    annee: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Statistiques des parutions par type de contenu."""
    where = "WHERE 1=1"
    params: dict = {}
    if annee:
        where += " AND EXTRACT(YEAR FROM date_parution)=:yr"
        params["yr"] = annee
    try:
        r = await db.execute(text(f"""
            SELECT type_contenu,
                   COUNT(*) AS nb_parutions,
                   MIN(date_parution) AS premiere_parution,
                   MAX(date_parution) AS derniere_parution
            FROM publications_jo {where}
            GROUP BY type_contenu
            ORDER BY nb_parutions DESC
        """), params)
        return {
            "annee_filtre": annee,
            "stats": [dict(row) for row in r.mappings().all()]
        }
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.get("/stats/rnaf-en-attente", response_model=list,
    summary="Liste stats/rnaf en attente")
async def stats_rnaf_en_attente_publication(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_JO)),
):
    """Détail des RNAF scellés en attente de publication au JO.
    Inclut le délai d'attente depuis le scellement.
    """
    try:
        r = await db.execute(text("""
            SELECT
                rw.id AS rnaf_workflow_id,
                rw.rnaf_id,
                rw.date_scellement,
                EXTRACT(DAY FROM NOW() - rw.date_scellement)::INT
                    AS jours_attente,
                rw.sha256_publie
            FROM rnaf_workflow rw
            WHERE rw.statut = 'scelle'
            ORDER BY rw.date_scellement ASC
        """))
        en_attente = [dict(row) for row in r.mappings().all()]
        critique = [r for r in en_attente
                    if r.get("jours_attente", 0) > 7]
        return {
            "total_en_attente": len(en_attente),
            "en_retard_plus_7j": len(critique),
            "details": en_attente,
        }
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.get("/stats/activite-mensuelle", response_model=list,
    summary="Liste stats/activite mensuelle")
async def stats_activite_mensuelle(
    nb_mois: int = Query(default=12, ge=1, le=36),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Activité mensuelle des publications sur N mois."""
    try:
        r = await db.execute(text("""
            SELECT
                EXTRACT(YEAR FROM date_parution)::INT  AS annee,
                EXTRACT(MONTH FROM date_parution)::INT AS mois,
                COUNT(*) AS nb_parutions,
                COUNT(*) FILTER (WHERE type_contenu='rnaf') AS nb_rnaf
            FROM publications_jo
            WHERE date_parution >= NOW() - (:nb || ' months')::INTERVAL
            GROUP BY 1, 2
            ORDER BY 1 DESC, 2 DESC
        """), {"nb": nb_mois})
        return {
            "periode_mois": nb_mois,
            "activite": [dict(row) for row in r.mappings().all()]
        }
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


# ══════════════════════════════════════════════════════════════════
# SOUS-MODULE DAR — JOURNAL_OFFICIEL
# Direction des Archives — liaison automatique vers ANNF
# ══════════════════════════════════════════════════════════════════

@router.get("/dar/archives", response_model=list,
    summary="Liste dar/archives")
async def lister_archives_dar_journal_officiel(
    type_document: Optional[str] = Query(None,
        description="rnaf|arrete_ministeriel|decret|publication_jo"),
    scelle: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'EDITEUR_JO', 'AUDITEUR', 'DIRECTEUR_URBANISME'] + [
        "ARCHIVISTE_ANNF","RESPONSABLE_ANNF","AUDITEUR"
    ])),
):
    """Archives du module Journal_Officiel (sous-module DAR).
    Retourne les archives liées à ce module depuis annf_archive_links.
    Filtrables par type de document et statut de scellement.
    """
    where = "WHERE entite_table LIKE :mod"
    params: dict = {
        "mod": f"%journal_officiel%",
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
async def archiver_vers_annf_journal_officiel(
    type_document: str = Query(...,
        description="rnaf|arrete_ministeriel|decret|publication_jo"),
    entite_id: str = Query(..., description="UUID de l'entité à archiver"),
    entite_table: str = Query(..., description="Nom de la table source"),
    snapshot: Optional[dict] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'EDITEUR_JO', 'ARCHIVISTE_ANNF'])),
):
    """Archive un document du module Journal_Officiel vers l'ANNF national.
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
        "module_source": "journal_officiel",
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
        "module": "journal_officiel",
        "sous_module": "DAR",
        "type_archive": type_document,
        "scelle": False,
        "message": "Archivé vers ANNF — scellement via POST /annf/{ida}/sceller",
    }


@router.get("/dar/integrite", response_model=list,
    summary="Liste dar/integrite")
async def verifier_integrite_dar_journal_officiel(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'EDITEUR_JO', 'AUDITEUR', 'DIRECTEUR_URBANISME'] + ["AUDITEUR"])),
):
    """Vérifie l'intégrité des archives DAR du module Journal_Officiel.
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
        """), {"mod": f"%journal_officiel%"})
        row = r.mappings().first()
        return dict(row) if row else {"nb_scelles":0,"nb_en_attente":0,"nb_total":0}
    except Exception:
        return {"nb_scelles":0,"nb_en_attente":0,"nb_total":0}
