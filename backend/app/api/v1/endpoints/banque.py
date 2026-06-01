"""
FONCIER+ — Module Banque COMPLET
Module indépendant — garanties foncières et hypothèques.

Tables couvertes :
  banque_agreee (020)        — registre BCEAO (11 banques seedées)
  hypotheques (legacy 001)   — hypothèques de base
  mortgage_registry (005)    — registre enrichi (RNAF, CCFM, mainlevée)
  hypotheque_dossier (008)   — dossiers WF8 complets
  autorisation_bancaire (011)— autorisations bancaires exceptionnelles
  levee_hypotheque (011)     — mainlevées officielles tracées

Workflow porté :
  WF8 — Hypothèques et garanties bancaires (5 étapes)

Triggers protecteurs :
  tg_wl7_gate_hypotheque      — bloque si hypothèque active, litige,
                                 ou absence de CCFM valide
  tg_verifier_banque_agreee   — bloque si banque non agréée BCEAO
  tg_hyp_autorisation_requise — opération bloquée si hypothèque active
                                 sans autorisation bancaire expresse
"""
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (require_role, get_current_user,
    calculate_sha256, ROLES_BANQUES, ROLES_READ_ALL)
from app.models.droits_fonciers import MortgageRegistry, StatutHypotheque
from app.models.banque import (
    HypothequeDossier, HypothequeLegacy, AutorisationBancaire, LeveeHypotheque
)
from app.services.workflow_orchestrator import TransactionGate

router = APIRouter(prefix="/banque", tags=["Banque — Hypothèques"])

ROLES_BANQUE = ROLES_BANQUES
ROLES_DIR    = ROLES_BANQUES
ROLES_READ   = ROLES_READ_ALL



# ══════════════════════════════════════════════════════════════════
# SCHÉMAS
# ══════════════════════════════════════════════════════════════════

class HypothequeIn(BaseModel):
    parcelle_id: str
    banque_id: str = Field(...,
        description="UUID banque_agreee — vérifié par tg_verifier_banque_agreee")
    rnp_parcelle_id: str
    rnaf_id: str
    debiteur_id: str = Field(...,
        description="UUID right_holders du débiteur")
    montant_fcfa: float = Field(..., gt=0)
    duree_mois: int = Field(..., ge=1, le=360)
    taux_interet: Optional[float] = Field(None, ge=0, le=100)
    reference_contrat: str
    ccfm_validation_id: Optional[str] = None


class AutorisationBancaireIn(BaseModel):
    hypotheque_dossier_id: str
    type_operation: str = Field(...,
        description="mutation|division|fusion|lotissement")
    conditions: Optional[str] = None
    montant_garanti_fcfa: Optional[float] = Field(None, gt=0,
        description='Montant de la garantie en FCFA — doit être > 0')
    valide_jusqu_au: Optional[datetime] = None


class MainleveeIn(BaseModel):
    motif: str = Field(...,
        description="remboursement|accord_amiable|decision_judiciaire|expiration")
    acte_mainlevee_ref: str = Field(..., min_length=3)
    notaire_id: Optional[str] = None
    montant_solde: Optional[float] = None


_sha = calculate_sha256



# ══════════════════════════════════════════════════════════════════
# TABLEAU DE BORD BANQUE
# ══════════════════════════════════════════════════════════════════

