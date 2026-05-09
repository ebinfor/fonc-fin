"""
FONCIER+ — Module ANNF COMPLET
Archives Nationales Numériques Foncières — dépôt légal numérique de la République du Niger.

Tables couvertes :
  annf_archive_links (004)     — archives WORM toutes entités (IDA atomique)
  annf_right_archive (005)     — archives spécialisées droits fonciers
  archive_lotissement (008)    — archives opérations de lotissement
  anomalie_fonciere (013)      — anomalies détectées + correction tracée
  indicateur_national (013)    — KPIs fonciers nationaux (10 types)
  historique_indicateur (013)  — variations des KPIs dans le temps
  sauvegarde_systeme (013)     — sauvegardes DB (AES-256, SHA-256)
  restauration_systeme (013)   — restaurations et plans de continuité
  parcel_public_history (004)  — historique public des statuts parcelles

Vues exploitées :
  v_integrite_hash (016)         — détection ruptures chaîne SHA-256
  v_etat_trimestriel_courant (021) — KPIs workflow par trimestre courant

Fonctions PL/pgSQL :
  generate_annf_ida()            — IDA atomique ANNF-YYYY-NNNNNNN
  generer_etat_trimestriel()     — rapport JSONB par trimestre historique

Workflow :
  WF27 — Archivage WORM définitif (4 étapes)
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user
from app.models.workflows import ANNFArchiveLink, TypeArchiveANNF
from app.models.droits_fonciers import ANNFRightArchive
from app.services.workflow_orchestrator import ANNFArchiveService

router = APIRouter(prefix="/annf", tags=["ANNF — Archives Nationales"])

ROLES_ANNF  = ["ADMIN", "ARCHIVISTE_ANNF", "RESPONSABLE_ANNF"]
ROLES_DIR   = ["ADMIN", "RESPONSABLE_ANNF"]
ROLES_READ  = ["ADMIN", "ARCHIVISTE_ANNF", "RESPONSABLE_ANNF",
               "AUDITEUR", "DIRECTEUR_CADASTRE", "JUGE_FONCIER",
               "DIRECTEUR_URBANISME", "NOTAIRE"]

TYPES_ARCHIVE = {t.value for t in TypeArchiveANNF}


def _sha(*parts) -> str:
    return hashlib.sha256(
        "|".join(str(p) for p in parts).encode()
    ).hexdigest()


# ══════════════════════════════════════════════════════════════════
# SCHÉMAS
# ══════════════════════════════════════════════════════════════════

class ArchivageIn(BaseModel):
    type_archive: str = Field(...,
        description="rnaf|rnp|ccfm|acte_notarie|decision_justice|"
                    "bgu_scelle|plan_cadastral")
    entite_id: str = Field(..., min_length=3,
        description='UUID ou identifiant de l entité à archiver')
    entite_table: str = Field(...,
        description="Nom de la table source ex: rnaf, actes_notaries")
    snapshot: dict = Field(default={},
        description="État complet de l'entité au moment de l'archivage")


class AnomalieIn(BaseModel):
    type_anomalie: str = Field(...,
        description="double_propriete|parcelle_sans_rnaf|rnaf_sans_ccfm|"
                    "geometrie_invalide|mutation_sur_parcelle_gelee|"
                    "nicad_invalide|surface_incoh|droit_orphelin|"
                    "ccfm_expire|hypotheque_sans_mainlevee")
    gravite: str = Field(...,
        description="faible|moderee|elevee|critique|bloquante")
    parcelle_id: Optional[str] = None
    entite_type: Optional[str] = None
    entite_id: Optional[str] = None
    nicad: Optional[str] = None
    description: str = Field(..., min_length=10)
    valeur_mesuree: Optional[dict] = None
    action_requise: Optional[str] = None


class CorrectionAnomalieIn(BaseModel):
    motif_correction: str = Field(..., min_length=10)


class IndicateurIn(BaseModel):
    type_indicateur: str = Field(...,
        description="nb_parcelles_actives|taux_litiges|zones_saturees|"
                    "delai_moyen_ccfm|nb_mutations_mois|nb_ccfm_valides|"
                    "taux_regularisation|nb_expropriations|"
                    "nb_successions_ouvertes|couverture_bgu")
    valeur: float = Field(..., ge=0,
        description='Valeur de l indicateur — doit être >= 0')
    unite: str = Field(..., description="nombre|pourcentage|jours|m2|fcfa")
    region_id: Optional[str] = None
    commune_id: Optional[str] = None
    periode: str = Field(..., description="YYYY-MM ou YYYY")
    methode_calcul: Optional[str] = None


class SauvegardeIn(BaseModel):
    type_sauvegarde: str = Field(...,
        description="complete|incrementale|differentielle|wal")
    emplacement: str = Field(..., min_length=5)
    emplacement_secondaire: Optional[str] = None
    taille_octets: Optional[int] = None
    nb_tables: Optional[int] = None
    sha256_archive: Optional[str] = None
    chiffrement: str = Field(default="AES-256")
    retention_jours: int = Field(default=90, ge=1)


# ══════════════════════════════════════════════════════════════════
# TABLEAU DE BORD ANNF
# ══════════════════════════════════════════════════════════════════

@router.get("/tableau-de-bord", response_model=list)
async def tableau_de_bord_annf(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_ANNF + ["AUDITEUR"])),
):
    """Vue consolidée de l'ANNF nationale."""
    stats: dict = {}
    queries = {
        "archives_totales":
            "SELECT COUNT(*) FROM annf_archive_links",
        "archives_scellees":
            "SELECT COUNT(*) FROM annf_archive_links WHERE scelle=true",
        "archives_en_attente":
            "SELECT COUNT(*) FROM annf_archive_links WHERE scelle=false",
        "droits_archives":
            "SELECT COUNT(*) FROM annf_right_archive",
        "anomalies_actives":
            "SELECT COUNT(*) FROM anomalie_fonciere WHERE corrige=false",
        "anomalies_critiques":
            "SELECT COUNT(*) FROM anomalie_fonciere "
            "WHERE corrige=false AND gravite IN ('critique','bloquante')",
        "sauvegardes_recentes":
            "SELECT COUNT(*) FROM sauvegarde_systeme "
            "WHERE date_sauvegarde >= NOW()-INTERVAL '7 days' AND statut='reussie'",
        "integrite_ko":
            "SELECT COUNT(*) FROM v_integrite_hash WHERE NOT integre",
    }
    for key, sql in queries.items():
        try:
            r = await db.execute(text(sql))
            stats[key] = r.scalar() or 0
        except Exception:
            stats[key] = 0

    # Dernières archives scellées (5)
    recentes = []
    try:
        r = await db.execute(text("""
            SELECT id, ida, type_archive, scelle_at
            FROM annf_archive_links
            WHERE scelle=true
            ORDER BY scelle_at DESC LIMIT 5
        """))
        recentes = [dict(row) for row in r.mappings().all()]
    except Exception:
        pass

    return {
        "acteur": current_user.email,
        "role": current_user.role,
        "module": "ANNF — Archives Nationales Numériques Foncières",
        "stats": stats,
        "dernieres_archives_scellees": recentes,
        "horodatage": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════
# WF27 — ARCHIVAGE WORM
# ══════════════════════════════════════════════════════════════════

@router.post("/archiver", status_code=201)
async def archiver_entite(
    payload: ArchivageIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_ANNF)),
):
    """Archive une entité foncière (étapes 1-3 WF27).

    Génère un IDA atomique ANNF-YYYY-NNNNNNN via generate_annf_ida().
    L'archive est non scellée — appeler POST /annf/{ida}/sceller
    pour le scellement WORM définitif (étape 4 WF27).
    """
    if payload.type_archive not in TYPES_ARCHIVE:
        raise HTTPException(422,
            f"type_archive : {sorted(TYPES_ARCHIVE)}")

    try:
        type_a = TypeArchiveANNF(payload.type_archive)
        archive = await ANNFArchiveService.archiver(
            db=db,
            type_archive=type_a,
            entite_id=payload.entite_id,
            entite_table=payload.entite_table,
            snapshot=payload.snapshot,
            archive_par_id=str(current_user.id),
        )
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(422, str(e)[:300])

    return {
        "id": str(archive.id),
        "ida": archive.ida,
        "type_archive": payload.type_archive,
        "sha256_snapshot": archive.sha256_snapshot,
        "version": archive.version,
        "scelle": False,
        "message": "Archive créée — scellement WORM requis via POST /annf/{ida}/sceller",
    }


