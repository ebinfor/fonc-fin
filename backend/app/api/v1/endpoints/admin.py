"""
FONCIER+ -- Administration nationale
Expose les composants architecturaux de la migration 031 :
  - Institutions et hierarchie
  - Decision Engine
  - Audit immuable
  - Entity Locks
  - Validations multi-niveaux
  - Delegations administratives
  - DRP
  - Interoperabilite
"""
import json
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user

router = APIRouter(prefix="/admin", tags=["Administration nationale"])

_ADMIN  = ["ADMIN"]
_SUPER  = ["ADMIN", "DIRECTEUR_URBANISME", "SECRETAIRE_GENERAL", "AUDITEUR"]


# ── Institutions ──────────────────────────────────────────────────

class InstitutionIn(BaseModel):
    code:              str = Field(..., min_length=2, max_length=20)
    nom:               str = Field(..., min_length=3)
    type_institution:  str
    region:            Optional[str] = None
    parent_id:         Optional[str] = None
    niveau_hierarchique: int = Field(default=1, ge=1, le=4)


@router.post("/institutions", status_code=201)
async def creer_institution(
    payload: InstitutionIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Cree une institution (ministere, direction, commune...)."""
    try:
        r = await db.execute(text("""
            INSERT INTO institutions (code, nom, type_institution, region, parent_id, niveau_hierarchique)
            VALUES (:code, :nom, :type, :region, :parent::uuid, :niveau)
            RETURNING id
        """), {
            "code": payload.code, "nom": payload.nom,
            "type": payload.type_institution, "region": payload.region,
            "parent": payload.parent_id, "niveau": payload.niveau_hierarchique,
        })
        iid = r.scalar()
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"id": str(iid), "code": payload.code}


@router.get("/institutions", response_model=list)
async def lister_institutions(
    region: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Liste des institutions, filtrable par region."""
    where = "WHERE actif=TRUE"
    params: dict = {}
    if region:
        where += " AND region=:reg"; params["reg"] = region
    try:
        # ScopeFilter
        try:
            from app.core.scope_filter import ScopeFilter as _S
            _sc = _S.from_user(current_user)
            if not _sc.peut_voir_national and _sc.region:
                where += ' AND i.region = :_scope_region'
                params['_scope_region'] = _sc.region
        except Exception: pass
        r = await db.execute(text(f"""
            SELECT id, code, nom, type_institution, region,
                   niveau_hierarchique, actif, created_at
            FROM institutions {where}
            ORDER BY niveau_hierarchique, nom
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("lister_institutions: %s", e)
        return []


# ── Agent Profil ──────────────────────────────────────────────────

class AgentProfilIn(BaseModel):
    user_id:                 str
    institution_id:          str
    region_principale:       str
    regions_autorisees:      List[str] = []
    niveau_autorite:         int = Field(default=1, ge=1, le=4)
    peut_valider_hors_region: bool = False


@router.post("/agents/profil", status_code=201)
async def creer_profil_agent(
    payload: AgentProfilIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Cree ou met a jour le profil institutionnel d un agent."""
    try:
        await db.execute(text("""
            INSERT INTO agent_profil (
                user_id, institution_id, region_principale,
                regions_autorisees, niveau_autorite, peut_valider_hors_region
            ) VALUES (
                :uid::uuid, :iid::uuid, :reg,
                :regs, :niveau, :hors
            )
            ON CONFLICT (user_id) DO UPDATE SET
                institution_id=EXCLUDED.institution_id,
                region_principale=EXCLUDED.region_principale,
                regions_autorisees=EXCLUDED.regions_autorisees,
                niveau_autorite=EXCLUDED.niveau_autorite,
                peut_valider_hors_region=EXCLUDED.peut_valider_hors_region
        """), {
            "uid": payload.user_id, "iid": payload.institution_id,
            "reg": payload.region_principale,
            "regs": payload.regions_autorisees or [],
            "niveau": payload.niveau_autorite,
            "hors": payload.peut_valider_hors_region,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"user_id": payload.user_id, "message": "Profil agent cree"}


@router.get("/agents/juridictions", response_model=list)
async def lister_agents_juridictions(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Vue agents et leurs juridictions."""
    try:
        r = await db.execute(text("SELECT * FROM v_agents_juridictions"))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("lister_agents: %s", e)
        return []


# ── Decision Engine ───────────────────────────────────────────────

@router.post("/decision-engine/evaluer")
async def evaluer_decision(
    entite_type: str = Query(...),
    entite_id:   str = Query(...),
    operation:   str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Evalue le Decision Engine pour une entite et une operation."""
    from app.services.decision_engine import DecisionEngine
    result = await DecisionEngine.evaluer(
        db=db, entite_type=entite_type, entite_id=entite_id,
        operation=operation, user_id=str(current_user["id"]),
    )
    return result.as_dict()


@router.get("/decision-engine/dashboard", response_model=list)
async def dashboard_decision_engine(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Tableau de bord Decision Engine (7 derniers jours)."""
    try:
        r = await db.execute(text("SELECT * FROM v_decision_engine_dashboard"))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("de_dashboard: %s", e)
        return []


# ── Regles metier ─────────────────────────────────────────────────

class RegleMetierIn(BaseModel):
    code:          str = Field(..., min_length=3)
    nom:           str = Field(..., min_length=3)
    domaine:       str
    condition_sql: str = Field(..., min_length=10)
    niveau_blocage: str = Field(default="BLOQUANT")
    description:   Optional[str] = None
    effectif_depuis: Optional[str] = None


@router.post("/regles-metier", status_code=201)
async def creer_regle_metier(
    payload: RegleMetierIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Cree une nouvelle regle metier. Historise automatiquement."""
    try:
        r = await db.execute(text("""
            INSERT INTO regle_metier (
                code, nom, domaine, condition_sql,
                niveau_blocage, description, created_by
            ) VALUES (
                :code, :nom, :dom, :sql, :niveau, :desc, :uid::uuid
            ) RETURNING id, version
        """), {
            "code": payload.code, "nom": payload.nom, "dom": payload.domaine,
            "sql": payload.condition_sql, "niveau": payload.niveau_blocage,
            "desc": payload.description, "uid": str(current_user["id"]),
        })
        row = r.first()
        rid, ver = row[0], row[1]
        # Historiser
        await db.execute(text("""
            INSERT INTO regle_version (regle_id, version, condition_sql, niveau_blocage, motif_changement, modifie_par)
            VALUES (:rid, :ver, :sql, :niveau, 'Creation', :uid::uuid)
        """), {
            "rid": rid, "ver": ver, "sql": payload.condition_sql,
            "niveau": payload.niveau_blocage, "uid": str(current_user["id"]),
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"id": str(rid), "code": payload.code, "version": ver}


@router.get("/regles-metier", response_model=list)
async def lister_regles(
    domaine: Optional[str] = Query(None),
    actif: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Liste les regles metier actives."""
    try:
        where = "WHERE rm.actif=:actif"
        params: dict = {"actif": actif}
        if domaine:
            where += " AND rm.domaine=:dom"; params["dom"] = domaine
        r = await db.execute(text(f"""
            SELECT rm.id, rm.code, rm.nom, rm.domaine,
                   rm.niveau_blocage, rm.version, rm.actif,
                   rm.effectif_depuis, rm.description
            FROM regle_metier rm {where}
            ORDER BY rm.domaine, rm.code
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("lister_regles: %s", e)
        return []


# ── Audit immuable ────────────────────────────────────────────────

@router.get("/audit/journal", response_model=list)
async def journal_audit(
    entite_type: Optional[str] = Query(None),
    entite_id:   Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER + ["AUDITEUR"])),
):
    """Consulte le journal d audit immuable."""
    try:
        where = "WHERE 1=1"
        params: dict = {"l": limit, "o": (page-1)*limit}
        if entite_type: where += " AND entite_type=:et";  params["et"] = entite_type
        if entite_id:   where += " AND entite_id=:eid::uuid"; params["eid"] = entite_id
        r = await db.execute(text(f"""
            SELECT seq, entite_type, entite_id, operation,
                   effectue_par, region, sha256_courant, created_at
            FROM audit_immutable {where}
            ORDER BY seq DESC LIMIT :l OFFSET :o
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("journal_audit: %s", e)
        return []


@router.get("/audit/integrite", response_model=list)
async def verifier_integrite_audit(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN + ["AUDITEUR"])),
):
    """Verifie l integrite du hash chaining de l audit immuable."""
    try:
        r = await db.execute(text("""
            SELECT COUNT(*) FILTER (WHERE chaine_integre=FALSE) AS ruptures,
                   COUNT(*) AS total
            FROM v_audit_integrite
        """))
        row = r.first()
        ruptures, total = row[0] or 0, row[1] or 0
        return {
            "integre":  ruptures == 0,
            "ruptures": ruptures,
            "total":    total,
            "message":  "Chaine integre" if ruptures == 0 else f"{ruptures} rupture(s) detectee(s)",
        }
    except Exception as e:
        import logging; logging.getLogger("admin").warning("integrite_audit: %s", e)
        return {"integre": None, "message": "Verification non disponible"}


# ── Entity Locks ──────────────────────────────────────────────────

@router.get("/locks", response_model=list)
async def lister_verrous(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Liste tous les verrous d entites actifs."""
    try:
        r = await db.execute(text("SELECT * FROM v_entity_locks_actifs"))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("lister_locks: %s", e)
        return []


@router.delete("/locks/{entite_type}/{entite_id}")
async def liberer_verrou_admin(
    entite_type: str,
    entite_id:   str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Force la libération d'un verrou actif (ADMIN uniquement).
    Retourne 404 si aucun verrou actif n'est trouvé pour cette entité.
    """
    from app.services.decision_engine import DecisionEngine
    uid = (current_user.get("id","") if isinstance(current_user, dict)
           else getattr(current_user, "id", ""))
    ok = await DecisionEngine.liberer_verrou(
        db=db, entite_type=entite_type,
        entite_id=entite_id, user_id=str(uid)
    )
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Aucun verrou actif trouvé pour {entite_type}/{entite_id}"
        )
    return {"libere": True, "entite_type": entite_type, "entite_id": entite_id}


# ── Delegations ───────────────────────────────────────────────────

class DelegationIn(BaseModel):
    delegataire_id:   str
    type_delegation:  str = Field(..., description="SIGNATURE|VALIDATION|SUPERVISION|REMPLACEMENT_COMPLET")
    domaine:          str
    date_fin:         str = Field(..., description="ISO datetime YYYY-MM-DDTHH:MM:SS")
    motif:            str = Field(..., min_length=5)
    region_concernee: Optional[str] = None


@router.post("/delegations", status_code=201)
async def creer_delegation(
    payload: DelegationIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Cree une delegation administrative temporaire."""
    import hashlib
    sha = hashlib.sha256(
        f"{current_user['id']}|{payload.delegataire_id}|{payload.type_delegation}|{payload.date_fin}".encode()
    ).hexdigest()
    try:
        r = await db.execute(text("""
            INSERT INTO delegation_administrative (
                delegant_id, delegataire_id, type_delegation,
                domaine, region_concernee, date_fin, motif,
                sha256_delegation, statut
            ) VALUES (
                :did::uuid, :dtid::uuid, :type,
                :dom, :reg, :fin::timestamp, :motif,
                :sha, 'EN_ATTENTE'
            ) RETURNING id
        """), {
            "did": str(current_user["id"]), "dtid": payload.delegataire_id,
            "type": payload.type_delegation, "dom": payload.domaine,
            "reg": payload.region_concernee, "fin": payload.date_fin,
            "motif": payload.motif, "sha": sha,
        })
        did = r.scalar()
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"id": str(did), "sha256": sha, "statut": "EN_ATTENTE"}


@router.get("/delegations/actives", response_model=list)
async def lister_delegations_actives(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Liste toutes les delegations actives."""
    try:
        r = await db.execute(text("SELECT * FROM v_delegations_actives"))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("delegations: %s", e)
        return []


@router.patch("/delegations/{delegation_id}/activer")
async def activer_delegation(
    delegation_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN + ["DIRECTEUR_URBANISME"])),
):
    """Active une delegation en attente (validation hierarchique)."""
    try:
        await db.execute(text("""
            UPDATE delegation_administrative
            SET statut='ACTIVE', valide_par=:vid::uuid, valide_at=NOW()
            WHERE id=:did::uuid AND statut='EN_ATTENTE'
        """), {"vid": str(current_user["id"]), "did": delegation_id})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"delegation_id": delegation_id, "statut": "ACTIVE"}


# ── Validation multi-niveaux ──────────────────────────────────────

@router.get("/validations/pipeline/{entite_type}/{entite_id}", response_model=dict)
async def pipeline_validation(
    entite_type: str,
    entite_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Etat du pipeline de validation multi-niveaux d une entite."""
    try:
        r = await db.execute(text("""
            SELECT id, workflow_type, niveau_requis, niveau_courant, statut,
                   valide_local_at, valide_dept_at, valide_regional_at, valide_national_at,
                   sha256_chain, created_at
            FROM validation_pipeline
            WHERE entite_type=:et AND entite_id=:eid::uuid
            ORDER BY created_at DESC LIMIT 5
        """), {"et": entite_type, "eid": entite_id})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("pipeline_validation: %s", e)
        return []


@router.post("/validations/{pipeline_id}/valider")
async def valider_niveau(
    pipeline_id: str,
    commentaire: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Valide le niveau courant du pipeline (selon le niveau_autorite de l agent)."""
    from app.services.decision_engine import DecisionEngine
    result = await DecisionEngine.valider_niveau(
        db=db, pipeline_id=pipeline_id,
        validateur_id=str(current_user["id"]),
        commentaire=commentaire,
    )
    return {"pipeline_id": pipeline_id, "resultat": result}


# ── DRP ───────────────────────────────────────────────────────────

@router.get("/drp/backups", response_model=list)
async def lister_backups(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Journal des sauvegardes DRP."""
    try:
        r = await db.execute(text("""
            SELECT * FROM drp_backup_log ORDER BY created_at DESC LIMIT 100
        """))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("drp_backups: %s", e)
        return []


@router.get("/drp/replication", response_model=list)
async def statut_replication(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Statut de la replication (replicas DRP)."""
    try:
        r = await db.execute(text(
            "SELECT * FROM drp_replication_status ORDER BY verifie_at DESC"
        ))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("drp_replication: %s", e)
        return []


# ── Interoperabilite ──────────────────────────────────────────────

@router.get("/integrations", response_model=list)
async def lister_integrations(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Liste les integrations externes configurees."""
    try:
        r = await db.execute(text("""
            SELECT id, code, nom, type_systeme, base_url,
                   format_echange, auth_type, actif, created_at
            FROM integration_externe ORDER BY nom
        """))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("integrations: %s", e)
        return []


@router.get("/integrations/{integration_id}/webhooks", response_model=dict)
async def webhooks_integration(
    integration_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Historique des appels webhook d une integration."""
    try:
        r = await db.execute(text("""
            SELECT direction, evenement, statut_http, succes, duree_ms, created_at
            FROM webhook_log WHERE integration_id=:iid::uuid
            ORDER BY created_at DESC LIMIT 200
        """), {"iid": integration_id})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("webhooks: %s", e)
        return []


# ── Sante globale ─────────────────────────────────────────────────

@router.get("/sante", response_model=list)
async def sante_architecture(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER + ["AUDITEUR"])),
):
    """Bilan sante de tous les composants architecturaux."""
    sante = {}
    checks = {
        "institutions":     "SELECT COUNT(*) FROM institutions WHERE actif=TRUE",
        "agents_profils":   "SELECT COUNT(*) FROM agent_profil WHERE actif=TRUE",
        "regles_metier":    "SELECT COUNT(*) FROM regle_metier WHERE actif=TRUE",
        "verrous_actifs":   "SELECT COUNT(*) FROM entity_lock WHERE actif=TRUE AND date_expiration>NOW()",
        "audit_entrees":    "SELECT COUNT(*) FROM audit_immutable",
        "delegations":      "SELECT COUNT(*) FROM delegation_administrative WHERE statut='ACTIVE' AND date_fin>NOW()",
        "validations_cours":"SELECT COUNT(*) FROM validation_pipeline WHERE statut NOT IN ('VALIDE','REJETE')",
        "integrations":     "SELECT COUNT(*) FROM integration_externe WHERE actif=TRUE",
        "territories":      "SELECT COUNT(*) FROM territoire_juridiction WHERE actif=TRUE",
    }
    for key, sql in checks.items():
        try:
            r = await db.execute(text(sql))
            sante[key] = r.scalar() or 0
        except Exception:
            sante[key] = "N/A"

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "composants": sante,
        "status": "OK",
    }


# ═══════════════════════════════════════════════════════════════════
# SQL SANDBOX -- Validation securisee des regles metier
# ═══════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════
# SQL SANDBOX — Validation sécurisée des règles métier (4 couches)
# Architecture : C1 Statique → C2 Sémantique → C3 Sandbox DB → C4 SHA-256
# ═══════════════════════════════════════════════════════════════════

class ValiderRegleIn(BaseModel):
    """Valider un lot de règles SQL après contrôle sandbox."""
    sql:            str = Field(..., min_length=3,  max_length=2000,
                                description="Condition SQL booléenne à valider")
    domaine:        str = Field(..., min_length=2,
                                description="GENERAL|CCFM|MUTATION|RNAF|BGU|JUSTICE|HYPOTHEQUE|SUCCESSION|URBANISME")
    code:           str = Field(..., min_length=3,  max_length=50,
                                description="Code unique de la règle")
    niveau_blocage: str = Field(default="BLOQUANT",
                                description="BLOQUANT|AVERTISSEMENT|INFO")
    mode:           str = Field(default="COMPLET",
                                description="STATIQUE (C1+C2+C4) | COMPLET (C1+C2+C3+C4)")


class ValiderLotIn(BaseModel):
    """Valider un lot de règles SQL après contrôle sandbox."""
    regles: List[dict] = Field(...,
        description="Liste des règles à valider")
    mode:   str        = Field(default="STATIQUE",
        pattern="^(STATIQUE|DYNAMIQUE|HYBRIDE)$")

class RegleMetierIn(BaseModel):
    code:            str           = Field(..., min_length=3)
    nom:             str           = Field(..., min_length=3)
    domaine:         str
    condition_sql:   str           = Field(..., min_length=10)
    niveau_blocage:  str           = Field(default="BLOQUANT")
    description:     Optional[str] = None
    effectif_depuis: Optional[str] = None


# ──────────────────────────────────────────────────────────────────
# POST /admin/regles-metier/valider
#   Point d'entrée du pipeline 4 couches (diagramme officiel)
# ──────────────────────────────────────────────────────────────────

@router.post("/regles-metier/valider")
async def valider_regle_sql(
    payload: ValiderRegleIn,
    db:      AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """
    Valide une règle SQL en 4 couches successives.

    ```
    POST /admin/regles-metier/valider
           │
    C1 ── Validation statique     (Python pur, 0 dep)
           │ 40+ mots-clés interdits, commentaires, dollar-quoting,
           │ multi-instructions, UNION, INTO, profondeur max 6,
           │ WITH RECURSIVE, schémas système, fonctions dangereuses
           ▼ si OK
    C2 ── Validation sémantique   (logique métier foncier)
           │ Whitelist 30 tables, pg_shadow/pg_catalog bloqués,
           │ paramètres $1/$2, cohérence domaine, expression booléenne
           ▼ si OK
    C3 ── Sandbox PostgreSQL      (si mode=COMPLET)
           │ SAVEPOINT + EXPLAIN READ ONLY + timeout 2 s
           ▼ si OK
    C4 ── Signature SHA-256       (non-répudiation)
           └→ safe_sql + sha256_regle
    ```

    Retourne :
    - `status`        : VALIDE | INVALIDE | REFUSE
    - `layers`        : trace d'exécution couche par couche
    - `errors`        : erreurs bloquantes avec code et couche
    - `warnings`      : avertissements non-bloquants
    - `sha256_regle`  : empreinte SHA-256 (si VALIDE)
    - `duree_ms`      : durée totale de validation
    """
    from app.services.sql_sandbox import SQLRuleValidator

    use_db = db if payload.mode == "COMPLET" else None
    result = await SQLRuleValidator.valider(
        sql=payload.sql, domaine=payload.domaine,
        code=payload.code, niveau_blocage=payload.niveau_blocage,
        user_id=str(current_user["id"]),
        db=use_db, log_db=db,
    )

    if result.status.value == "REFUSE":
        raise HTTPException(
            status_code=400,
            detail={
                "code":    "SQL_REGLE_DANGEREUSE",
                "message": "La règle SQL a été rejetée par le système de sécurité (C1/C2/C3)",
                "errors":  [{"code": e.code, "message": e.message, "couche": e.couche}
                            for e in result.errors],
                "layers":  [{"couche": l.couche, "nom": l.nom, "statut": l.statut}
                            for l in result.layers],
            }
        )
    return result.as_dict()


# ──────────────────────────────────────────────────────────────────
# POST /admin/regles-metier  — Création avec validation obligatoire
# ──────────────────────────────────────────────────────────────────

@router.post("/regles-metier/valider-lot")
async def valider_lot(
    payload: ValiderLotIn,
    db:      AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Valide un lot de règles SQL (max 50) en une passe."""
    from app.services.sql_sandbox import SQLRuleValidator
    use_db = db if payload.mode == "COMPLET" else None
    result = await SQLRuleValidator.valider_lot(
        regles=payload.regles, db=use_db,
        log_db=db, user_id=str(current_user["id"]),
    )
    return result.as_dict()


# ──────────────────────────────────────────────────────────────────
# PATCH /admin/regles-metier/{id}/activer|desactiver
# ──────────────────────────────────────────────────────────────────

@router.patch("/regles-metier/{regle_id}/activer")
async def activer_regle(
    regle_id: str,
    db:       AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Active une règle validée (sandbox_valide=TRUE requis par le trigger)."""
    try:
        r = await db.execute(text("""
            SELECT id, code, sandbox_valide, actif
            FROM regle_metier WHERE id=:rid::uuid
        """), {"rid": regle_id})
        row = r.mappings().first()
        if not row:
            raise HTTPException(404, "Règle non trouvée")
        if not row["sandbox_valide"]:
            raise HTTPException(422, {
                "code": "SANDBOX_REQUIS",
                "message": f"La règle [{row['code']}] doit passer les 4 couches de validation "
                           "avant activation.",
                "correction": f"Appeler POST /v1/admin/regles-metier/valider "
                              f"avec code={row['code']}",
            })
        if row["actif"]:
            return {"id": regle_id, "code": row["code"], "statut": "DEJA_ACTIVE"}
        await db.execute(text(
            "UPDATE regle_metier SET actif=TRUE WHERE id=:rid::uuid"
        ), {"rid": regle_id})
        await db.commit()
        return {"id": regle_id, "code": row["code"], "statut": "ACTIVEE"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])


@router.patch("/regles-metier/{regle_id}/desactiver")
async def desactiver_regle(
    regle_id: str,
    motif: Optional[str] = Query(None),
    db:    AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Désactive une règle sans la supprimer (logique no-DELETE FONCIER+)."""
    try:
        r = await db.execute(text("""
            UPDATE regle_metier SET actif=FALSE WHERE id=:rid::uuid RETURNING code
        """), {"rid": regle_id})
        code = r.scalar()
        if not code:
            raise HTTPException(404, "Règle non trouvée")
        from app.services.sql_sandbox import ValidationCache
        ValidationCache.invalidate(code)
        await db.commit()
        return {"id": regle_id, "code": code, "statut": "DESACTIVEE", "motif": motif}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])


# ──────────────────────────────────────────────────────────────────
# GET  — Monitoring sandbox
# ──────────────────────────────────────────────────────────────────

@router.get("/sandbox/rapport", response_model=list)
async def rapport_sandbox(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER + ["AUDITEUR"])),
):
    """Rapport complet de santé du système de validation SQL."""
    rapport: dict = {}

    async def safe_query(label: str, sql: str) -> list:
        try:
            r = await db.execute(text(sql))
            return [dict(row) for row in r.mappings().all()]
        except Exception as e:
            import logging; logging.getLogger("admin").warning("%s: %s", label, e)
            return []

    sante_rows = await safe_query("sante", "SELECT * FROM v_sandbox_sante")
    rapport["sante"] = sante_rows[0] if sante_rows else {}
    rapport["regles_en_attente"]  = await safe_query("attente", "SELECT * FROM v_regles_attente LIMIT 50")
    rapport["bypass_alerts_7j"]   = await safe_query("bypass",  "SELECT * FROM v_bypass_alerts_7j")
    rapport["stats_30j"]          = await safe_query("stats",   "SELECT * FROM v_sandbox_stats_30j LIMIT 60")
    rapport["function_allowlist"] = await safe_query("allowlist",
        "SELECT nom_fonction, categorie, description FROM sql_function_allowlist WHERE actif=TRUE ORDER BY categorie, nom_fonction")
    rapport["generated_at"] = datetime.now(timezone.utc).isoformat()
    return rapport


@router.get("/sandbox/stats", response_model=list)
async def stats_sandbox(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Statistiques 30 jours du sandbox."""
    try:
        r = await db.execute(text("SELECT * FROM v_sandbox_stats_30j"))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("stats: %s", e)
        return []


@router.get("/sandbox/attente", response_model=list)
async def regles_attente(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Règles en attente de validation sandbox."""
    try:
        r = await db.execute(text("SELECT * FROM v_regles_attente"))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("attente: %s", e)
        return []


@router.get("/regles-metier/{regle_id}/historique-validation", response_model=dict)
async def historique_validation(
    regle_id: str,
    db:       AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Historique des tentatives de validation d'une règle."""
    try:
        r = await db.execute(text("""
            SELECT code_regle, statut, nb_erreurs, nb_warnings,
                   sha256_regle, duree_ms, created_at
            FROM sql_validation_log
            WHERE code_regle = (SELECT code FROM regle_metier WHERE id=:rid::uuid)
            ORDER BY created_at DESC LIMIT 30
        """), {"rid": regle_id})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("historique_validation: %s", e)
        return []


# ═══════════════════════════════════════════════════════════════════
# MOTEUR DE VALIDATION LOGIQUE (C5)
# POST /admin/regles-metier/valider-logique  — analyse d'une règle
# GET  /admin/logic/rapport                  — rapport santé moteur
# GET  /admin/logic/conflits                 — règles avec conflits
# GET  /admin/logic/heuristiques             — liste & config
# PATCH /admin/logic/heuristiques/{id}       — activer/désactiver
# GET  /admin/logic/taux-conflit             — taux par domaine
# ═══════════════════════════════════════════════════════════════════

class ValiderLogiqueIn(BaseModel):
    sql:            str = Field(..., min_length=3, max_length=2000)
    domaine:        str = Field(..., min_length=2)
    code:           str = Field(..., min_length=3, max_length=50)
    niveau_blocage: str = Field(default="BLOQUANT")
    nom:            str = Field(default="")


@router.post("/regles-metier/valider-logique")
async def valider_logique(
    payload: ValiderLogiqueIn,
    db:      AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """
    Valide une règle SQL via le moteur logique C5 uniquement
    (sans repasser par C1-C4).

    Analyse par rapport à toutes les règles actives du même domaine.

    Retourne un rapport détaillé :
    - recommandation : AUTORISER | CORRIGER | REFUSER
    - conflits       : liste détaillée par heuristique
    - regles_impactees : codes des règles en conflit
    - nb_critique / nb_majeur / nb_mineur
    """
    from app.services.logic_validator import LogicConflictEngine

    report = await LogicConflictEngine.valider(
        sql           = payload.sql,
        domaine       = payload.domaine,
        code          = payload.code,
        niveau_blocage= payload.niveau_blocage,
        nom           = payload.nom,
        db            = db,
        log_db        = db,
        user_id       = str(current_user["id"]),
    )

    if report.bloque:
        raise HTTPException(
            status_code=409,
            detail={
                "code":             "LOGIC_CONFLIT_CRITIQUE",
                "message":          "Conflit logique critique — activation bloquée",
                "recommandation":   report.recommandation.value,
                "nb_critique":      report.nb_critique,
                "regles_impactees": report.regles_impactees,
                "conflits": [
                    {
                        "heuristique": c.heuristique,
                        "niveau":      c.niveau.value,
                        "regle_cible": c.regle_cible,
                        "message":     c.message,
                        "suggestion":  c.suggestion,
                    }
                    for c in report.conflits
                    if c.niveau.value == "CRITIQUE"
                ],
            }
        )

    return report.as_dict()


@router.post("/regles-metier", status_code=201)
async def creer_regle_metier_v2(
    payload: "RegleMetierIn",
    db:      AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """
    Crée une règle métier après validation complète C1→C5.
    """
    from app.services.sql_sandbox import SQLRuleValidator, ValidationStatus

    result = await SQLRuleValidator.valider(
        sql=payload.condition_sql, domaine=payload.domaine,
        code=payload.code, niveau_blocage=payload.niveau_blocage,
        user_id=str(current_user["id"]),
        db=db, log_db=db,
    )

    if result.status == ValidationStatus.REFUSE:
        raise HTTPException(400, {
            "code":    "VALIDATION_REFUSEE",
            "message": "La règle a été rejetée (C1-C5)",
            "errors":  [{"code": e.code, "message": e.message, "couche": e.couche}
                        for e in result.errors],
            "logic_report": result.logic_report,
        })
    if not result.valide:
        raise HTTPException(422, {
            "code":     "VALIDATION_INVALIDE",
            "message":  "La règle contient des erreurs",
            "errors":   [{"code": e.code, "message": e.message} for e in result.errors],
            "warnings": result.warnings,
        })

    # Statut validation logique
    logic = result.logic_report or {}
    logic_valide        = logic.get("recommandation") != "REFUSER" if logic else None
    logic_recommandation= logic.get("recommandation") if logic else None
    logic_nb_conflits   = (logic.get("nb_critique",0) + logic.get("nb_majeur",0)) if logic else 0

    try:
        r = await db.execute(text("""
            INSERT INTO regle_metier (
                code, nom, domaine, condition_sql, sql_normalise,
                niveau_blocage, description, created_by,
                sandbox_valide, sandbox_valide_at, sandbox_valide_par, sha256_regle,
                logic_valide, logic_valide_at, logic_recommandation, logic_nb_conflits
            ) VALUES (
                :code, :nom, :dom, :sql, :sql_norm,
                :niveau, :desc, :uid::uuid,
                TRUE, NOW(), :uid::uuid, :sha256,
                :lv, CASE WHEN :lv IS NOT NULL THEN NOW() END,
                :lreco, :lnb
            ) RETURNING id, version
        """), {
            "code":    payload.code,  "nom":      payload.nom,
            "dom":     payload.domaine, "sql":    payload.condition_sql,
            "sql_norm": result.safe_sql or payload.condition_sql,
            "niveau":  payload.niveau_blocage, "desc": payload.description,
            "uid":     str(current_user["id"]),
            "sha256":  result.sha256_regle,
            "lv":      logic_valide,
            "lreco":   logic_recommandation,
            "lnb":     logic_nb_conflits,
        })
        row = r.first()
        rid, ver = row[0], row[1]
        await db.execute(text("""
            INSERT INTO regle_version(regle_id, version, condition_sql,
                niveau_blocage, motif_changement, modifie_par)
            VALUES (:rid, :ver, :sql, :niveau,
                    'Création — validée C1→C5', :uid::uuid)
        """), {
            "rid": rid, "ver": ver,
            "sql": result.safe_sql or payload.condition_sql,
            "niveau": payload.niveau_blocage, "uid": str(current_user["id"]),
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:300])

    return {
        "id":                str(rid),
        "code":              payload.code,
        "version":           ver,
        "sha256_regle":      result.sha256_regle,
        "sandbox":           "VALIDE_4_COUCHES",
        "logic_recommandation": logic_recommandation,
        "logic_nb_conflits": logic_nb_conflits,
        "warnings":          result.warnings,
    }


@router.get("/logic/rapport", response_model=list)
async def rapport_logique(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER + ["AUDITEUR"])),
):
    """Rapport de santé du moteur logique (24h + 30j)."""
    rapport: dict = {}
    import logging
    lg = logging.getLogger("admin")

    async def sq(label: str, sql_str: str) -> list:
        try:
            r = await db.execute(text(sql_str))
            return [dict(row) for row in r.mappings().all()]
        except Exception as e:
            lg.warning("%s: %s", label, e); return []

    sante = await sq("sante", "SELECT * FROM v_logic_sante")
    rapport["sante"]             = sante[0] if sante else {}
    rapport["conflits_recents"]  = await sq("recents",
        "SELECT * FROM v_logic_conflits_recents LIMIT 30")
    rapport["regles_conflits"]   = await sq("conflits",
        "SELECT * FROM v_regles_conflits_logiques LIMIT 20")
    rapport["taux_par_domaine"]  = await sq("taux",
        "SELECT * FROM logic_taux_conflit(30)")
    rapport["heuristiques"]      = await sq("heuristiques",
        "SELECT heuristic_id, domaine, actif, description FROM logic_heuristic_config ORDER BY heuristic_id")
    rapport["generated_at"]      = datetime.now(timezone.utc).isoformat()
    return rapport


@router.get("/logic/conflits", response_model=list)
async def regles_avec_conflits(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Liste des règles avec conflits logiques détectés."""
    try:
        r = await db.execute(text("SELECT * FROM v_regles_conflits_logiques"))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("logic_conflits: %s", e)
        return []


@router.get("/logic/heuristiques", response_model=list)
async def lister_heuristiques(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Liste et configuration des heuristiques du moteur logique."""
    from app.services.logic_validator import LogicConflictEngine
    engine_list = {h["id"]: h for h in LogicConflictEngine.list_heuristics()}
    try:
        r = await db.execute(text("""
            SELECT heuristic_id, domaine, actif, description, seuil_override
            FROM logic_heuristic_config ORDER BY heuristic_id
        """))
        db_rows = [dict(row) for row in r.mappings().all()]
        # Fusionner avec l'état Python-layer
        for row in db_rows:
            hid = row["heuristic_id"]
            row["actif_engine"] = engine_list.get(hid, {}).get("active", True)
        return db_rows
    except Exception as e:
        import logging; logging.getLogger("admin").warning("heuristiques: %s", e)
        return LogicConflictEngine.list_heuristics()


class HeuristiqueConfig(BaseModel):
    """Configuration d'une heuristique de détection."""
    heuristic_id:   str            = Field(..., min_length=2)
    actif:          bool           = Field(default=True)
    seuil_override: Optional[dict] = None
    jours:          int            = Field(default=30, ge=1, le=365)


async def configurer_heuristique(
    heuristic_id: str,
    payload:      HeuristiqueConfig,
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Active ou désactive une heuristique du moteur logique."""
    from app.services.logic_validator import LogicConflictEngine
    try:
        await db.execute(text("""
            UPDATE logic_heuristic_config
            SET actif=:actif, seuil_override=:seuil::jsonb,
                modifie_par=:uid::uuid, updated_at=NOW()
            WHERE heuristic_id=:hid AND domaine='ALL'
        """), {
            "actif":  payload.actif,
            "seuil":  json.dumps(payload.seuil_override) if payload.seuil_override else None,
            "uid":    str(current_user["id"]),
            "hid":    heuristic_id,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    # Synchroniser avec Python-layer
    if payload.actif:
        LogicConflictEngine.enable(heuristic_id)
    else:
        LogicConflictEngine.disable(heuristic_id)

    return {"heuristic_id": heuristic_id, "actif": payload.actif}


@router.get("/logic/taux-conflit", response_model=list)
async def taux_conflit_domaine(
    jours: int = 30,
    db:    AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Taux de conflit par domaine (défaut : 30 derniers jours)."""
    try:
        r = await db.execute(
            text("SELECT * FROM logic_taux_conflit(:j)"), {"j": jours})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("taux_conflit: %s", e)
        return []


# ═══════════════════════════════════════════════════════════════════
# MOTEUR DE SIMULATION DES OPÉRATIONS FONCIÈRES (035)
# POST /admin/simulation/lancer        ── simuler une opération
# POST /admin/simulation/batch         ── simuler plusieurs scénarios
# GET  /admin/simulation/{id}          ── récupérer un rapport
# GET  /admin/simulation/historique    ── historique des simulations
# GET  /admin/simulation/rapport       ── tableau de bord global
# GET  /admin/simulation/stats         ── statistiques par opération
# GET  /admin/simulation/operations    ── types d'opérations disponibles
# ═══════════════════════════════════════════════════════════════════

class SimulationIn(BaseModel):
    operation_type:       str  = Field(..., description="MUTATION|HYPOTHEQUE|FUSION|…")
    parcelle_ids:         List[str] = Field(..., min_items=1, max_items=10)
    region:               str  = Field(..., min_length=2, max_length=10)
    nouveau_titulaire_id: Optional[str]   = None
    montant_hypotheque:   Optional[float] = None
    parcelles_cibles:     Optional[List[str]] = None
    nb_lots:              Optional[int]   = None
    motif:                Optional[str]   = None
    contexte_extra:       Optional[dict]  = {}


class ScenarioBatch(BaseModel):
    nom:       str
    simulation: SimulationIn


class SimulationBatchIn(BaseModel):
    scenarios: List[ScenarioBatch] = Field(..., min_items=1, max_items=5)


@router.post("/simulation/lancer", status_code=200)
async def lancer_simulation(
    payload:     SimulationIn,
    db:          AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Simule complètement une opération foncière sans modifier les données réelles.

    Le moteur exécute 6 couches de vérification dans un SAVEPOINT PostgreSQL
    (ROLLBACK systématique — aucune donnée réelle n'est modifiée) :

    - **S1 Pré-conditions** : état des parcelles, gel, verrous, litiges
    - **S2 Règles métier**  : decision_engine sur les données simulées
    - **S3 Droits fonciers**: somme 100%, cohérence titulaires
    - **S4 Documents**      : CCFM gate, RNAF, actes notariés
    - **S5 Juridiction**    : compétence régionale, niveau d'autorité
    - **S6 Topologie**      : conflits géographiques, surface, contiguïté

    Retourne un rapport avec :
    - `statut`         : SUCCES | AVERTISSEMENT | ECHEC
    - `autorisee`      : true si l'opération peut être soumise
    - `impacts`        : liste détaillée des impacts par couche
    - `effets_projetes`: effets attendus si l'opération est exécutée
    - `recommandation` : texte de recommandation
    """
    from app.services.simulation_engine import SimulationEngine, SimulationContext

    ctx = SimulationContext(
        operation_type       = payload.operation_type,
        parcelle_ids         = payload.parcelle_ids,
        user_id              = str(current_user["id"]),
        region               = payload.region,
        nouveau_titulaire_id = payload.nouveau_titulaire_id,
        montant_hypotheque   = payload.montant_hypotheque,
        parcelles_cibles     = payload.parcelles_cibles,
        nb_lots              = payload.nb_lots,
        motif                = payload.motif,
        contexte_extra       = payload.contexte_extra or {},
    )

    result = await SimulationEngine.simuler(ctx, db=db, log_db=db)
    return result.as_dict()


@router.post("/simulation/batch", status_code=200)
async def simulation_batch(
    payload:     SimulationBatchIn,
    db:          AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Simule plusieurs scénarios alternatifs sur un même dossier (max 5).
    Utile pour comparer différentes options d'opération avant décision.

    Retourne la liste des rapports avec le nom du scénario.
    """
    from app.services.simulation_engine import SimulationEngine, SimulationContext

    rapports = []
    for sc in payload.scenarios:
        p = sc.simulation
        ctx = SimulationContext(
            operation_type       = p.operation_type,
            parcelle_ids         = p.parcelle_ids,
            user_id              = str(current_user["id"]),
            region               = p.region,
            nouveau_titulaire_id = p.nouveau_titulaire_id,
            montant_hypotheque   = p.montant_hypotheque,
            parcelles_cibles     = p.parcelles_cibles,
            nb_lots              = p.nb_lots,
            motif                = p.motif,
            contexte_extra       = p.contexte_extra or {},
        )
        result = await SimulationEngine.simuler(ctx, db=db, log_db=db)
        rapports.append({"scenario": sc.nom, **result.as_dict()})

    # Résumé comparatif
    meilleur = min(rapports, key=lambda r: (r["nb_bloquants"], r["nb_avertissements"]))
    return {
        "nb_scenarios":  len(rapports),
        "meilleur_scenario": meilleur["scenario"],
        "scenarios":     rapports,
    }


@router.get("/simulation/{simulation_id}", response_model=dict)
async def get_simulation(
    simulation_id: str,
    db:            AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Récupère le rapport complet d'une simulation par son ID."""
    try:
        r = await db.execute(text("""
            SELECT simulation_id, operation_type, statut,
                   nb_bloquants, nb_avertissements, nb_infos,
                   autorisee, rapport_json, sha256_rapport,
                   duree_ms, region, created_at
            FROM simulation_log
            WHERE simulation_id = :sid::uuid
        """), {"sid": simulation_id})
        row = r.mappings().first()
    except Exception as e:
        raise HTTPException(400, str(e)[:200])

    if not row:
        raise HTTPException(404, f"Simulation {simulation_id} introuvable")
    return dict(row)


@router.get("/simulation/historique", response_model=list)
async def historique_simulations(
    operation_type: Optional[str] = Query(None),
    statut:         Optional[str] = Query(None),
    limite:         int           = Query(default=20, ge=1, le=100),
    db:             AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Historique des simulations (filtrables par type et statut)."""
    where = "WHERE 1=1"
    params: dict = {"lim": limite}
    if operation_type:
        where += " AND operation_type=:op"; params["op"] = operation_type
    if statut:
        where += " AND statut=:stat"; params["stat"] = statut.upper()

    try:
        r = await db.execute(text(f"""
            SELECT simulation_id, operation_type, statut,
                   nb_bloquants, nb_avertissements, autorisee,
                   parcelle_ids, region, duree_ms, created_at
            FROM simulation_log
            {where}
            ORDER BY created_at DESC
            LIMIT :lim
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("simulation_historique: %s", e)
        return []


@router.get("/simulation/rapport", response_model=list)
async def rapport_simulation(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER + ["AUDITEUR"])),
):
    """Tableau de bord complet du moteur de simulation."""
    rapport: dict = {}
    import logging; lg = logging.getLogger("admin")

    async def sq(lbl: str, sql_q: str, p: dict = {}) -> list:
        try:
            r = await db.execute(text(sql_q), p)
            return [dict(row) for row in r.mappings().all()]
        except Exception as e:
            lg.warning("%s: %s", lbl, e); return []

    sante = await sq("sante", "SELECT * FROM v_simulation_sante")
    rapport["sante"]          = sante[0] if sante else {}
    rapport["stats_30j"]      = await sq("stats",     "SELECT * FROM v_simulation_stats_30j")
    rapport["bloquees_7j"]    = await sq("bloquees",  "SELECT * FROM v_simulations_bloquees LIMIT 20")
    rapport["taux_succes"]    = await sq("taux",      "SELECT * FROM simulation_taux_succes(30)")
    rapport["config"]         = await sq("config",    "SELECT cle, valeur, description FROM simulation_config ORDER BY cle")
    rapport["generated_at"]   = datetime.now(timezone.utc).isoformat()
    return rapport


@router.get("/simulation/stats", response_model=list)
async def stats_simulation(
    jours: int = Query(default=30, ge=1, le=365),
    db:    AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Statistiques de simulation par type d'opération."""
    try:
        r = await db.execute(
            text("SELECT * FROM simulation_taux_succes(:j)"), {"j": jours})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging; logging.getLogger("admin").warning("simulation_stats: %s", e)
        return []


@router.get("/simulation/operations", response_model=list)
async def lister_operations(current_user=Depends(get_current_user)):
    """Liste des types d'opérations foncières disponibles pour simulation."""
    from app.services.simulation_engine import (
        OPERATION_TYPES, DOCS_REQUIS, NIVEAUX_VALIDATION
    )
    return [
        {
            "code":          op,
            "libelle":       lib,
            "docs_requis":   DOCS_REQUIS.get(op, []),
            "niveau_validation": NIVEAUX_VALIDATION.get(op, 1),
        }
        for op, lib in OPERATION_TYPES.items()
    ]


class SimulationConfigUpdate(BaseModel):
    valeur: str = Field(..., min_length=1)


@router.patch("/simulation/config/{cle}")
async def update_simulation_config(
    cle:     str,
    payload: SimulationConfigUpdate,
    db:      AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Met à jour un paramètre de configuration du moteur de simulation."""
    try:
        await db.execute(text("""
            UPDATE simulation_config
            SET valeur=:val, modifie_par=:uid::uuid, updated_at=NOW()
            WHERE cle=:cle
        """), {"val": payload.valeur, "uid": str(current_user["id"]), "cle": cle})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"cle": cle, "valeur": payload.valeur}