@router.get("/tableau-de-bord", response_model=list)
async def tableau_de_bord_banque(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_BANQUE + ["AUDITEUR"])),
):
    """Vue consolidée du module Banque."""
    stats: dict = {}
    queries = {
        "hypotheques_actives":
            "SELECT COUNT(*) FROM mortgage_registry WHERE statut='active'",
        "dossiers_en_cours":
            "SELECT COUNT(*) FROM hypotheque_dossier "
            "WHERE statut NOT IN ('levee','contentieux')",
        "mainlevees_ce_mois":
            "SELECT COUNT(*) FROM levee_hypotheque "
            "WHERE date_levee >= date_trunc('month',NOW())",
        "autorisations_actives":
            "SELECT COUNT(*) FROM autorisation_bancaire WHERE statut='accordee'",
        "banques_agreees":
            "SELECT COUNT(*) FROM banque_agreee WHERE statut='actif'",
        "encours_total_fcfa":
            "SELECT COALESCE(SUM(montant),0) FROM mortgage_registry "
            "WHERE statut='active'",
    }
    for key, sql in queries.items():
        try:
            r = await db.execute(text(sql))
            v = r.scalar()
            stats[key] = float(v) if v is not None else 0
        except Exception:
            stats[key] = 0

    # Dossiers récents de la banque connectée
    dossiers_recents = []
    try:
        r = await db.execute(text("""
            SELECT hd.id, hd.montant_fcfa, hd.statut, hd.created_at,
                   p.nicad
            FROM hypotheque_dossier hd
            LEFT JOIN parcelles p ON p.id = hd.parcelle_id
            ORDER BY hd.created_at DESC LIMIT 5
        """))
        dossiers_recents = [dict(row) for row in r.mappings().all()]
    except Exception:
        pass

    return {
        "acteur": current_user.email,
        "role": current_user.role,
        "module": "Banque",
        "stats": stats,
        "dossiers_recents": dossiers_recents,
        "horodatage": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════
# REGISTRE BANQUE_AGREEE (BCEAO)
# ══════════════════════════════════════════════════════════════════

@router.get("/registre-bceao", response_model=list)
async def lister_banques_agreees(
    statut: str = Query(default="actif",
        description="actif|suspendu|retrait|fusion"),
    type_etablissement: Optional[str] = Query(None,
        description="banque_universelle|banque_affaires|microfinance|caisse_epargne"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ + ["NOTAIRE","DIRECTEUR_URBANISME"])),
):
    """Registre BCEAO des banques agréées au Niger.
    Source d'autorité pour le trigger tg_verifier_banque_agreee.
    """
    where = "WHERE statut=:s"
    params: dict = {"s": statut}
    if type_etablissement:
        where += " AND type_etablissement=:t"; params["t"] = type_etablissement
    try:
        r = await db.execute(text(
            f"SELECT id, numero_agrement_bceao, raison_sociale, sigle, "
            f"type_etablissement, siege_social, date_agrement, statut, contact_direction "
            f"FROM banque_agreee {where} ORDER BY raison_sociale"
        ), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.get("/registre-bceao/{banque_id}", response_model=dict)
async def get_banque_agreee(
    banque_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Détail d'une banque agréée avec son encours hypothécaire."""
    try:
        r = await db.execute(text(
            "SELECT * FROM banque_agreee WHERE id=:id"
        ), {"id": banque_id})
        row = r.mappings().first()
        if not row:
            raise HTTPException(404, "Banque introuvable")
        result = dict(row)
        # Encours
        r_enc = await db.execute(text("""
            SELECT COUNT(*) nb, COALESCE(SUM(montant),0) encours
            FROM mortgage_registry
            WHERE banque_id=:bid AND statut='active'
        """), {"bid": banque_id})
        enc = r_enc.mappings().first()
        result["encours"] = dict(enc) if enc else {}
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


# ══════════════════════════════════════════════════════════════════
# VÉRIFICATION ÉLIGIBILITÉ
# ══════════════════════════════════════════════════════════════════

@router.get("/verif-parcelle/{parcelle_id}", response_model=dict)
async def verifier_eligibilite_bancaire(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_BANQUE)),
):
    """Vérifie si une parcelle est éligible à une hypothèque.

    Contrôles effectués :
    1. Gate transactionnelle (gel, blocks, litiges, CCFM)
    2. Hypothèques actives existantes
    3. Autorisations bancaires actives (si opération malgré hypothèque)
    """
    eligible, raison, details = await TransactionGate.check(db, parcelle_id)

    # Hypothèques actives
    try:
        r = await db.execute(text("""
            SELECT COUNT(*) nb,
                   COALESCE(SUM(mr.montant),0) encours_fcfa
            FROM mortgage_registry mr
            WHERE mr.parcelle_id=:pid AND mr.statut='active'
        """), {"pid": parcelle_id})
        hyp = r.mappings().first()
        details["hypotheques_actives"] = hyp["nb"] if hyp else 0
        details["encours_fcfa"] = float(hyp["encours_fcfa"]) if hyp else 0
    except Exception:
        details["hypotheques_actives"] = 0

    # Autorisations bancaires actives
    try:
        r = await db.execute(text("""
            SELECT COUNT(*) FROM autorisation_bancaire ab
            JOIN hypotheque_dossier hd ON hd.id = ab.hypotheque_dossier_id
            WHERE hd.parcelle_id=:pid AND ab.statut='accordee'
              AND (ab.valide_jusqu_au IS NULL OR ab.valide_jusqu_au > NOW())
        """), {"pid": parcelle_id})
        details["autorisations_bancaires"] = r.scalar() or 0
    except Exception:
        details["autorisations_bancaires"] = 0

    return {
        "parcelle_id": parcelle_id,
        "eligible": eligible,
        "raison": raison,
        "details": details,
    }