@router.post("/{ida}/sceller")
async def sceller_archive(
    ida: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_DIR)),
):
    """Scelle définitivement une archive — IRRÉVERSIBLE (WORM).

    Seul le RESPONSABLE_ANNF peut sceller.
    Après scellement : aucune modification possible de ce document.
    """
    r = await db.execute(
        select(ANNFArchiveLink).where(ANNFArchiveLink.ida == ida)
    )
    archive = r.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, f"Archive IDA {ida} introuvable")
    if archive.scelle:
        raise HTTPException(400,
            f"Archive {ida} déjà scellée le "
            f"{archive.scelle_at.isoformat()} — WORM irréversible")

    try:
        archive = await ANNFArchiveService.sceller(
            db=db,
            archive_id=str(archive.id),
            scelle_par_id=str(current_user.id),
        )
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(422, str(e)[:300])

    return {
        "ida": ida,
        "scelle": True,
        "scelle_at": archive.scelle_at.isoformat(),
        "sha256_snapshot": archive.sha256_snapshot,
        "message": "Archive WORM — immuable définitivement",
    }


@router.get("/archives", response_model=list)
async def lister_archives(
    type_archive: Optional[str] = Query(None),
    scelle: Optional[bool] = Query(None),
    entite_table: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Liste les archives ANNF avec filtres."""
    filters = []
    if type_archive: filters.append(ANNFArchiveLink.type_archive == type_archive)
    if scelle is not None: filters.append(ANNFArchiveLink.scelle == scelle)
    if entite_table: filters.append(ANNFArchiveLink.entite_table == entite_table)

    q = select(ANNFArchiveLink)
    if filters: q = q.where(and_(*filters))
    q = q.order_by(ANNFArchiveLink.created_at.desc())
    q = q.offset((page-1)*limit).limit(limit)

        # Filtre acteur — ScopeFilter
    try:
        from app.core.scope_filter import ScopeFilter as _SF
        _scope = _SF.from_user(current_user)
        if not _scope.peut_voir_national:
            if _scope.region:
                where += " AND a.region = :_scope_region"
                params["_scope_region"] = _scope.region
            elif _scope.entite_id:
                where += " AND a.region = :_scope_entite"
                params["_scope_entite"] = _scope.entite_id
    except Exception:
        pass
    r = await db.execute(q)
    return [{
        "id": str(a.id), "ida": a.ida,
        "type_archive": a.type_archive,
        "entite_id": str(a.entite_id),
        "entite_table": a.entite_table,
        "sha256_snapshot": a.sha256_snapshot,
        "scelle": a.scelle,
        "scelle_at": a.scelle_at.isoformat() if a.scelle_at else None,
        "version": a.version,
        "created_at": a.created_at.isoformat(),
    } for a in r.scalars().all()]


@router.get("/archives/{ida}", response_model=dict)
async def get_archive_par_ida(
    ida: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Récupère une archive par son IDA avec son snapshot complet."""
    try:
        r = await db.execute(
            select(ANNFArchiveLink).where(ANNFArchiveLink.ida == ida)
        )
        archive = r.scalar_one_or_none()
        if not archive:
            raise HTTPException(404, f"IDA {ida} introuvable")
        return {
            "id": str(archive.id), "ida": archive.ida,
            "type_archive": archive.type_archive,
            "entite_id": str(archive.entite_id),
            "entite_table": archive.entite_table,
            "snapshot_json": archive.snapshot_json,
            "sha256_snapshot": archive.sha256_snapshot,
            "scelle": archive.scelle,
            "scelle_at": archive.scelle_at.isoformat() if archive.scelle_at else None,
            "version": archive.version,
            "version_precedente_id": str(archive.version_precedente_id)
                                      if archive.version_precedente_id else None,
        }

    except Exception as e:
        import logging as _l
        _l.getLogger('annf').warning('get_archive_par_ida: %s', e)
        raise HTTPException(status_code=500,
            detail=str(e)[:200])

@router.get("/archives/entite/{entite_id}", response_model=dict)
async def get_archives_entite(
    entite_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Toutes les versions archivées d'une entité (historique de versioning)."""
    try:
        r = await db.execute(
            select(ANNFArchiveLink)
            .where(ANNFArchiveLink.entite_id == entite_id)
            .order_by(ANNFArchiveLink.version.desc())
        )
        archives = r.scalars().all()
        return {
            "entite_id": entite_id,
            "nb_versions": len(archives),
            "archives": [{
                "ida": a.ida, "version": a.version,
                "sha256_snapshot": a.sha256_snapshot,
                "scelle": a.scelle,
                "scelle_at": a.scelle_at.isoformat() if a.scelle_at else None,
                "created_at": a.created_at.isoformat(),
            } for a in archives]
        }


    # ══════════════════════════════════════════════════════════════════
    # ARCHIVES DROITS FONCIERS (annf_right_archive)
    # ══════════════════════════════════════════════════════════════════
    except Exception as e:
        import logging as _l
        _l.getLogger('annf').warning('get_archives_entite: %s', e)
        raise HTTPException(status_code=500,
            detail=str(e)[:200])

@router.post("/droits-fonciers", status_code=201)
async def archiver_droit_foncier(
    right_version_id: str = Query(...),
    source: str = Query(...,
        description="notaire|justice|domaine|ccfm|admin"),
    snapshot: dict = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_ANNF)),
):
    """Archive une version de droit foncier dans annf_right_archive."""
    SOURCES = {"notaire","justice","domaine","ccfm","admin"}
    if source not in SOURCES:
        raise HTTPException(422, f"source : {SOURCES}")

    sha = _sha(right_version_id, source, str(datetime.now(timezone.utc)))
    snap = snapshot or {"right_version_id": right_version_id,
                        "archived_at": datetime.now(timezone.utc).isoformat()}
    snap_str = json.dumps(snap, sort_keys=True, default=str)

    try:
        # IDA via PostgreSQL
        r_ida = await db.execute(text("SELECT generate_annf_ida()"))
        ida = r_ida.scalar()

        # Créer annf_archive_links
        link = ANNFArchiveLink(
            id=uuid.uuid4(),
            type_archive=TypeArchiveANNF.RNP,
            entite_id=right_version_id,
            entite_table="parcel_right_version",
            snapshot_json=snap,
            sha256_snapshot=sha,
            ida=ida,
            scelle=False,
            version=1,
            archive_par=str(current_user.id),
        )
        db.add(link)
        await db.flush()

        # Créer annf_right_archive
        droit_archive = ANNFRightArchive(
            id=uuid.uuid4(),
            right_version_id=right_version_id,
            annf_link_id=link.id,
            hash_integrite=sha,
            version=1,
            source=source,
            snapshot_json=snap,
            scelle=False,
            archive_par=str(current_user.id),
        )
        db.add(droit_archive)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    return {
        "id": str(droit_archive.id),
        "ida": ida,
        "right_version_id": right_version_id,
        "sha256": sha,
        "scelle": False,
    }