# ══════════════════════════════════════════════════════════════════
# WF8 — DOSSIER HYPOTHÈQUE (pipeline complet)
# ══════════════════════════════════════════════════════════════════

@router.post("/dossiers-hypotheque", status_code=201)
async def ouvrir_dossier_hypotheque(
    payload: HypothequeIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_BANQUE)),
):
    """Ouvre un dossier d'hypothèque (étape 1 WF8).

    Trigger tg_wl7_gate_hypotheque bloque automatiquement si :
    - Hypothèque déjà active sur la parcelle
    - Litige en cours
    - Pas de CCFM valide
    Trigger tg_verifier_banque_agreee bloque si banque non BCEAO.
    """
    # Vérifier banque agréée avant insertion
    try:
        r = await db.execute(text(
            "SELECT statut FROM banque_agreee WHERE id=:bid"
        ), {"bid": payload.banque_id})
        banque = r.mappings().first()
        if not banque:
            raise HTTPException(404, "Banque introuvable dans le registre BCEAO")
        if banque["statut"] != "actif":
            raise HTTPException(404,
                f"Banque au statut '{banque['statut']}' — agrément BCEAO requis")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])

    dos_id = uuid.uuid4()
    sha = _sha(str(dos_id), payload.parcelle_id, payload.banque_id,
               str(payload.montant_fcfa))
    try:
        dossier = HypothequeDossier(
            id=dos_id,
            parcelle_id=uuid.UUID(payload.parcelle_id),
            rnp_parcelle_id=uuid.UUID(payload.rnp_parcelle_id),
            debiteur_id=uuid.UUID(payload.debiteur_id),
            banque_id=uuid.UUID(payload.banque_id),
            montant_fcfa=payload.montant_fcfa,
            taux_interet=payload.taux_interet,
            duree_mois=payload.duree_mois,
            ccfm_validation_id=payload.ccfm_validation_id,
            statut="initie",
            sha256_dossier=sha,
            cree_par=current_user.id,
        )
        db.add(dossier)
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "HYPOTHÈQUE BLOQUÉE" in str(e):
            raise HTTPException(400,
                f"Dossier refusé (tg_wl7) : {str(e)[:400]}")
        raise HTTPException(400, str(e)[:200])

    return {
        "id": str(dos_id),
        "statut": "initie",
        "sha256_dossier": sha,
        "montant_fcfa": payload.montant_fcfa,
        "message": "Dossier ouvert — étape suivante : CCFM gate puis inscription",
    }


@router.get("/dossiers-hypotheque", response_model=list)
async def lister_dossiers(
    statut: Optional[str] = Query(None),
    parcelle_id: Optional[str] = Query(None),
    banque_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Liste les dossiers d'hypothèque avec filtres."""
    where = "WHERE 1=1"
    params: dict = {"l": limit, "o": (page-1)*limit}
    if statut: where += " AND statut=:s"; params["s"] = statut
    if parcelle_id: where += " AND parcelle_id=:pid"; params["pid"] = parcelle_id
    if banque_id: where += " AND banque_id=:bid"; params["bid"] = banque_id
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
                    where += " AND d.banque_id = :_scope_entite"
                    params["_scope_entite"] = _scope.entite_id
        except Exception:
            pass
        r = await db.execute(text(
            f"SELECT * FROM hypotheque_dossier {where} "
            "ORDER BY created_at DESC LIMIT :l OFFSET :o"), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


@router.get("/dossiers-hypotheque/{dos_id}", response_model=dict)
async def get_dossier_hypotheque(
    dos_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Détail d'un dossier avec son mortgage_registry et ses autorisations."""
    try:
        r = await db.execute(text("""
            SELECT hd.*,
                   mr.id AS mortgage_registry_id,
                   mr.statut AS statut_mortgage,
                   mr.sha256_contrat
            FROM hypotheque_dossier hd
            LEFT JOIN mortgage_registry mr ON mr.id = hd.mortgage_registry_id
            WHERE hd.id = :id
        """), {"id": dos_id})
        row = r.mappings().first()
        if not row:
            raise HTTPException(404, "Dossier introuvable")
        result = dict(row)

        # Autorisations associées
        r_aut = await db.execute(text("""
            SELECT type_operation, statut, conditions,
                   valide_jusqu_au, montant_garanti_fcfa
            FROM autorisation_bancaire
            WHERE hypotheque_dossier_id = :id
            ORDER BY created_at DESC
        """), {"id": dos_id})
        result["autorisations"] = [dict(a) for a in r_aut.mappings().all()]

        # Mainlevée si existante
        r_lev = await db.execute(text("""
            SELECT motif, acte_mainlevee_ref, date_levee, montant_solde
            FROM levee_hypotheque WHERE hypotheque_dossier_id = :id
        """), {"id": dos_id})
        lev = r_lev.mappings().first()
        result["mainlevee"] = dict(lev) if lev else None

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.patch("/dossiers-hypotheque/{dos_id}/statut")
async def avancer_dossier(
    dos_id: str,
    nouveau_statut: str = Query(...,
        description="ccfm_gate|evaluation|signature|enregistrement|levee|contentieux"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","BANQ_DIRECTEUR","CHEF_CCFM","NOTAIRE","ARCHIVISTE_ANNF", "BANQ_AGENT"]
    )),
):
    """Avance le statut d'un dossier d'hypothèque (WF8)."""
    STATUTS = {"ccfm_gate","evaluation","signature",
               "enregistrement","levee","contentieux"}
    if nouveau_statut not in STATUTS:
        raise HTTPException(422, f"Statut invalide : {STATUTS}")
    try:
        await db.execute(text("""
            UPDATE hypotheque_dossier
            SET statut=:s, updated_at=NOW() WHERE id=:id
        """), {"s": nouveau_statut, "id": dos_id})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"id": dos_id, "statut": nouveau_statut}


# ══════════════════════════════════════════════════════════════════
# INSCRIPTION HYPOTHÈQUE (mortgage_registry)
# ══════════════════════════════════════════════════════════════════

@router.post("/hypotheques", status_code=201)
async def inscrire_hypotheque(
    payload: HypothequeIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_DIR)),
):
    """WF8 étape 4 — Inscrit officiellement l'hypothèque.

    Pipeline :
    1. Gate transactionnelle
    2. Inscription dans hypotheques (legacy)
    3. Enregistrement dans mortgage_registry (trigger tg_verifier_banque_agreee)
    4. Génère numéro HYP-YYYY-NNNNNNN
    """
    # Gate
    eligible, raison, _ = await TransactionGate.check(db, payload.parcelle_id)
    if not eligible:
        raise HTTPException(400, f"Gate refusée : {raison}")

    # Récupérer right_holder bancaire
    r_holder = await db.execute(text(
        "SELECT id FROM right_holders WHERE user_id=:uid LIMIT 1"
    ), {"uid": str(current_user.id)})
    holder_id = r_holder.scalar()
    if not holder_id:
        raise HTTPException(400, "Aucun right_holder lié à cet utilisateur")

    sha = _sha(payload.parcelle_id, payload.banque_id,
               str(payload.montant_fcfa), str(datetime.now(timezone.utc)))
    hypo_id = uuid.uuid4()

    try:
        # Table legacy hypotheques via ORM
        legacy_hypo = HypothequeLegacy(
            id=hypo_id,
            parcelle_id=uuid.UUID(payload.parcelle_id),
            montant=payload.montant_fcfa,
            statut="active"
        )
        db.add(legacy_hypo)

        # mortgage_registry — trigger tg_verifier_banque_agreee valide banque_id
        mortgage = MortgageRegistry(
            id=uuid.uuid4(),
            hypotheque_id=hypo_id,
            rnp_parcelle_id=uuid.UUID(payload.rnp_parcelle_id),
            parcelle_id=uuid.UUID(payload.parcelle_id),
            banque_holder_id=holder_id,
            banque_id=uuid.UUID(payload.banque_id),
            montant=payload.montant_fcfa,
            devise="XOF",
            date_debut=datetime.now(timezone.utc),
            statut=StatutHypotheque.ACTIVE,
            reference_contrat=payload.reference_contrat,
            rnaf_id=uuid.UUID(payload.rnaf_id),
            ccfm_validation_id=payload.ccfm_validation_id,
            sha256_contrat=sha,
        )
        db.add(mortgage)
        await db.flush()

        # Numéro officiel
        r_num = await db.execute(text(
            "SELECT generer_numero_officiel('HYP','seq_hypotheque')"
        ))
        num_hyp = r_num.scalar()
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "HYPOTHÈQUE REFUSÉE" in str(e) or "banque" in str(e).lower():
            raise HTTPException(400,
                f"Inscription refusée (tg_verifier_banque_agreee) : {str(e)[:400]}")
        raise HTTPException(400, str(e)[:200])

    return {
        "id": str(mortgage.id),
        "numero_hypotheque": num_hyp,
        "montant_fcfa": payload.montant_fcfa,
        "devise": "XOF",
        "sha256_contrat": sha,
        "statut": "active",
    }