@router.get("/droits-fonciers/{right_version_id}", response_model=dict)
async def get_archive_droit(
    right_version_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Archive ANNF d'une version de droit foncier."""
    try:
        r = await db.execute(
            select(ANNFRightArchive)
            .where(ANNFRightArchive.right_version_id == right_version_id)
            .order_by(ANNFRightArchive.version.desc())
        )
        archives = r.scalars().all()
        if not archives:
            raise HTTPException(404, "Droit non archivé")
        return [{
            "id": str(a.id),
            "version": a.version,
            "source": a.source,
            "hash_integrite": a.hash_integrite,
            "scelle": a.scelle,
            "scelle_at": a.scelle_at.isoformat() if a.scelle_at else None,
            "date_archivage": a.date_archivage.isoformat(),
        } for a in archives]


    # ══════════════════════════════════════════════════════════════════
    # INTÉGRITÉ — v_integrite_hash (migration 016)
    # ══════════════════════════════════════════════════════════════════
    except Exception as e:
        import logging as _l
        _l.getLogger('annf').warning('get_archive_droit: %s', e)
        raise HTTPException(status_code=500,
            detail=str(e)[:200])

@router.get("/integrite", response_model=list)
async def verifier_integrite_nationale(
    table_name: Optional[str] = Query(None,
        description="notarial_act|ccfm_verification_log — NULL=toutes"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_ANNF + ["AUDITEUR"])),
):
    """Vérifie l'intégrité de toutes les chaînes SHA-256 via v_integrite_hash.
    Détecte toute rupture indiquant une falsification potentielle.
    """
    try:
        where = ""
        params: dict = {}
        if table_name:
            where = "WHERE table_name=:t"; params["t"] = table_name

        r = await db.execute(text(
            f"SELECT * FROM v_integrite_hash {where} "
            "ORDER BY table_name, hash_actuel"
        ), params)
        rows = [dict(row) for row in r.mappings().all()]

        ruptures = [row for row in rows if not row.get("integre")]
        total = len(rows)

        return {
            "total_entrees_verifiees": total,
            "nb_ruptures": len(ruptures),
            "integrite_globale": len(ruptures) == 0,
            "ruptures": ruptures[:50],
            "message": "INTÉGRITÉ OK" if not ruptures
                       else f"⚠ {len(ruptures)} RUPTURE(S) DÉTECTÉE(S)",
        }
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