@router.get("/hypotheques/parcelle/{parcelle_id}", response_model=dict)
async def lister_hypotheques_parcelle(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Toutes les hypothèques d'une parcelle (actives et levées)."""
    try:
        r = await db.execute(
            select(MortgageRegistry)
            .where(MortgageRegistry.parcelle_id == parcelle_id)
            .order_by(MortgageRegistry.date_debut.desc())
        )
        return [{
            "id": str(m.id),
            "montant": float(m.montant), "devise": m.devise,
            "statut": m.statut,
            "date_debut": m.date_debut.isoformat(),
            "date_fin": m.date_fin.isoformat() if m.date_fin else None,
            "reference_contrat": m.reference_contrat,
            "levee_at": m.levee_at.isoformat() if m.levee_at else None,
            "sha256_contrat": m.sha256_contrat,
        } for m in r.scalars().all()]

    except Exception as e:
        import logging as _l
        _l.getLogger('banque').warning('lister_hypotheques_parcelle: %s', e)
        raise HTTPException(status_code=500,
            detail=str(e)[:200])

@router.get("/hypotheques", response_model=list)
async def lister_toutes_hypotheques(
    statut: Optional[str] = Query(default="active"),
    banque_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Liste toutes les hypothèques avec filtres."""
    where = "WHERE statut=:s"
    params: dict = {"s": statut, "l": limit, "o": (page-1)*limit}
    if banque_id:
        where += " AND banque_id=:bid"; params["bid"] = banque_id
    try:

        # Filtre acteur — ScopeFilter
        try:
            from app.core.scope_filter import ScopeFilter as _SF
            _scope = _SF.from_user(current_user)
            if not _scope.peut_voir_national:
                if _scope.region:
                    where += " AND h.region = :_scope_region"
                    params["_scope_region"] = _scope.region
                elif _scope.entite_id:
                    where += " AND h.banque_id = :_scope_entite"
                    params["_scope_entite"] = _scope.entite_id
        except Exception:
            pass
        r = await db.execute(text(
            f"SELECT mr.*, p.nicad FROM mortgage_registry mr "
            f"LEFT JOIN parcelles p ON p.id = mr.parcelle_id "
            f"{where} ORDER BY mr.date_debut DESC "
            f"LIMIT :l OFFSET :o"), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


# ══════════════════════════════════════════════════════════════════
# AUTORISATIONS BANCAIRES
# ══════════════════════════════════════════════════════════════════

@router.post("/autorisations", status_code=201)
async def emettre_autorisation_bancaire(
    payload: AutorisationBancaireIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_DIR)),
):
    """Émet une autorisation bancaire pour une opération sur parcelle hypothéquée.

    Permet à un notaire d'effectuer une mutation/division malgré
    l'hypothèque active (tg_hyp_autorisation_requise).
    """
    TYPES = {"mutation","division","fusion","lotissement"}
    if payload.type_operation not in TYPES:
        raise HTTPException(422, f"type_operation : {TYPES}")

    sha = _sha(payload.hypotheque_dossier_id, payload.type_operation,
               str(payload.montant_garanti_fcfa or 0))

    # Récupérer le right_holder bancaire
    r_h = await db.execute(text(
        "SELECT id FROM right_holders WHERE user_id=:uid LIMIT 1"
    ), {"uid": str(current_user.id)})
    holder_id = r_h.scalar()
    if not holder_id:
        raise HTTPException(404, "Right_holder bancaire introuvable")

    try:
        aut = AutorisationBancaire(
            id=uuid.uuid4(),
            hypotheque_dossier_id=uuid.UUID(payload.hypotheque_dossier_id),
            banque_holder_id=holder_id,
            type_operation=payload.type_operation,
            statut="accordee",
            conditions=payload.conditions,
            montant_garanti_fcfa=payload.montant_garanti_fcfa,
            valide_jusqu_au=payload.valide_jusqu_au,
            sha256_autorisation=sha,
            accordee_par=current_user.id,
        )
        db.add(aut)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    return {
        "hypotheque_dossier_id": payload.hypotheque_dossier_id,
        "type_operation": payload.type_operation,
        "statut": "accordee",
        "sha256": sha,
        "message": "Autorisation émise — tg_hyp_autorisation_requise levé pour cette opération",
    }


@router.patch("/autorisations/{aut_id}/refuser")
async def refuser_autorisation(
    aut_id: str,
    motif: str = Query(..., min_length=5),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_DIR)),
):
    """Refuse ou révoque une autorisation bancaire."""
    try:
        await db.execute(text(
            "UPDATE autorisation_bancaire SET statut='refusee' WHERE id=:id"
        ), {"id": aut_id})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"id": aut_id, "statut": "refusee"}


# ══════════════════════════════════════════════════════════════════
# MAINLEVÉE OFFICIELLE (levee_hypotheque)
# ══════════════════════════════════════════════════════════════════

@router.post("/hypotheques/{mortgage_id}/mainlevee")
async def prononcer_mainlevee(
    mortgage_id: str,
    payload: MainleveeIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_DIR + ["NOTAIRE","JUGE_FONCIER"])),
):
    """Prononce la mainlevée officielle d'une hypothèque.

    Crée un enregistrement dans levee_hypotheque (SHA-256 tracé),
    met mortgage_registry à LEVEE et libère la parcelle.
    """
    MOTIFS = {"remboursement","accord_amiable","decision_judiciaire","expiration"}
    if payload.motif not in MOTIFS:
        raise HTTPException(422, f"motif : {MOTIFS}")

    r = await db.execute(
        select(MortgageRegistry).where(MortgageRegistry.id == uuid.UUID(mortgage_id))
    )
    mortgage = r.scalar_one_or_none()
    if not mortgage:
        raise HTTPException(404, "Hypothèque introuvable")
    if mortgage.statut != StatutHypotheque.ACTIVE:
        raise HTTPException(404,
            f"Statut '{mortgage.statut}' — mainlevée impossible "
            "(seule une hypothèque ACTIVE peut être levée)")

    sha = _sha(mortgage_id, payload.motif, payload.acte_mainlevee_ref)
    now = datetime.now(timezone.utc)

    # Récupérer l'ID du dossier d'hypothèque
    r_dos = await db.execute(
        select(HypothequeDossier.id).where(HypothequeDossier.mortgage_registry_id == uuid.UUID(mortgage_id))
    )
    dossier_id = r_dos.scalar()

    try:
        # Enregistrement officiel dans levee_hypotheque via ORM
        levee = LeveeHypotheque(
            id=uuid.uuid4(),
            hypotheque_dossier_id=dossier_id,
            hypotheque_id=mortgage.hypotheque_id,
            mortgage_registry_id=mortgage.id,
            motif=payload.motif,
            montant_solde=payload.montant_solde,
            acte_mainlevee_ref=payload.acte_mainlevee_ref,
            notaire_id=uuid.UUID(payload.notaire_id) if payload.notaire_id else None,
            sha256_mainlevee=sha,
            date_levee=now,
            enregistre_par=current_user.id
        )
        db.add(levee)

        # Mettre à jour mortgage_registry
        mortgage.statut    = StatutHypotheque.LEVEE
        mortgage.levee_at  = now
        mortgage.levee_par = current_user.id
        mortgage.motif_levee = payload.motif
        mortgage.acte_mainlevee = payload.acte_mainlevee_ref

        # Mettre à jour hypotheques legacy via ORM
        r_legacy = await db.execute(
            select(HypothequeLegacy).where(HypothequeLegacy.id == mortgage.hypotheque_id)
        )
        legacy_hypo = r_legacy.scalar_one_or_none()
        if legacy_hypo:
            legacy_hypo.statut = "levee"

        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    return {
        "id": mortgage_id,
        "statut": "levee",
        "motif": payload.motif,
        "sha256_mainlevee": sha,
        "levee_at": now.isoformat(),
        "message": "Mainlevée enregistrée — parcelle libérée de toute hypothèque",
    }


@router.get("/mainlevees", response_model=list)
async def lister_mainlevees(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Historique des mainlevées enregistrées."""
    try:

        # Filtre acteur — ScopeFilter
        try:
            from app.core.scope_filter import ScopeFilter as _SF
            _scope = _SF.from_user(current_user)
            if not _scope.peut_voir_national:
                if _scope.region:
                    where += " AND m.region = :_scope_region"
                    params["_scope_region"] = _scope.region
                elif _scope.entite_id:
                    where += " AND m.banque_id = :_scope_entite"
                    params["_scope_entite"] = _scope.entite_id
        except Exception:
            pass
        r = await db.execute(text("""
            SELECT lh.id, lh.motif, lh.acte_mainlevee_ref,
                   lh.date_levee, lh.sha256_mainlevee,
                   lh.montant_solde, p.nicad
            FROM levee_hypotheque lh
            JOIN mortgage_registry mr ON mr.id = lh.mortgage_registry_id
            LEFT JOIN parcelles p ON p.id = mr.parcelle_id
            ORDER BY lh.date_levee DESC
            LIMIT :l OFFSET :o
        """), {"l": limit, "o": (page-1)*limit})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


# ══════════════════════════════════════════════════════════════════
# STATISTIQUES
# ══════════════════════════════════════════════════════════════════

@router.get("/stats", response_model=list)
async def stats_module_banque(
    annee: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Statistiques du module Banque."""
    where_yr = ""
    params: dict = {}
    if annee:
        where_yr = "AND EXTRACT(YEAR FROM date_debut)=:yr"
        params["yr"] = annee

    stats: dict = {}

    # Encours par banque
    try:
        r = await db.execute(text(f"""
            SELECT ba.raison_sociale, ba.sigle,
                   COUNT(mr.id) nb_hyp,
                   COALESCE(SUM(mr.montant),0) encours_fcfa
            FROM banque_agreee ba
            LEFT JOIN mortgage_registry mr ON mr.banque_id=ba.id
                AND mr.statut='active' {where_yr}
            GROUP BY ba.id, ba.raison_sociale, ba.sigle
            ORDER BY encours_fcfa DESC
        """), params)
        stats["encours_par_banque"] = [dict(row) for row in r.mappings().all()]
    except Exception:
        stats["encours_par_banque"] = []

    # Dossiers par statut
    try:
        r = await db.execute(text("""
            SELECT statut, COUNT(*) nb
            FROM hypotheque_dossier GROUP BY statut ORDER BY nb DESC
        """))
        stats["dossiers_par_statut"] = [dict(row) for row in r.mappings().all()]
    except Exception:
        stats["dossiers_par_statut"] = []

    # Mainlevées par motif
    try:
        r = await db.execute(text("""
            SELECT motif, COUNT(*) nb
            FROM levee_hypotheque GROUP BY motif ORDER BY nb DESC
        """))
        stats["mainlevees_par_motif"] = [dict(row) for row in r.mappings().all()]
    except Exception:
        stats["mainlevees_par_motif"] = []

    return {"annee_filtre": annee, "stats": stats}


# ══════════════════════════════════════════════════════════════════
# SOUS-MODULE DAR — BANQUE
# Direction des Archives — liaison automatique vers ANNF
# ══════════════════════════════════════════════════════════════════

@router.get("/dar/archives", response_model=list)
async def lister_archives_dar_banque(
    type_document: Optional[str] = Query(None,
        description="acte_hypotheque|mainlevee|autorisation_bancaire"),
    scelle: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'BANQ_DIRECTEUR', 'BANQ_AGENT', 'AUDITEUR'] + [
        "ARCHIVISTE_ANNF","RESPONSABLE_ANNF","AUDITEUR"
    ])),
):
    """Archives du module Banque (sous-module DAR).
    Retourne les archives liées à ce module depuis annf_archive_links.
    Filtrables par type de document et statut de scellement.
    """
    where = "WHERE entite_table LIKE :mod"
    params: dict = {
        "mod": f"%banque%",
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
async def archiver_vers_annf_banque(
    type_document: str = Query(...,
        description="acte_hypotheque|mainlevee|autorisation_bancaire"),
    entite_id: str = Query(..., description="UUID de l'entité à archiver"),
    entite_table: str = Query(..., description="Nom de la table source"),
    snapshot: Optional[dict] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'BANQ_DIRECTEUR', 'ARCHIVISTE_ANNF'])),
):
    """Archive un document du module Banque vers l'ANNF national.
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
        "module_source": "banque",
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
        "module": "banque",
        "sous_module": "DAR",
        "type_archive": type_document,
        "scelle": False,
        "message": "Archivé vers ANNF — scellement via POST /annf/{ida}/sceller",
    }


@router.get("/dar/integrite", response_model=list)
async def verifier_integrite_dar_banque(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'BANQ_DIRECTEUR', 'BANQ_AGENT', 'AUDITEUR'] + ["AUDITEUR"])),
):
    """Vérifie l'intégrité des archives DAR du module Banque.
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
        """), {"mod": f"%banque%"})
        row = r.mappings().first()
        return dict(row) if row else {"nb_scelles":0,"nb_en_attente":0,"nb_total":0}
    except Exception:
        return {"nb_scelles":0,"nb_en_attente":0,"nb_total":0}


# ══════════════════════════════════════════════════════════════════
# VÉRIFICATION CCFM OBLIGATOIRE — Module BANQUE
# Conforme à l'obligation réglementaire DNCD :
# tout acte foncier exige un certificat CCFM signé sur la parcelle.
# ══════════════════════════════════════════════════════════════════

@router.get("/verifier-ccfm/{parcelle_id}", response_model=dict)
async def verifier_ccfm_banque(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Vérifie le certificat CCFM d'une parcelle depuis le module BANQUE.
    Obligatoire avant toute action transactionnelle sur la parcelle.

    Retourne : valide, nus, statut, sha256_verif, qr_code_url,
               demandeur_nom, nicad, surface_m2, message, parcelle_gele
    """
    from app.services.ccfm_verification import verifier_ccfm as _vcfm
    return await _vcfm(db=db, parcelle_id=parcelle_id)


@router.post("/verifier-ccfm-qr")
async def verifier_ccfm_qr_banque(
    qr_code: str = Query(...,
        description="Données QR CODE scannées depuis le certificat CCFM"),
    parcelle_id: Optional[str] = Query(None,
        description="UUID parcelle optionnel — cross-check QR vs parcelle"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Vérification CCFM par QR CODE depuis le module BANQUE.
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
async def ccfm_gate_banque(
    parcelle_id: str,
    action: str = Query("operation",
        description="Nom de l'action : vente, hypotheque, litige, regularisation…"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","BANQ_DIRECTEUR","BANQ_AGENT"])),
):
    """Gate CCFM — Bloque si CCFM absent, non signé, contesté ou parcelle gelée.
    À appeler avant tout acte transactionnel en module BANQUE.
    """
    from app.services.ccfm_verification import exiger_ccfm_valide
    ok, message = await exiger_ccfm_valide(
        db=db, parcelle_id=parcelle_id, contexte=f"BANQUE/{action}"
    )
    if not ok:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CCFM_GATE_FAILED",
                "message": message,
                "parcelle_id": parcelle_id,
                "action": action,
                "module": "BANQUE",
                "correction": "Émettre et signer un CCFM via POST /v1/ccfm/demandes",
            }
        )
    return {
        "gate_ok": True,
        "parcelle_id": parcelle_id,
        "message": message,
        "module": "BANQUE",
        "action": action,
    }