# ══════════════════════════════════════════════════════════════════
# ANOMALIES FONCIÈRES (migration 013)
# ══════════════════════════════════════════════════════════════════

@router.post("/anomalies", status_code=201)
async def signaler_anomalie(
    payload: AnomalieIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_ANNF + ["AUDITEUR"])),
):
    """Signale une anomalie foncière détectée."""
    TYPES = {"double_propriete","parcelle_sans_rnaf","rnaf_sans_ccfm",
             "geometrie_invalide","mutation_sur_parcelle_gelee",
             "nicad_invalide","surface_incoh","droit_orphelin",
             "ccfm_expire","hypotheque_sans_mainlevee"}
    GRAVITES = {"faible","moderee","elevee","critique","bloquante"}
    if payload.type_anomalie not in TYPES:
        raise HTTPException(422, f"type_anomalie : {TYPES}")
    if payload.gravite not in GRAVITES:
        raise HTTPException(422, f"gravite : {GRAVITES}")

    sha = _sha(payload.type_anomalie,
               payload.parcelle_id or payload.entite_id or "global",
               payload.description[:50])
    ano_id = str(uuid.uuid4())
    try:
        await db.execute(text("""
            INSERT INTO anomalie_fonciere
                (id, type_anomalie, gravite, parcelle_id,
                 entite_type, entite_id, nicad, description,
                 valeur_mesuree, sha256_anomalie,
                 action_requise, corrige, detecte_at)
            VALUES
                (:id,:type,:gravite,:parc,
                 :etype,:eid,:nicad,:desc,
                 :val::jsonb,:sha,
                 :action,false,NOW())
            ON CONFLICT DO NOTHING
        """), {
            "id": ano_id, "type": payload.type_anomalie,
            "gravite": payload.gravite,
            "parc": payload.parcelle_id, "etype": payload.entite_type,
            "eid": payload.entite_id, "nicad": payload.nicad,
            "desc": payload.description,
            "val": json.dumps(payload.valeur_mesuree or {}),
            "sha": sha, "action": payload.action_requise,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"id": ano_id, "sha256": sha, "corrige": False}


@router.get("/anomalies", response_model=list)
async def lister_anomalies(
    type_anomalie: Optional[str] = Query(None),
    gravite: Optional[str] = Query(None),
    corrige: bool = Query(default=False),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Liste les anomalies foncières détectées."""
    where = "WHERE corrige=:c"
    params: dict = {"c": corrige, "l": limit, "o": (page-1)*limit}
    if type_anomalie:
        where += " AND type_anomalie=:t"; params["t"] = type_anomalie
    if gravite:
        where += " AND gravite=:g"; params["g"] = gravite
    try:

        # Filtre acteur — ScopeFilter
        try:
            from app.core.scope_filter import ScopeFilter as _SF
            _scope = _SF.from_user(current_user)
            if not _scope.peut_voir_national:
                if _scope.region:
                    where += " AND a.region = :_scope_region"
                    params["_scope_region"] = _scope.region
                elif _scope.entite_id:
                    where += " AND a.region = :_scope_entite"
                    params["_scope_entite"] = _scope.entite_id
        except Exception:
            pass
        r = await db.execute(text(
            f"SELECT id,type_anomalie,gravite,parcelle_id,nicad,"
            f"description,action_requise,detecte_at "
            f"FROM anomalie_fonciere {where} "
            f"ORDER BY detecte_at DESC LIMIT :l OFFSET :o"
        ), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


@router.patch("/anomalies/{ano_id}/corriger")
async def marquer_anomalie_corrigee(
    ano_id: str,
    payload: CorrectionAnomalieIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_ANNF)),
):
    """Marque une anomalie comme corrigée avec traçabilité complète."""
    try:
        await db.execute(text("""
            UPDATE anomalie_fonciere
            SET corrige=true, corrige_par=:uid,
                corrige_at=NOW(), motif_correction=:motif
            WHERE id=:id AND corrige=false
        """), {"id": ano_id, "uid": str(current_user.id),
               "motif": payload.motif_correction})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"id": ano_id, "corrige": True,
            "corrige_par": current_user.email}


# ══════════════════════════════════════════════════════════════════
# INDICATEURS NATIONAUX (migration 013)
# ══════════════════════════════════════════════════════════════════

@router.post("/indicateurs", status_code=201)
async def enregistrer_indicateur(
    payload: IndicateurIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_ANNF + ["AUDITEUR"])),
):
    """Enregistre un KPI foncier national avec SHA-256 anti-manipulation."""
    TYPES = {"nb_parcelles_actives","taux_litiges","zones_saturees",
             "delai_moyen_ccfm","nb_mutations_mois","nb_ccfm_valides",
             "taux_regularisation","nb_expropriations",
             "nb_successions_ouvertes","couverture_bgu"}
    if payload.type_indicateur not in TYPES:
        raise HTTPException(422, f"type_indicateur : {TYPES}")

    sha = _sha(payload.type_indicateur, str(payload.valeur),
               payload.periode, payload.region_id or "national")
    try:
        await db.execute(text("""
            INSERT INTO indicateur_national
                (id,type_indicateur,valeur,unite,region_id,commune_id,
                 periode,methode_calcul,sha256_calcul,
                 calcule_par,date_calcul)
            VALUES
                (gen_random_uuid(),:type,:val,:unite,:rid,:cid,
                 :per,:meth,:sha,:role,NOW())
            ON CONFLICT (type_indicateur,periode,region_id,commune_id)
            DO UPDATE SET
                valeur=EXCLUDED.valeur,
                sha256_calcul=EXCLUDED.sha256_calcul,
                methode_calcul=EXCLUDED.methode_calcul,
                calcule_par=EXCLUDED.calcule_par,
                date_calcul=NOW()
        """), {
            "type": payload.type_indicateur,
            "val": payload.valeur, "unite": payload.unite,
            "rid": payload.region_id, "cid": payload.commune_id,
            "per": payload.periode, "meth": payload.methode_calcul,
            "sha": sha, "role": current_user.role,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"type_indicateur": payload.type_indicateur,
            "valeur": payload.valeur, "periode": payload.periode,
            "sha256": sha}


@router.get("/indicateurs", response_model=list)
async def consulter_indicateurs(
    type_indicateur: Optional[str] = Query(None),
    periode: Optional[str] = Query(None),
    region_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Consulte les indicateurs nationaux fonciers."""
    where = "WHERE 1=1"
    params: dict = {}
    if type_indicateur:
        where += " AND type_indicateur=:t"; params["t"] = type_indicateur
    if periode:
        where += " AND periode LIKE :p"; params["p"] = f"{periode}%"
    if region_id:
        where += " AND region_id=:rid"; params["rid"] = region_id
    try:
        r = await db.execute(text(
            f"SELECT type_indicateur,valeur,unite,region_id,"
            f"commune_id,periode,date_calcul,sha256_calcul "
            f"FROM indicateur_national {where} "
            f"ORDER BY periode DESC, type_indicateur"
        ), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


@router.get("/indicateurs/etat-trimestriel", response_model=list)
async def etat_trimestriel(
    annee: int = Query(..., ge=2020, le=500),
    trimestre: int = Query(..., ge=1, le=4),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Rapport trimestriel via generer_etat_trimestriel() (migration 021)."""
    try:
        r = await db.execute(text(
            "SELECT generer_etat_trimestriel(:annee, :trim)"
        ), {"annee": annee, "trim": trimestre})
        rapport = r.scalar()
    except Exception as e:
        raise HTTPException(500, str(e)[:200])
    return {
        "annee": annee, "trimestre": trimestre,
        "rapport": rapport or [],
    }


@router.get("/indicateurs/courant", response_model=list)
async def etat_trimestriel_courant(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Vue v_etat_trimestriel_courant — trimestre en cours."""
    try:
        r = await db.execute(text(
            "SELECT * FROM v_etat_trimestriel_courant "
            "ORDER BY type_workflow"
        ))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


# ══════════════════════════════════════════════════════════════════
# SAUVEGARDES SYSTÈME (migration 013)
# ══════════════════════════════════════════════════════════════════

@router.post("/sauvegardes", status_code=201)
async def enregistrer_sauvegarde(
    payload: SauvegardeIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_DIR + ["ARCHIVISTE_ANNF"])),
):
    """Enregistre une sauvegarde système (AES-256, SHA-256 vérifiable)."""
    TYPES = {"complete","incrementale","differentielle","wal"}
    if payload.type_sauvegarde not in TYPES:
        raise HTTPException(422, f"type_sauvegarde : {TYPES}")
    sha = payload.sha256_archive or _sha(
        payload.emplacement, str(payload.taille_octets or 0),
        str(datetime.now(timezone.utc))
    )
    try:
        await db.execute(text("""
            INSERT INTO sauvegarde_systeme
                (id,type_sauvegarde,statut,emplacement,
                 emplacement_secondaire,taille_octets,
                 sha256_archive,chiffrement,retention_jours,
                 date_sauvegarde)
            VALUES
                (gen_random_uuid(),:type,'reussie',:emp,
                 :emp2,:taille,
                 :sha,:chiff,:ret,NOW())
        """), {
            "type": payload.type_sauvegarde,
            "emp": payload.emplacement,
            "emp2": payload.emplacement_secondaire,
            "taille": payload.taille_octets,
            "sha": sha, "chiff": payload.chiffrement,
            "ret": payload.retention_jours,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"type_sauvegarde": payload.type_sauvegarde,
            "sha256_archive": sha, "statut": "reussie"}


@router.get("/sauvegardes", response_model=list)
async def lister_sauvegardes(
    type_sauvegarde: Optional[str] = Query(None),
    statut: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_ANNF + ["AUDITEUR"])),
):
    """Liste des sauvegardes système enregistrées."""
    where = "WHERE 1=1"
    params: dict = {"l": limit, "o": (page-1)*limit}
    if type_sauvegarde:
        where += " AND type_sauvegarde=:t"; params["t"] = type_sauvegarde
    if statut:
        where += " AND statut=:s"; params["s"] = statut
    try:

        # Filtre acteur — ScopeFilter
        try:
            from app.core.scope_filter import ScopeFilter as _SF
            _scope = _SF.from_user(current_user)
            if not _scope.peut_voir_national:
                if _scope.region:
                    where += " AND s.region = :_scope_region"
                    params["_scope_region"] = _scope.region
                elif _scope.entite_id:
                    where += " AND s.region = :_scope_entite"
                    params["_scope_entite"] = _scope.entite_id
        except Exception:
            pass
        r = await db.execute(text(
            f"SELECT id,type_sauvegarde,statut,emplacement,"
            f"taille_octets,sha256_archive,chiffrement,"
            f"retention_jours,date_sauvegarde "
            f"FROM sauvegarde_systeme {where} "
            f"ORDER BY date_sauvegarde DESC LIMIT :l OFFSET :o"
        ), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


# ══════════════════════════════════════════════════════════════════
# HISTORIQUE PUBLIC (parcel_public_history)
# ══════════════════════════════════════════════════════════════════

@router.get("/historique-public/{parcelle_id}", response_model=dict)
async def historique_public_parcelle(
    parcelle_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Historique public des changements de statut d'une parcelle.
    Accessible à tous les acteurs authentifiés (portail citoyen).
    """
    try:
        r = await db.execute(text("""
            SELECT nicad, statut_avant, statut_apres,
                   module_source, motif_public, role_acteur, created_at
            FROM parcel_public_history
            WHERE parcelle_id=:pid
            ORDER BY created_at DESC
            LIMIT :l OFFSET :o
        """), {"pid": parcelle_id, "l": limit, "o": (page-1)*limit})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


# ══════════════════════════════════════════════════════════════════
# STATISTIQUES ANNF
# ══════════════════════════════════════════════════════════════════

@router.get("/stats", response_model=list)
async def stats_module_annf(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Statistiques consolidées du module ANNF."""
    stats: dict = {}
    for key, sql in [
        ("archives_par_type",
         "SELECT type_archive, COUNT(*) nb, "
         "COUNT(*) FILTER (WHERE scelle) nb_scelles "
         "FROM annf_archive_links GROUP BY type_archive ORDER BY nb DESC"),
        ("anomalies_par_gravite",
         "SELECT gravite, COUNT(*) nb, "
         "COUNT(*) FILTER (WHERE corrige) nb_corriges "
         "FROM anomalie_fonciere GROUP BY gravite ORDER BY nb DESC"),
        ("indicateurs_recents",
         "SELECT type_indicateur,valeur,unite,periode "
         "FROM indicateur_national "
         "ORDER BY date_calcul DESC LIMIT 10"),
        ("sauvegardes_par_statut",
         "SELECT statut, COUNT(*) nb, "
         "COALESCE(SUM(taille_octets),0) taille_totale "
         "FROM sauvegarde_systeme GROUP BY statut"),
    ]:
        try:
            r = await db.execute(text(sql))
            stats[key] = [dict(row) for row in r.mappings().all()]
        except Exception:
            stats[key] = []

    return {"stats": stats}


# ══════════════════════════════════════════════════════════════════
# WF27 — PIPELINE ARCHIVAGE WORM via WorkflowEngine
# ══════════════════════════════════════════════════════════════════

@router.post("/wf27/demarrer", status_code=201,
             tags=["ANNF — WF27 Archivage"])
async def demarrer_wf27(
    entite_id: str = Query(...),
    entite_type: str = Query(...,
        description="rnaf|rnp|ccfm|acte_notarie|decision_justice|bgu_scelle|plan_cadastral"),
    entite_table: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_ANNF)),
):
    """Démarre WF27 — Archivage WORM définitif (4 étapes).

    1. detection_inactivite — ARCHIVISTE_ANNF
    2. transfert_archive    — ARCHIVISTE_ANNF
    3. indexation           — ARCHIVISTE_ANNF
    4. scellement_worm      — RESPONSABLE_ANNF (IRRÉVERSIBLE)
    """
    from app.services.workflow_engine import WorkflowEngine
    try:
        instance = await WorkflowEngine.demarrer(
            db=db,
            type_workflow="ARCHIVAGE_DEFINITIF",
            entite_type=entite_type,
            entite_id=entite_id,
            demarre_par_id=str(current_user.id),
            contexte={"entite_table": entite_table,
                      "demarre_par": current_user.email},
        )
        await db.commit()
    except ValueError as e:
        await db.rollback()
        raise HTTPException(422, str(e))
    return {
        "instance_id": str(instance.id),
        "statut": instance.statut,
        "etape_courante": instance.etape_courante_code,
        "attendu_de_role": instance.attendu_de_role,
        "message": "WF27 démarré — 4 étapes jusqu au scellement WORM",
    }


@router.get("/wf27/{instance_id}/etat",
            tags=["ANNF — WF27 Archivage"])
async def etat_wf27(
    instance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """État courant d une instance WF27."""
    from app.services.workflow_engine import WorkflowEngine
    try:
        return await WorkflowEngine.get_etat(db, instance_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
