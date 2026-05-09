"""
FONCIER+ — Module Notaire COMPLET
Module indépendant — authentification juridique des actes fonciers.

Tables couvertes :
  notary_registry (droits_fonciers.py)    — registre des notaires agréés
  etude_notariale (020)                   — études notariales agréées
  property_transfers (droits_fonciers.py) — transferts de propriété
  mutation_dossier (008)                  — dossiers WF17 mutation/vente
  succession (011)                        — successions foncières WF19
  succession_parcelle (011)               — parcelles d'une succession
  succession_heritier (011)               — héritiers et parts
  indivision (011)                        — indivisions non résolues

Workflows portés :
  WF17 — Mutation de propriété par vente (6 étapes)
  WF18 — Donation foncière (5 étapes)
  WF19 — Succession foncière (6 étapes)

Triggers protecteurs :
  tg_wl5_bloquer_mutation_si_charges  — bloque si charges actives
  tg_succession_somme_parts           — somme parts héritiers = 100%
  tg_succession_bloc_si_indivision    — gel si indivision non résolue
"""
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy import select, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user
from app.models.droits_fonciers import (
    NotaryRegistry, PropertyTransfer, TypeTransfert,
)
from app.services.droits_service import DroitVersionService
from app.services.workflow_orchestrator import TransactionGate

router = APIRouter(prefix="/notaire", tags=["Notaire — Actes fonciers"])

ROLES_NOTAIRE  = ["ADMIN", "NOTAIRE"]
ROLES_READ     = ["ADMIN", "NOTAIRE", "DIRECTEUR_CADASTRE", "ADMIN_CADASTRE",
                  "CHEF_CCFM", "BANQ_DIRECTEUR", "AUDITEUR",
                  "DIRECTEUR_DOMAINE", "JUGE_FONCIER"]


# ══════════════════════════════════════════════════════════════════
# SCHÉMAS
# ══════════════════════════════════════════════════════════════════

class ActeIn(BaseModel):
    parcelle_id: str
    type_acte: str = Field(...,
        description="vente|donation|heritage|cession|echange|expropriation")
    vendeur_id: str
    acquereur_id: str
    pourcentage: float = Field(default=100.0, gt=0, le=100)
    prix_fcfa: Optional[int] = Field(None, gt=0,
        description='Prix de cession en FCFA — doit être > 0 si renseigné')
    nus_ccfm: str
    motif: str = ""


class MutationDossierIn(BaseModel):
    parcelle_id: str
    rnp_parcelle_id: str
    type_mutation: str = Field(...,
        description="vente|donation|heritage|cession|echange|expropriation")
    cedant_id: str
    acquereur_id: str
    pourcentage_cede: float = Field(..., gt=0, le=100)
    prix_fcfa: Optional[float] = Field(None, gt=0,
        description='Prix de la mutation en FCFA — doit être > 0 si renseigné')
    nus_ccfm: Optional[str] = None


class SuccessionIn(BaseModel):
    de_cujus_id: str = Field(...,
        description="UUID du right_holder défunt")
    type_succession: str = Field(...,
        description="testamentaire|ab_intestat|coutumiere|judiciaire|mixte")
    date_ouverture: datetime
    reference_acte: Optional[str] = Field(None, min_length=3,
        description='Référence de l acte de décès ou testament')
    parcelles_ids: List[str] = Field(default=[])


class HeritierIn(BaseModel):
    succession_id: str
    heritier_id: str
    lien_parente: str = Field(...,
        description="conjoint|enfant|parent|frere_soeur|autre")
    part_pct: float = Field(..., gt=0, le=100)


class EtudeNotarialeIn(BaseModel):
    numero_parquet: str = Field(..., min_length=3)
    nom_etude: str = Field(..., min_length=3)
    adresse: str
    commune_id: Optional[str] = None
    chambre_regionale: Optional[str] = None
    date_agrement: str = Field(..., description="YYYY-MM-DD")


def _sha(*parts) -> str:
    return hashlib.sha256(
        "|".join(str(p) for p in parts).encode()
    ).hexdigest()


# ══════════════════════════════════════════════════════════════════
# TABLEAU DE BORD NOTAIRE
# ══════════════════════════════════════════════════════════════════

@router.get("/tableau-de-bord", response_model=list)
async def tableau_de_bord_notaire(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE + ["AUDITEUR"])),
):
    """Vue consolidée du notaire : mutations, successions, actes récents."""
    stats: dict = {}
    queries = {
        "mutations_en_cours":
            "SELECT COUNT(*) FROM mutation_dossier "
            "WHERE statut NOT IN ('archive','rejete')",
        "successions_ouvertes":
            "SELECT COUNT(*) FROM succession "
            "WHERE statut NOT IN ('archive')",
        "indivisions_actives":
            "SELECT COUNT(*) FROM indivision WHERE est_active=true",
        "transferts_ce_mois":
            "SELECT COUNT(*) FROM property_transfers "
            "WHERE created_at >= date_trunc('month',NOW())",
        "notaires_agrees":
            "SELECT COUNT(*) FROM notary_registry "
            "WHERE statut_agrement='agree'",
    }
    for key, sql in queries.items():
        try:
            r = await db.execute(text(sql))
            stats[key] = r.scalar() or 0
        except Exception:
            stats[key] = 0

    # Mutations récentes du notaire connecté
    mutations_recentes = []
    try:
        r = await db.execute(text("""
            SELECT md.id, md.type_mutation, md.statut, md.created_at
            FROM mutation_dossier md
            JOIN notary_registry nr ON nr.id = md.notaire_id
            WHERE nr.user_id = :uid
            ORDER BY md.created_at DESC LIMIT 5
        """), {"uid": str(current_user.id)})
        mutations_recentes = [dict(row) for row in r.mappings().all()]
    except Exception:
        pass

    return {
        "acteur": current_user.email,
        "role": current_user.role,
        "module": "Notaire",
        "stats": stats,
        "mes_mutations_recentes": mutations_recentes,
        "horodatage": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════
# REGISTRE DES NOTAIRES
# ══════════════════════════════════════════════════════════════════

@router.get("/registre", response_model=list)
async def lister_notaires_agrees(
    statut: str = Query(default="agree",
        description="agree|suspendu|radie"),
    region_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Registre des notaires agréés au Niger."""
    filters = [NotaryRegistry.statut_agrement == statut]
    if region_id:
        filters.append(NotaryRegistry.region_id == region_id)
    r = await db.execute(
        select(NotaryRegistry).where(and_(*filters))
        .order_by(NotaryRegistry.nom_notaire)
        .offset((page-1)*limit).limit(limit)
    )
    return [{
        "id": str(n.id), "nom": n.nom_notaire,
        "numero_agrement": n.numero_agrement,
        "chambre": n.chambre_notariale,
        "statut": n.statut_agrement,
        "region_id": str(n.region_id) if n.region_id else None,
        "contact": n.contact,
        "date_agrement": n.date_agrement.isoformat() if n.date_agrement else None,
    } for n in r.scalars().all()]


@router.get("/registre/{notaire_id}", response_model=dict)
async def get_notaire(
    notaire_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Détail d'un notaire agréé avec son étude et ses statistiques."""
    r = await db.execute(
        select(NotaryRegistry).where(NotaryRegistry.id == notaire_id)
    )
    n = r.scalar_one_or_none()
    if not n:
        raise HTTPException(404, "Notaire introuvable")

    # Étude notariale
    etude = None
    try:
        r_e = await db.execute(text("""
            SELECT en.* FROM etude_notariale en
            JOIN notary_registry nr ON nr.etude_id = en.id
            WHERE nr.id = :nid
        """), {"nid": notaire_id})
        e = r_e.mappings().first()
        etude = dict(e) if e else None
    except Exception:
        pass

    # Statistiques actes
    stats: dict = {}
    try:
        r_s = await db.execute(text("""
            SELECT COUNT(*) AS nb_transferts,
                   COUNT(*) FILTER (WHERE type_transfert='vente') AS nb_ventes
            FROM property_transfers WHERE notaire_id = :nid
        """), {"nid": notaire_id})
        row = r_s.mappings().first()
        stats = dict(row) if row else {}
    except Exception:
        pass

    return {
        "id": str(n.id), "nom": n.nom_notaire,
        "numero_agrement": n.numero_agrement,
        "chambre": n.chambre_notariale,
        "statut": n.statut_agrement,
        "adresse": n.adresse, "contact": n.contact,
        "date_agrement": n.date_agrement.isoformat() if n.date_agrement else None,
        "date_fin_agrement": n.date_fin_agrement.isoformat() if n.date_fin_agrement else None,
        "etude": etude,
        "stats": stats,
    }


# ══════════════════════════════════════════════════════════════════
# ÉTUDES NOTARIALES (migration 020)
# ══════════════════════════════════════════════════════════════════

@router.post("/etudes", status_code=201)
async def creer_etude_notariale(
    payload: EtudeNotarialeIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN", "DIRECTEUR_URBANISME", "MINISTRE_URBANISME"]
    )),
):
    """Enregistre une étude notariale agréée (migration 020)."""
    etude_id = str(uuid.uuid4())
    try:
        await db.execute(text("""
            INSERT INTO etude_notariale
                (id, numero_parquet, nom_etude, adresse, commune_id,
                 chambre_regionale, statut, date_agrement, created_at)
            VALUES
                (:id,:parquet,:nom,:adr,:cid,
                 :chambre,'active',:date,NOW())
        """), {
            "id": etude_id, "parquet": payload.numero_parquet,
            "nom": payload.nom_etude, "adr": payload.adresse,
            "cid": payload.commune_id, "chambre": payload.chambre_regionale,
            "date": payload.date_agrement,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "uq_etude_parquet" in str(e):
            raise HTTPException(409,
                f"Numéro de parquet {payload.numero_parquet} déjà utilisé")
        raise HTTPException(400, str(e)[:200])
    return {"id": etude_id, "numero_parquet": payload.numero_parquet,
            "statut": "active"}


@router.get("/etudes", response_model=list)
async def lister_etudes(
    statut: str = Query(default="active"),
    commune_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Liste des études notariales agréées."""
    where = "WHERE statut=:s"
    params: dict = {"s": statut}
    if commune_id:
        where += " AND commune_id=:cid"; params["cid"] = commune_id
    try:
        r = await db.execute(text(
            f"SELECT * FROM etude_notariale {where} "
            "ORDER BY nom_etude"), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


# ══════════════════════════════════════════════════════════════════
# GATE TRANSACTIONNELLE
# ══════════════════════════════════════════════════════════════════

@router.get("/gate/{parcelle_id}", response_model=dict)
async def verifier_gate_transactionnelle(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Vérifie qu'une parcelle est éligible à un acte notarial.
    Contrôles : statut parcelle, gel judiciaire, blocks actifs,
    hypothèques, litiges, suspensions CCFM.
    """
    eligible, raison, details = await TransactionGate.check(db, parcelle_id)
    return {
        "parcelle_id": parcelle_id,
        "eligible": eligible,
        "raison": raison,
        "details": details,
    }


# ══════════════════════════════════════════════════════════════════
# ACTES NOTARIÉS — route principale
# ══════════════════════════════════════════════════════════════════

@router.post("/actes", status_code=201)
async def creer_acte_notarie(
    payload: ActeIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE)),
):
    """WF17/WF18 — Rédige et enregistre un acte notarié.

    Pipeline complet :
    1. Vérifie l'agrément du notaire
    2. Gate transactionnelle (6 contrôles)
    3. Vérifie NUS CCFM valide et signé
    4. Récupère le RNP actif de la parcelle
    5. Effectue le transfert via DroitVersionService
       (BUG-SVC-07 fix : SELECT FOR UPDATE NOWAIT anti race condition)
    6. Génère le numéro d'acte officiel NOT-YYYY-NNNNNNN
    """
    # 1. Notaire agréé
    r = await db.execute(
        select(NotaryRegistry).where(
            and_(NotaryRegistry.user_id == current_user.id,
                 NotaryRegistry.statut_agrement == "agree")
        )
    )
    notaire = r.scalar_one_or_none()
    if not notaire:
        raise HTTPException(403,
            "Utilisateur non enregistré comme notaire agréé "
            "(notary_registry.statut_agrement = 'agree' requis)")

    # 2. Gate transactionnelle
    eligible, raison, _ = await TransactionGate.check(db, payload.parcelle_id)
    if not eligible:
        raise HTTPException(400,
            f"Gate transactionnelle refusée : {raison}")

    # 3. NUS CCFM valide et signé
    r_ccfm = await db.execute(
        text("SELECT statut FROM demandes_ccfm WHERE nus=:nus"),
        {"nus": payload.nus_ccfm}
    )
    ccfm_row = r_ccfm.mappings().first()
    if not ccfm_row or ccfm_row["statut"] != "signe":
        raise HTTPException(400,
            f"NUS CCFM {payload.nus_ccfm} invalide ou non signé "
            f"(statut requis : 'signe', obtenu : "
            f"'{ccfm_row['statut'] if ccfm_row else 'introuvable'}')")

    # 4. RNP actif
    r_rnp = await db.execute(text("""
        SELECT r.id AS rnp_id, r.rnaf_id
        FROM rnp_demandes r
        JOIN rnp_parcelle_link l ON l.rnp_demande_id = r.id
        WHERE l.parcelle_id = :pid
          AND r.statut = 'actif'
        LIMIT 1
    """), {"pid": payload.parcelle_id})
    rnp_row = r_rnp.mappings().first()
    if not rnp_row:
        raise HTTPException(400,
            "Aucun RNP actif pour cette parcelle")

    # 5. Transfert
    try:
        type_t = TypeTransfert(payload.type_acte)
    except ValueError:
        raise HTTPException(422,
            f"Type d'acte inconnu : {payload.type_acte}. "
            f"Valeurs : vente|donation|heritage|cession|echange|expropriation")

    ref_acte = f"ACT-{datetime.now().year}-{uuid.uuid4().hex[:8].upper()}"
    try:
        transfer, new_droit = await DroitVersionService.effectuer_transfert(
            db=db,
            rnp_parcelle_id=str(rnp_row["rnp_id"]),
            rnaf_id=str(rnp_row["rnaf_id"]),
            ancien_titulaire_id=payload.vendeur_id,
            nouveau_titulaire_id=payload.acquereur_id,
            pourcentage_transfere=payload.pourcentage,
            type_transfert=type_t,
            date_transfert=datetime.now(timezone.utc),
            enregistre_par_id=str(current_user.id),
            notaire_id=str(notaire.id),
            acte_reference=ref_acte,
            ccfm_validation_id=None,
        )
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))

    # 6. Numéro d'acte officiel
    r_num = await db.execute(
        text("SELECT generer_numero_officiel('NOT','seq_acte_notaire')")
    )
    num_acte = r_num.scalar()

    await db.commit()
    return {
        "numero_acte": num_acte,
        "transfer_id": str(transfer.id),
        "acte_reference": ref_acte,
        "nouveau_droit_sha256": new_droit.sha256_version,
        "notaire": notaire.nom_notaire,
        "numero_agrement": notaire.numero_agrement,
        "type_acte": payload.type_acte,
        "pourcentage_transfere": payload.pourcentage,
    }


@router.get("/actes/parcelle/{parcelle_id}", response_model=dict)
async def historique_actes_parcelle(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Historique de tous les transferts notariés d'une parcelle."""
    r = await db.execute(text("""
        SELECT pt.id, pt.type_transfert, pt.pourcentage_transfere,
               pt.acte_reference, pt.date_transfert,
               pt.sha256_transfert,
               nr.nom_notaire
        FROM property_transfers pt
        LEFT JOIN notary_registry nr ON nr.id = pt.notaire_id
        WHERE pt.parcelle_id = :pid
        ORDER BY pt.date_transfert DESC
    """), {"pid": parcelle_id})
    return [dict(row) for row in r.mappings().all()]


# ══════════════════════════════════════════════════════════════════
# WF17 — MUTATION DOSSIER (vente, donation, cession)
# ══════════════════════════════════════════════════════════════════

@router.post("/mutations", status_code=201)
async def ouvrir_mutation_dossier(
    payload: MutationDossierIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE)),
):
    """Ouvre un dossier de mutation (WF17).
    Trigger tg_wl5_bloquer_mutation_si_charges bloque si charges actives.
    """
    TYPES = {"vente","donation","heritage","cession","echange","expropriation"}
    if payload.type_mutation not in TYPES:
        raise HTTPException(422, f"type_mutation : {TYPES}")

    sha = _sha(payload.parcelle_id, payload.type_mutation,
               payload.cedant_id, payload.acquereur_id)
    dos_id = str(uuid.uuid4())

    # Récupérer notaire
    r = await db.execute(
        select(NotaryRegistry).where(
            and_(NotaryRegistry.user_id == current_user.id,
                 NotaryRegistry.statut_agrement == "agree")
        )
    )
    notaire = r.scalar_one_or_none()
    if not notaire:
        raise HTTPException(403, "Notaire agréé requis")

    try:
        await db.execute(text("""
            INSERT INTO mutation_dossier
                (id, parcelle_id, rnp_parcelle_id, type_mutation,
                 cedant_id, acquereur_id, pourcentage_cede, prix_fcfa,
                 statut, notaire_id, ccfm_validation_id,
                 sha256_dossier, cree_par, created_at)
            VALUES
                (:id,:parc,:rnp,:type,
                 :cedant,:acq,:pct,:prix,
                 'initie',:nid,:ccfm,
                 :sha,:uid,NOW())
        """), {
            "id": dos_id, "parc": payload.parcelle_id,
            "rnp": payload.rnp_parcelle_id,
            "type": payload.type_mutation,
            "cedant": payload.cedant_id,
            "acq": payload.acquereur_id,
            "pct": payload.pourcentage_cede,
            "prix": payload.prix_fcfa,
            "nid": str(notaire.id),
            "ccfm": payload.nus_ccfm,
            "sha": sha, "uid": str(current_user.id),
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "bloquer_mutation" in str(e).lower():
            raise HTTPException(400,
                f"Mutation bloquée (tg_wl5) : {str(e)[:300]}")
        raise HTTPException(400, str(e)[:200])

    return {"id": dos_id, "statut": "initie",
            "sha256_dossier": sha, "notaire": notaire.nom_notaire}


@router.get("/mutations", response_model=list)
async def lister_mutations(
    statut: Optional[str] = Query(None),
    type_mutation: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Liste les dossiers de mutation avec filtres."""
    where = "WHERE 1=1"
    params: dict = {"l": limit, "o": (page-1)*limit}
    if statut: where += " AND statut=:s"; params["s"] = statut
    if type_mutation: where += " AND type_mutation=:t"; params["t"] = type_mutation
    try:

        # Filtre acteur — ScopeFilter
        try:
            from app.core.scope_filter import ScopeFilter as _SF
            _scope = _SF.from_user(current_user)
            if not _scope.peut_voir_national:
                if _scope.region:
                    where += " AND pt.region = :_scope_region"
                    params["_scope_region"] = _scope.region
                elif _scope.entite_id:
                    where += " AND pt.notaire_id = :_scope_entite"
                    params["_scope_entite"] = _scope.entite_id
        except Exception:
            pass
        r = await db.execute(text(
            f"SELECT * FROM mutation_dossier {where} "
            "ORDER BY created_at DESC LIMIT :l OFFSET :o"), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


@router.patch("/mutations/{dos_id}/statut")
async def avancer_mutation(
    dos_id: str,
    nouveau_statut: str = Query(...,
        description="ccfm_verif|notaire_redaction|signature|"
                    "enregistrement|archive|rejete"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","NOTAIRE","CHEF_CCFM",
         "DIRECTEUR_DOMAINE","DIRECTEUR_CADASTRE"]
    )),
):
    """Avance le statut d'un dossier de mutation (WF17)."""
    STATUTS = {"ccfm_verif","notaire_redaction","signature",
               "enregistrement","archive","rejete"}
    if nouveau_statut not in STATUTS:
        raise HTTPException(422, f"Statut invalide : {STATUTS}")
    try:
        await db.execute(text(
            "UPDATE mutation_dossier SET statut=:s,updated_at=NOW() WHERE id=:id"
        ), {"s": nouveau_statut, "id": dos_id})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"id": dos_id, "statut": nouveau_statut}


# ══════════════════════════════════════════════════════════════════
# WF19 — SUCCESSION FONCIÈRE
# ══════════════════════════════════════════════════════════════════

@router.post("/successions", status_code=201)
async def ouvrir_succession(
    payload: SuccessionIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","NOTAIRE","MAIRE","JUGE_FONCIER"]
    )),
):
    """Ouvre une succession foncière (étape 1 WF19).
    Déclenche tg_succession_bloc_si_indivision sur les parcelles concernées.
    """
    TYPES = {"testamentaire","ab_intestat","coutumiere","judiciaire","mixte"}
    if payload.type_succession not in TYPES:
        raise HTTPException(422, f"type_succession : {TYPES}")

    sha = _sha(payload.de_cujus_id, payload.type_succession,
               payload.date_ouverture.isoformat())
    succ_id = str(uuid.uuid4())

    try:
        await db.execute(text("""
            INSERT INTO succession
                (id, de_cujus_id, type_succession, statut,
                 date_ouverture, reference_acte,
                 sha256_succession, cree_par, created_at)
            VALUES
                (:id,:dcj,:type,'ouverte',
                 :date,:ref,:sha,:uid,NOW())
        """), {
            "id": succ_id, "dcj": payload.de_cujus_id,
            "type": payload.type_succession,
            "date": payload.date_ouverture,
            "ref": payload.reference_acte,
            "sha": sha, "uid": str(current_user.id),
        })

        # Rattacher les parcelles
        for parc_id in payload.parcelles_ids:
            try:
                await db.execute(text("""
                    INSERT INTO succession_parcelle
                        (id,succession_id,parcelle_id,en_indivision,created_at)
                    VALUES
                        (gen_random_uuid(),:sid,:pid,true,NOW())
                    ON CONFLICT (succession_id,parcelle_id) DO NOTHING
                """), {"sid": succ_id, "pid": parc_id})
            except Exception:
                pass

        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    return {
        "id": succ_id,
        "type_succession": payload.type_succession,
        "statut": "ouverte",
        "sha256_succession": sha,
        "nb_parcelles": len(payload.parcelles_ids),
    }


@router.get("/successions", response_model=list)
async def lister_successions(
    statut: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Liste les successions foncières."""
    where = "WHERE 1=1"
    params: dict = {"l": limit, "o": (page-1)*limit}
    if statut: where += " AND statut=:s"; params["s"] = statut
    try:

        # Filtre acteur — ScopeFilter
        try:
            from app.core.scope_filter import ScopeFilter as _SF
            _scope = _SF.from_user(current_user)
            if not _scope.peut_voir_national:
                if _scope.region:
                    where += " AND pt.region = :_scope_region"
                    params["_scope_region"] = _scope.region
                elif _scope.entite_id:
                    where += " AND pt.notaire_id = :_scope_entite"
                    params["_scope_entite"] = _scope.entite_id
        except Exception:
            pass
        r = await db.execute(text(
            f"SELECT * FROM succession {where} "
            "ORDER BY created_at DESC LIMIT :l OFFSET :o"), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


@router.get("/successions/{succ_id}", response_model=dict)
async def get_succession(
    succ_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Détail d'une succession avec héritiers et parcelles."""
    try:
        r = await db.execute(
            text("SELECT * FROM succession WHERE id=:id"), {"id": succ_id})
        row = r.mappings().first()
        if not row:
            raise HTTPException(404, "Succession introuvable")
        result = dict(row)

        # Héritiers
        r_h = await db.execute(text("""
            SELECT sh.heritier_id, sh.lien_parente,
                   sh.part_pct, sh.a_accepte
            FROM succession_heritier sh
            WHERE sh.succession_id=:sid
            ORDER BY sh.part_pct DESC
        """), {"sid": succ_id})
        result["heritiers"] = [dict(h) for h in r_h.mappings().all()]

        # Parcelles
        r_p = await db.execute(text("""
            SELECT sp.parcelle_id, sp.en_indivision,
                   sp.est_traite, p.nicad
            FROM succession_parcelle sp
            JOIN parcelles p ON p.id = sp.parcelle_id
            WHERE sp.succession_id=:sid
        """), {"sid": succ_id})
        result["parcelles"] = [dict(p) for p in r_p.mappings().all()]

        # Somme parts
        somme = sum(h["part_pct"] for h in result["heritiers"])
        result["somme_parts_pct"] = round(somme, 2)
        result["parts_equilibrees"] = abs(somme - 100) < 0.01

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.post("/successions/{succ_id}/heritiers", status_code=201)
async def ajouter_heritier(
    succ_id: str,
    payload: HeritierIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","NOTAIRE","JUGE_FONCIER"]
    )),
):
    """Ajoute un héritier à une succession.
    Trigger tg_succession_somme_parts vérifie que la somme = 100%.
    """
    LIENS = {"conjoint","enfant","parent","frere_soeur","autre"}
    if payload.lien_parente not in LIENS:
        raise HTTPException(422, f"lien_parente : {LIENS}")

    try:
        await db.execute(text("""
            INSERT INTO succession_heritier
                (id,succession_id,heritier_id,lien_parente,
                 part_pct,a_accepte,created_at)
            VALUES
                (gen_random_uuid(),:sid,:hid,:lien,
                 :pct,false,NOW())
        """), {
            "sid": succ_id, "hid": payload.heritier_id,
            "lien": payload.lien_parente, "pct": payload.part_pct,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "somme" in str(e).lower() or "100" in str(e):
            raise HTTPException(400,
                f"Héritier refusé (tg_succession_somme_parts) : {str(e)[:300]}")
        raise HTTPException(400, str(e)[:200])

    return {
        "succession_id": succ_id,
        "heritier_id": payload.heritier_id,
        "part_pct": payload.part_pct,
        "lien_parente": payload.lien_parente,
    }


@router.patch("/successions/{succ_id}/statut")
async def avancer_succession(
    succ_id: str,
    nouveau_statut: str = Query(...,
        description="en_instruction|jugement|partage|archive|litigieuse"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","NOTAIRE","JUGE_FONCIER","DIRECTEUR_CADASTRE"]
    )),
):
    """Avance le statut d'une succession (WF19)."""
    STATUTS = {"en_instruction","jugement","partage","archive","litigieuse"}
    if nouveau_statut not in STATUTS:
        raise HTTPException(422, f"Statut invalide : {STATUTS}")
    try:
        await db.execute(text("""
            UPDATE succession SET statut=:s,updated_at=NOW() WHERE id=:id
        """), {"s": nouveau_statut, "id": succ_id})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"id": succ_id, "statut": nouveau_statut}


# ══════════════════════════════════════════════════════════════════
# INDIVISIONS
# ══════════════════════════════════════════════════════════════════

@router.get("/indivisions", response_model=list)
async def lister_indivisions_actives(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Liste les indivisions non résolues (à traiter en priorité)."""
    try:
        r = await db.execute(text("""
            SELECT i.id, i.succession_id, i.parcelle_id,
                   i.date_debut, p.nicad,
                   EXTRACT(DAY FROM NOW() - i.date_debut)::INT AS jours_indivision
            FROM indivision i
            JOIN parcelles p ON p.id = i.parcelle_id
            WHERE i.est_active = true
            ORDER BY i.date_debut ASC
            LIMIT :l OFFSET :o
        """), {"l": limit, "o": (page-1)*limit})
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


@router.patch("/indivisions/{indivision_id}/resoudre")
async def resoudre_indivision(
    indivision_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","NOTAIRE","JUGE_FONCIER"]
    )),
):
    """Marque une indivision comme résolue — lève le gel des parcelles."""
    try:
        await db.execute(text("""
            UPDATE indivision
            SET est_active=false, date_fin=NOW()
            WHERE id=:id
        """), {"id": indivision_id})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"id": indivision_id, "est_active": False,
            "message": "Indivision résolue — gel parcelles levé"}


# ══════════════════════════════════════════════════════════════════
# STATISTIQUES
# ══════════════════════════════════════════════════════════════════

@router.get("/stats", response_model=list)
async def stats_module_notaire(
    annee: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Statistiques du module Notaire."""
    where_yr = ""
    params: dict = {}
    if annee:
        where_yr = "AND EXTRACT(YEAR FROM created_at)=:yr"
        params["yr"] = annee

    stats: dict = {}
    for key, sql in [
        ("transferts_par_type",
         f"SELECT type_transfert, COUNT(*) nb FROM property_transfers "
         f"WHERE 1=1 {where_yr} GROUP BY type_transfert ORDER BY nb DESC"),
        ("mutations_par_statut",
         f"SELECT statut, COUNT(*) nb FROM mutation_dossier "
         f"WHERE 1=1 {where_yr} GROUP BY statut ORDER BY nb DESC"),
        ("successions_par_type",
         f"SELECT type_succession, COUNT(*) nb FROM succession "
         f"WHERE 1=1 {where_yr} GROUP BY type_succession ORDER BY nb DESC"),
    ]:
        try:
            r = await db.execute(text(sql), params)
            stats[key] = [dict(row) for row in r.mappings().all()]
        except Exception:
            stats[key] = []

    try:
        r = await db.execute(text(
            "SELECT COUNT(*) FROM indivision WHERE est_active=true"))
        stats["indivisions_actives"] = r.scalar() or 0
    except Exception:
        stats["indivisions_actives"] = 0

    return {"annee_filtre": annee, "stats": stats}


# ══════════════════════════════════════════════════════════════════
# SOUS-MODULE COMMUNICATION
# Bordereaux inter-services, escalades workflow, templates
# Tables : bordereau_envoi (021), workflow_escalade (007),
#          document_template (021)
# ══════════════════════════════════════════════════════════════════

class BordereauIn(BaseModel):
    service_expediteur: str = Field(...,
        description="notaire|cadastre|ccfm|justice|banque|annf|domaine|commune")
    service_destinataire: str
    dossiers_transmis: List[str] = Field(default=[],
        description="Liste de numéros de dossiers transmis")
    signature_pki: Optional[str] = None


@router.post("/communication/bordereaux", status_code=201,
             tags=["Notaire — Communication"])
async def creer_bordereau_envoi(
    payload: BordereauIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE + [
        "DIRECTEUR_CADASTRE","CHEF_CCFM","ARCHIVISTE_ANNF","DIRECTEUR_DOMAINE"
    ])),
):
    """Crée un bordereau d'envoi inter-services (BOR-YYYY-NNNNNNN).
    Trace toutes les transmissions de dossiers entre services.
    """
    r = await db.execute(
        text("SELECT generer_numero_officiel('BOR','seq_bordereau')")
    )
    num_bordereau = r.scalar()
    try:
        await db.execute(text("""
            INSERT INTO bordereau_envoi
                (id, numero_bordereau, date_envoi,
                 service_expediteur, service_destinataire,
                 dossiers_transmis, signataire_id, signature_pki)
            VALUES
                (gen_random_uuid(), :num, NOW(),
                 :exp, :dest,
                 :dossiers::varchar[], :uid, :pki)
        """), {
            "num": num_bordereau,
            "exp": payload.service_expediteur,
            "dest": payload.service_destinataire,
            "dossiers": payload.dossiers_transmis,
            "uid": str(current_user.id),
            "pki": payload.signature_pki,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {
        "numero_bordereau": num_bordereau,
        "service_expediteur": payload.service_expediteur,
        "service_destinataire": payload.service_destinataire,
        "nb_dossiers": len(payload.dossiers_transmis),
    }


@router.get("/communication/bordereaux",
            tags=["Notaire — Communication"])
async def lister_bordereaux(
    service: Optional[str] = Query(None,
        description="Filtre expéditeur OU destinataire"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Liste les bordereaux d'envoi (envoyés et reçus)."""
    where = "WHERE 1=1"
    params: dict = {"l": limit, "o": (page-1)*limit}
    if service:
        where += " AND (service_expediteur=:s OR service_destinataire=:s)"
        params["s"] = service
    try:

        # Filtre acteur — ScopeFilter
        try:
            from app.core.scope_filter import ScopeFilter as _SF
            _scope = _SF.from_user(current_user)
            if not _scope.peut_voir_national:
                if _scope.region:
                    where += " AND b.region = :_scope_region"
                    params["_scope_region"] = _scope.region
                elif _scope.entite_id:
                    where += " AND b.notaire_id = :_scope_entite"
                    params["_scope_entite"] = _scope.entite_id
        except Exception:
            pass
        r = await db.execute(text(
            f"SELECT * FROM bordereau_envoi {where} "
            "ORDER BY date_envoi DESC LIMIT :l OFFSET :o"), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


@router.patch("/communication/bordereaux/{num}/accuser-reception",
              tags=["Notaire — Communication"])
async def accuser_reception_bordereau(
    num: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Accuse réception d'un bordereau d'envoi."""
    try:
        await db.execute(text("""
            UPDATE bordereau_envoi
            SET recu_par_id=:uid, recu_le=NOW()
            WHERE numero_bordereau=:num AND recu_le IS NULL
        """), {"uid": str(current_user.id), "num": num})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"numero_bordereau": num, "accuse_par": current_user.email}


@router.get("/communication/escalades",
            tags=["Notaire — Communication"])
async def lister_escalades_notaire(
    traite: bool = Query(default=False),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE + ["AUDITEUR"])),
):
    """Escalades workflow en attente pour le module Notaire.
    Les escalades non traitées signalent des dossiers en retard.
    """
    try:
        r = await db.execute(text("""
            SELECT we.id, we.step_code, we.type_escalade,
                   we.retard_heures, we.message_escalade,
                   we.created_at, wi.numero_dossier_maitre
            FROM workflow_escalade we
            JOIN workflow_instance wi ON wi.id = we.instance_id
            JOIN workflow_definition wd ON wd.id = wi.definition_id
            WHERE wd.type_workflow IN ('MUTATION_VENTE','DONATION','SUCCESSION_FONCIERE')
              AND we.est_traitee = :traite
            ORDER BY we.created_at DESC
            LIMIT :l OFFSET :o
        """), {"traite": traite, "l": limit, "o": (page-1)*limit})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


@router.patch("/communication/escalades/{escalade_id}/traiter",
              tags=["Notaire — Communication"])
async def traiter_escalade(
    escalade_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE + [
        "DIRECTEUR_CADASTRE","CHEF_CCFM"
    ])),
):
    """Marque une escalade workflow comme traitée."""
    try:
        await db.execute(text("""
            UPDATE workflow_escalade
            SET est_traitee=true, traitee_at=NOW()
            WHERE id=:id
        """), {"id": escalade_id})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"id": escalade_id, "est_traitee": True}


@router.get("/communication/templates",
            tags=["Notaire — Communication"])
async def lister_templates_notaire(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE + ["AUDITEUR"])),
):
    """Templates Jinja2 des actes notariaux (document_template)."""
    try:
        r = await db.execute(text("""
            SELECT id, code_template, version, type_acte,
                   libelle, champs_fusion, date_entree_vigueur,
                   date_fin_vigueur, is_active
            FROM document_template
            WHERE module='notaire' AND is_active=true
            ORDER BY code_template, version DESC
        """))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


@router.get("/communication/templates/{code_template}",
            tags=["Notaire — Communication"])
async def get_template_notaire(
    code_template: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE + ["AUDITEUR"])),
):
    """Retourne le template actif d'un type d'acte notarial.
    Utilise la fonction get_template_applicable(code, date).
    """
    try:
        r = await db.execute(text("""
            SELECT t.*
            FROM document_template t
            WHERE t.id = get_template_applicable(:code, CURRENT_DATE)
        """), {"code": code_template})
        row = r.mappings().first()
        if not row:
            raise HTTPException(404,
                f"Template {code_template} introuvable ou expiré")
        return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


# ══════════════════════════════════════════════════════════════════
# SOUS-MODULE ARCHIVES NOTARIALES
# Tables : notarial_act (legacy 001), annf_archive_links (004)
# Vue   : v_repertoire_mensuel_notaire (021)
# ══════════════════════════════════════════════════════════════════

@router.get("/archives/repertoire",
            tags=["Notaire — Archives"])
async def get_repertoire_mensuel(
    annee: Optional[int] = Query(None),
    mois: Optional[int] = Query(None, ge=1, le=12),
    notaire_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE + [
        "DIRECTEUR_CADASTRE","AUDITEUR","ARCHIVISTE_ANNF"
    ])),
):
    """Répertoire mensuel des actes notariaux (v_repertoire_mensuel_notaire).
    SHA-256 chaîné pour intégrité — vue officielle certifiée.
    """
    where = "WHERE 1=1"
    params: dict = {}
    if annee:
        where += " AND annee=:yr"; params["yr"] = annee
    if mois:
        where += " AND mois=:mo"; params["mo"] = mois
    if notaire_id:
        where += " AND notaire_id=:nid"; params["nid"] = notaire_id
    try:
        r = await db.execute(text(
            f"SELECT * FROM v_repertoire_mensuel_notaire {where} "
            "ORDER BY annee DESC, mois DESC, numero_chrono_officiel"
        ), params)
        rows = [dict(row) for row in r.mappings().all()]
        return {
            "total": len(rows),
            "filtres": {"annee": annee, "mois": mois},
            "actes": rows,
        }
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.get("/archives/integrite",
            tags=["Notaire — Archives"])
async def verifier_integrite_chaine(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","AUDITEUR","ARCHIVISTE_ANNF"])),
):
    """Vérifie l'intégrité de la chaîne SHA-256 des actes notariaux
    via la vue v_integrite_hash (migration 016).
    Détecte toute rupture dans la chaîne de hachage.
    """
    try:
        r = await db.execute(text("""
            SELECT table_name, row_id, hash_actuel,
                   hash_precedent, integre
            FROM v_integrite_hash
            WHERE table_name = 'notarial_act'
              AND NOT integre
            LIMIT 50
        """))
        ruptures = [dict(row) for row in r.mappings().all()]
        r_total = await db.execute(text(
            "SELECT COUNT(*) FROM v_integrite_hash "
            "WHERE table_name='notarial_act'"
        ))
        total = r_total.scalar() or 0
        return {
            "total_actes_verifies": total,
            "nb_ruptures": len(ruptures),
            "integrite_globale": len(ruptures) == 0,
            "ruptures": ruptures,
        }
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.post("/archives/archiver-acte",
             status_code=201, tags=["Notaire — Archives"])
async def archiver_acte_annf(
    acte_id: str = Query(..., description="UUID de l'acte à archiver"),
    snapshot: Optional[dict] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","ARCHIVISTE_ANNF","NOTAIRE"]
    )),
):
    """Archive un acte notarial dans l'ANNF (annf_archive_links).
    Génère un IDA (Identifiant d'Archive) ANNF-YYYY-NNNNNNN.
    Le snapshot JSON est immuable après archivage (WORM).
    """
    import json as _json
    snap_str = _json.dumps(snapshot or {"acte_id": acte_id,
                                         "archive_at": datetime.now(timezone.utc).isoformat()},
                           sort_keys=True)
    sha = hashlib.sha256(
        f"{acte_id}:{snap_str}".encode()
    ).hexdigest()

    # Générer IDA
    try:
        r = await db.execute(text("SELECT generate_annf_ida()"))
        ida = r.scalar()
    except Exception:
        ida = f"ANNF-{datetime.now().year}-{uuid.uuid4().hex[:7].upper()}"

    try:
        await db.execute(text("""
            INSERT INTO annf_archive_links
                (id, type_archive, entite_id, entite_table,
                 snapshot_json, sha256_snapshot, ida,
                 scelle, archive_par, created_at)
            VALUES
                (gen_random_uuid(), 'acte_notarie'::type_archive_annf,
                 :eid::uuid, 'notarial_act',
                 :snap::jsonb, :sha, :ida,
                 false, :uid, NOW())
        """), {
            "eid": acte_id, "snap": snap_str,
            "sha": sha, "ida": ida,
            "uid": str(current_user.id),
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    return {
        "ida": ida,
        "acte_id": acte_id,
        "sha256": sha,
        "scelle": False,
        "message": "Acte archivé dans ANNF — scellement requis pour WORM définitif",
    }


@router.post("/archives/{ida}/sceller",
             tags=["Notaire — Archives"])
async def sceller_archive_notariale(
    ida: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","ARCHIVISTE_ANNF","RESPONSABLE_ANNF"])),
):
    """Scelle définitivement une archive notariale (WORM irréversible)."""
    try:
        result = await db.execute(text("""
            UPDATE annf_archive_links
            SET scelle=true, scelle_at=NOW(), scelle_par=:uid
            WHERE ida=:ida AND scelle=false
            RETURNING id, ida, sha256_snapshot
        """), {"uid": str(current_user.id), "ida": ida})
        row = result.mappings().first()
        if not row:
            raise HTTPException(404,
                f"Archive {ida} introuvable ou déjà scellée")
        await db.commit()
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {
        "ida": ida,
        "scelle": True,
        "sha256": row["sha256_snapshot"],
        "message": "Archive WORM — immuable définitivement",
    }


@router.get("/archives/acte/{acte_id}",
            tags=["Notaire — Archives"])
async def get_archive_acte(
    acte_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Consulte l'archive ANNF d'un acte notarial."""
    try:
        r = await db.execute(text("""
            SELECT al.ida, al.sha256_snapshot, al.scelle,
                   al.scelle_at, al.created_at, al.version
            FROM annf_archive_links al
            WHERE al.entite_id = :eid::uuid
              AND al.type_archive = 'acte_notarie'
            ORDER BY al.version DESC
            LIMIT 1
        """), {"eid": acte_id})
        row = r.mappings().first()
        if not row:
            raise HTTPException(404, "Acte non archivé dans l'ANNF")
        return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


# ══════════════════════════════════════════════════════════════════
# SOUS-MODULE MESSAGERIE INTER-NOTAIRES
# Tables : fil_discussion, participant_fil, message_notarial,
#          piece_jointe_message, accuse_lecture (migration 026)
#
# Règles :
#   - Expéditeur doit être notaire agréé (trigger fn_verifier_notaire_agree_message)
#   - Chambre restreinte : seuls les notaires de la chambre voient le fil
#   - SHA-256 chaîné sur chaque message (trigger fn_message_sha256_chain)
#   - Accusé de lecture déclenche statut 'lu' (trigger fn_marquer_message_lu)
# ══════════════════════════════════════════════════════════════════

class FilDiscussionIn(BaseModel):
    sujet: str = Field(..., min_length=3, max_length=300)
    type_fil: str = Field(...,
        description="dossier_foncier|consultation_juridique|acte_commun|"
                    "succession_complexe|general|alerte")
    chambre_notariale: Optional[str] = Field(None,
        description="Restreindre à une chambre — NULL = toutes chambres")
    parcelle_id: Optional[str] = None
    dossier_ref: Optional[str] = None
    participants_ids: List[str] = Field(default=[],
        description="UUIDs notary_registry des participants initiaux")


class MessageIn(BaseModel):
    fil_id: Optional[str] = Field(None,
        description="UUID du fil — NULL pour message direct")
    destinataire_id: Optional[str] = Field(None,
        description="UUID notary_registry — NULL = message au fil entier")
    sujet: Optional[str] = Field(None, max_length=300)
    corps: str = Field(..., min_length=1)
    priorite: str = Field(default="normale",
        description="normale|urgente|confidentielle")
    acte_ref_id: Optional[str] = None

    @validator("fil_id", "destinataire_id", always=True)
    def un_dest_requis(cls, v, values):
        # Validation cross-field légère — le reste est géré en DB
        return v


class MessageDirectIn(BaseModel):
    """Message direct entre deux notaires (sans fil)."""
    destinataire_notaire_id: str
    sujet: str = Field(..., min_length=3, max_length=300)
    corps: str = Field(..., min_length=1)
    priorite: str = Field(default="normale",
        description="normale|urgente|confidentielle")
    acte_ref_id: Optional[str] = None


# ─── Fils de discussion ───────────────────────────────────────────

@router.post("/messagerie/fils", status_code=201,
             tags=["Notaire — Messagerie"])
async def creer_fil_discussion(
    payload: FilDiscussionIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE)),
):
    """Crée un fil de discussion (intra ou inter-chambres).

    Accès restreint à la chambre si chambre_notariale est renseigné.
    Les participants_ids reçoivent une invitation automatique.
    """
    TYPES_FIL = {"dossier_foncier","consultation_juridique","acte_commun",
                 "succession_complexe","general","alerte"}
    if payload.type_fil not in TYPES_FIL:
        raise HTTPException(422, f"type_fil : {TYPES_FIL}")

    # Vérifier que l'initiateur est notaire agréé
    r = await db.execute(
        select(NotaryRegistry).where(
            and_(NotaryRegistry.user_id == current_user.id,
                 NotaryRegistry.statut_agrement == "agree")
        )
    )
    notaire = r.scalar_one_or_none()
    if not notaire:
        raise HTTPException(403, "Notaire agréé requis")

    fil_id = str(uuid.uuid4())
    try:
        await db.execute(text("""
            INSERT INTO fil_discussion
                (id, sujet, type_fil, chambre_notariale,
                 parcelle_id, dossier_ref, cree_par, created_at)
            VALUES
                (:id, :sujet, :type, :chambre,
                 :parc, :dos_ref, :uid, NOW())
        """), {
            "id": fil_id, "sujet": payload.sujet,
            "type": payload.type_fil,
            "chambre": payload.chambre_notariale,
            "parc": payload.parcelle_id,
            "dos_ref": payload.dossier_ref,
            "uid": str(current_user.id),
        })

        # Ajouter l'initiateur comme participant
        await db.execute(text("""
            INSERT INTO participant_fil
                (id, fil_id, notaire_id, role_participant, joined_at)
            VALUES
                (gen_random_uuid(), :fid, :nid, 'initiateur', NOW())
            ON CONFLICT (fil_id, notaire_id) DO NOTHING
        """), {"fid": fil_id, "nid": str(notaire.id)})

        # Ajouter les participants initiaux
        for notaire_id in payload.participants_ids:
            try:
                await db.execute(text("""
                    INSERT INTO participant_fil
                        (id, fil_id, notaire_id, role_participant, joined_at)
                    VALUES
                        (gen_random_uuid(), :fid, :nid, 'membre', NOW())
                    ON CONFLICT (fil_id, notaire_id) DO NOTHING
                """), {"fid": fil_id, "nid": notaire_id})
            except Exception:
                pass

        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    return {
        "id": fil_id,
        "sujet": payload.sujet,
        "type_fil": payload.type_fil,
        "chambre_notariale": payload.chambre_notariale,
        "nb_participants": 1 + len(payload.participants_ids),
    }


@router.get("/messagerie/fils",
            tags=["Notaire — Messagerie"])
async def lister_mes_fils(
    chambre: Optional[str] = Query(None),
    type_fil: Optional[str] = Query(None),
    archive: bool = Query(default=False),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE + ["AUDITEUR"])),
):
    """Fils de discussion accessibles au notaire connecté.
    Filtrés par sa chambre notariale automatiquement.
    """
    # Récupérer la chambre du notaire connecté
    r = await db.execute(
        select(NotaryRegistry).where(NotaryRegistry.user_id == current_user.id)
    )
    notaire = r.scalar_one_or_none()
    if not notaire:
        raise HTTPException(403, "Notaire agréé requis")

    try:
        r = await db.execute(text("""
            SELECT DISTINCT fd.id, fd.sujet, fd.type_fil,
                   fd.chambre_notariale, fd.dossier_ref,
                   fd.est_archive, fd.created_at,
                   fd.updated_at,
                   (SELECT COUNT(*) FROM message_notarial mn
                    WHERE mn.fil_id = fd.id) AS nb_messages,
                   (SELECT COUNT(*) FROM message_notarial mn
                    WHERE mn.fil_id = fd.id
                      AND mn.statut = 'envoye'
                      AND mn.expediteur_id != :nid) AS non_lus
            FROM fil_discussion fd
            LEFT JOIN participant_fil pf ON pf.fil_id = fd.id
                AND pf.notaire_id = :nid AND pf.actif = true
            WHERE fd.est_archive = :arch
              AND (
                fd.chambre_notariale IS NULL
                OR fd.chambre_notariale = :chambre
                OR pf.notaire_id IS NOT NULL
              )
              AND (:type_fil IS NULL OR fd.type_fil = :type_fil)
            ORDER BY fd.updated_at DESC NULLS LAST, fd.created_at DESC
            LIMIT :l OFFSET :o
        """), {
            "nid": str(notaire.id),
            "chambre": notaire.chambre_notariale or "",
            "arch": archive,
            "type_fil": type_fil,
            "l": limit, "o": (page-1)*limit,
        })
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.post("/messagerie/fils/{fil_id}/participants",
             status_code=201, tags=["Notaire — Messagerie"])
async def ajouter_participant(
    fil_id: str,
    notaire_id: str = Query(..., description="UUID notary_registry"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE)),
):
    """Ajoute un notaire comme participant à un fil."""
    try:
        await db.execute(text("""
            INSERT INTO participant_fil
                (id, fil_id, notaire_id, role_participant, joined_at)
            VALUES
                (gen_random_uuid(), :fid, :nid, 'membre', NOW())
            ON CONFLICT (fil_id, notaire_id)
            DO UPDATE SET actif = true
        """), {"fid": fil_id, "nid": notaire_id})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"fil_id": fil_id, "notaire_id": notaire_id, "statut": "participant_actif"}


# ─── Messages ─────────────────────────────────────────────────────

@router.post("/messagerie/messages", status_code=201,
             tags=["Notaire — Messagerie"])
async def envoyer_message(
    payload: MessageIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE)),
):
    """Envoie un message dans un fil ou en direct.

    Triggers automatiques :
    - fn_verifier_notaire_agree_message : bloque si expéditeur non agréé
    - fn_message_sha256_chain : calcule SHA-256 + sha256_prev chaîné
    """
    if not payload.fil_id and not payload.destinataire_id:
        raise HTTPException(422,
            "fil_id ou destinataire_id obligatoire")

    PRIORITES = {"normale","urgente","confidentielle"}
    if payload.priorite not in PRIORITES:
        raise HTTPException(422, f"priorite : {PRIORITES}")

    # Récupérer notaire expéditeur
    r = await db.execute(
        select(NotaryRegistry).where(
            and_(NotaryRegistry.user_id == current_user.id,
                 NotaryRegistry.statut_agrement == "agree")
        )
    )
    notaire = r.scalar_one_or_none()
    if not notaire:
        raise HTTPException(403, "Notaire agréé requis")

    msg_id = str(uuid.uuid4())
    try:
        # Le trigger fn_message_sha256_chain calcule le SHA256 automatiquement
        await db.execute(text("""
            INSERT INTO message_notarial
                (id, fil_id, expediteur_id, destinataire_id,
                 sujet, corps, priorite, statut,
                 acte_ref_id, sha256_message, envoye_at)
            VALUES
                (:id, :fid, :exp, :dest,
                 :sujet, :corps, :priorite, 'envoye',
                 :acte, 'pending', NOW())
        """), {
            "id":      msg_id,
            "fid":     payload.fil_id,
            "exp":     str(notaire.id),
            "dest":    payload.destinataire_id,
            "sujet":   payload.sujet,
            "corps":   payload.corps,
            "priorite": payload.priorite,
            "acte":    payload.acte_ref_id,
        })
        # Mettre à jour updated_at du fil
        if payload.fil_id:
            await db.execute(text(
                "UPDATE fil_discussion SET updated_at=NOW() WHERE id=:fid"
            ), {"fid": payload.fil_id})
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "MESSAGE REFUSÉ" in str(e):
            raise HTTPException(403, str(e)[:300])
        raise HTTPException(400, str(e)[:200])

    # Récupérer le SHA-256 calculé par le trigger
    try:
        r_sha = await db.execute(text(
            "SELECT sha256_message FROM message_notarial WHERE id=:id"
        ), {"id": msg_id})
        sha = r_sha.scalar() or "pending"
    except Exception:
        sha = "pending"

    return {
        "id": msg_id,
        "fil_id": payload.fil_id,
        "priorite": payload.priorite,
        "sha256_message": sha,
        "statut": "envoye",
    }


@router.post("/messagerie/messages-directs", status_code=201,
             tags=["Notaire — Messagerie"])
async def envoyer_message_direct(
    payload: MessageDirectIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE)),
):
    """Message direct entre deux notaires (sans fil de discussion).
    Crée automatiquement un fil privé si premier échange.
    """
    r = await db.execute(
        select(NotaryRegistry).where(
            and_(NotaryRegistry.user_id == current_user.id,
                 NotaryRegistry.statut_agrement == "agree")
        )
    )
    notaire = r.scalar_one_or_none()
    if not notaire:
        raise HTTPException(403, "Notaire agréé requis")

    # Vérifier destinataire agréé
    r_dest = await db.execute(
        select(NotaryRegistry).where(
            and_(NotaryRegistry.id == payload.destinataire_notaire_id,
                 NotaryRegistry.statut_agrement == "agree")
        )
    )
    destinataire = r_dest.scalar_one_or_none()
    if not destinataire:
        raise HTTPException(404,
            "Destinataire introuvable ou non agréé")

    # Chercher un fil direct existant entre les deux
    fil_id = None
    try:
        r_fil = await db.execute(text("""
            SELECT fd.id FROM fil_discussion fd
            JOIN participant_fil p1 ON p1.fil_id = fd.id
                AND p1.notaire_id = :exp AND p1.actif = true
            JOIN participant_fil p2 ON p2.fil_id = fd.id
                AND p2.notaire_id = :dest AND p2.actif = true
            WHERE fd.type_fil = 'general'
              AND fd.chambre_notariale IS NULL
            ORDER BY fd.created_at DESC LIMIT 1
        """), {"exp": str(notaire.id), "dest": payload.destinataire_notaire_id})
        row = r_fil.first()
        if row:
            fil_id = str(row[0])
    except Exception:
        await db.rollback()
        pass

    # Créer le fil si inexistant
    if not fil_id:
        fil_id = str(uuid.uuid4())
        await db.execute(text("""
            INSERT INTO fil_discussion
                (id, sujet, type_fil, cree_par, created_at)
            VALUES
                (:id, :sujet, 'general', :uid, NOW())
        """), {"id": fil_id, "sujet": payload.sujet,
               "uid": str(current_user.id)})
        for nid in [str(notaire.id), payload.destinataire_notaire_id]:
            await db.execute(text("""
                INSERT INTO participant_fil
                    (id,fil_id,notaire_id,role_participant,joined_at)
                VALUES
                    (gen_random_uuid(),:fid,:nid,'membre',NOW())
                ON CONFLICT (fil_id,notaire_id) DO NOTHING
            """), {"fid": fil_id, "nid": nid})

    # Envoyer le message
    msg_id = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO message_notarial
            (id,fil_id,expediteur_id,destinataire_id,
             sujet,corps,priorite,statut,
             acte_ref_id,sha256_message,envoye_at)
        VALUES
            (:id,:fid,:exp,:dest,
             :sujet,:corps,:priorite,'envoye',
             :acte,'pending',NOW())
    """), {
        "id": msg_id, "fid": fil_id,
        "exp": str(notaire.id),
        "dest": payload.destinataire_notaire_id,
        "sujet": payload.sujet, "corps": payload.corps,
        "priorite": payload.priorite,
        "acte": payload.acte_ref_id,
    })
    await db.execute(text(
        "UPDATE fil_discussion SET updated_at=NOW() WHERE id=:fid"
    ), {"fid": fil_id})
    await db.commit()

    return {
        "message_id": msg_id,
        "fil_id": fil_id,
        "destinataire": destinataire.nom_notaire,
        "priorite": payload.priorite,
        "statut": "envoye",
    }


@router.get("/messagerie/fils/{fil_id}/messages",
            tags=["Notaire — Messagerie"])
async def lister_messages_fil(
    fil_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE + ["AUDITEUR"])),
):
    """Liste les messages d'un fil, du plus récent au plus ancien."""
    r_not = await db.execute(
        select(NotaryRegistry).where(NotaryRegistry.user_id == current_user.id)
    )
    notaire = r_not.scalar_one_or_none()
    if not notaire:
        raise HTTPException(403, "Notaire agréé requis")

    # Vérifier accès au fil
    try:
        r_acc = await db.execute(text("""
            SELECT 1 FROM fil_discussion fd
            LEFT JOIN participant_fil pf ON pf.fil_id=fd.id
                AND pf.notaire_id=:nid AND pf.actif=true
            WHERE fd.id=:fid
              AND (fd.chambre_notariale IS NULL
                   OR fd.chambre_notariale=:chambre
                   OR pf.notaire_id IS NOT NULL)
        """), {
            "fid": fil_id, "nid": str(notaire.id),
            "chambre": notaire.chambre_notariale or "",
        })
        if not r_acc.first():
            raise HTTPException(403, "Accès refusé à ce fil")
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        pass

    try:
        r = await db.execute(text("""
            SELECT mn.id, mn.sujet, mn.corps,
                   mn.priorite, mn.statut,
                   mn.sha256_message, mn.sha256_prev,
                   mn.envoye_at, mn.acte_ref_id,
                   nr.nom_notaire AS expediteur_nom,
                   (SELECT COUNT(*) FROM piece_jointe_message pj
                    WHERE pj.message_id = mn.id) AS nb_pj,
                   (SELECT lu_at FROM accuse_lecture al
                    WHERE al.message_id = mn.id
                      AND al.notaire_id = :nid LIMIT 1) AS mon_accusé
            FROM message_notarial mn
            JOIN notary_registry nr ON nr.id = mn.expediteur_id
            WHERE mn.fil_id = :fid
              AND mn.statut NOT IN ('supprime_expediteur')
            ORDER BY mn.envoye_at DESC
            LIMIT :l OFFSET :o
        """), {
            "fid": fil_id, "nid": str(notaire.id),
            "l": limit, "o": (page-1)*limit,
        })
        messages = [dict(row) for row in r.mappings().all()]

        # Marquer automatiquement les messages non lus comme lus
        for msg in messages:
            if not msg.get("mon_accusé") and str(msg.get("expediteur_nom","")) != notaire.nom_notaire:
                try:
                    await db.execute(text("""
                        INSERT INTO accuse_lecture (id,message_id,notaire_id,lu_at)
                        VALUES (gen_random_uuid(),:mid,:nid,NOW())
                        ON CONFLICT (message_id,notaire_id) DO NOTHING
                    """), {"mid": str(msg["id"]), "nid": str(notaire.id)})
                except Exception:
                    pass
        await db.commit()
        return messages
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.delete("/messagerie/messages/{msg_id}",
               tags=["Notaire — Messagerie"])
async def archiver_message(
    msg_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE)),
):
    """Archive un message (pas de DELETE physique — règle FONCIER+)."""
    r = await db.execute(
        select(NotaryRegistry).where(NotaryRegistry.user_id == current_user.id)
    )
    notaire = r.scalar_one_or_none()
    if not notaire:
        raise HTTPException(403, "Notaire agréé requis")
    try:
        await db.execute(text("""
            UPDATE message_notarial
            SET statut = 'supprime_expediteur'
            WHERE id = :id AND expediteur_id = :nid
        """), {"id": msg_id, "nid": str(notaire.id)})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"id": msg_id, "statut": "supprime_expediteur",
            "message": "Message archivé (pas de suppression physique)"}


@router.get("/messagerie/boite-reception",
            tags=["Notaire — Messagerie"])
async def boite_reception(
    non_lus_seulement: bool = Query(default=False),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_NOTAIRE)),
):
    """Boîte de réception du notaire connecté :
    messages directs + messages de fils auxquels il participe.
    """
    r = await db.execute(
        select(NotaryRegistry).where(NotaryRegistry.user_id == current_user.id)
    )
    notaire = r.scalar_one_or_none()
    if not notaire:
        raise HTTPException(403, "Notaire agréé requis")

    try:
        where_nl = (
            "AND al.id IS NULL"
            if non_lus_seulement else ""
        )
        r = await db.execute(text(f"""
            SELECT mn.id, mn.sujet, mn.corps, mn.priorite,
                   mn.statut, mn.envoye_at, mn.fil_id,
                   nr.nom_notaire AS expediteur_nom,
                   al.lu_at
            FROM message_notarial mn
            JOIN notary_registry nr ON nr.id = mn.expediteur_id
            LEFT JOIN accuse_lecture al ON al.message_id = mn.id
                AND al.notaire_id = :nid
            WHERE mn.expediteur_id != :nid
              AND mn.statut NOT IN ('supprime_expediteur')
              AND (
                mn.destinataire_id = :nid
                OR mn.fil_id IN (
                    SELECT fil_id FROM participant_fil
                    WHERE notaire_id=:nid AND actif=true
                )
              )
              {where_nl}
            ORDER BY mn.envoye_at DESC
            LIMIT :l OFFSET :o
        """), {
            "nid": str(notaire.id),
            "l": limit, "o": (page-1)*limit,
        })
        messages = [dict(row) for row in r.mappings().all()]

        # Compteur non lus
        r_cnt = await db.execute(text("""
            SELECT COUNT(*) FROM message_notarial mn
            LEFT JOIN accuse_lecture al ON al.message_id=mn.id
                AND al.notaire_id=:nid
            WHERE mn.expediteur_id != :nid
              AND mn.statut = 'envoye'
              AND al.id IS NULL
              AND (mn.destinataire_id=:nid
                OR mn.fil_id IN (
                    SELECT fil_id FROM participant_fil
                    WHERE notaire_id=:nid AND actif=true
                ))
        """), {"nid": str(notaire.id)})
        nb_non_lus = r_cnt.scalar() or 0

        return {
            "nb_non_lus": nb_non_lus,
            "messages": messages,
        }
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


# ══════════════════════════════════════════════════════════════════
# SOUS-MODULE DAR — NOTAIRE
# Direction des Archives — liaison automatique vers ANNF
# ══════════════════════════════════════════════════════════════════

@router.get("/dar/archives", response_model=list)
async def lister_archives_dar_notaire(
    type_document: Optional[str] = Query(None,
        description="acte_notarie|acte_vente|acte_donation|succession"),
    scelle: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'NOTAIRE', 'AUDITEUR'] + [
        "ARCHIVISTE_ANNF","RESPONSABLE_ANNF","AUDITEUR"
    ])),
):
    """Archives du module Notaire (sous-module DAR).
    Retourne les archives liées à ce module depuis annf_archive_links.
    Filtrables par type de document et statut de scellement.
    """
    where = "WHERE entite_table LIKE :mod"
    params: dict = {
        "mod": f"%notaire%",
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
async def archiver_vers_annf_notaire(
    type_document: str = Query(...,
        description="acte_notarie|acte_vente|acte_donation|succession"),
    entite_id: str = Query(..., description="UUID de l'entité à archiver"),
    entite_table: str = Query(..., description="Nom de la table source"),
    snapshot: Optional[dict] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'NOTAIRE', 'ARCHIVISTE_ANNF'])),
):
    """Archive un document du module Notaire vers l'ANNF national.
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
        "module_source": "notaire",
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
        "module": "notaire",
        "sous_module": "DAR",
        "type_archive": type_document,
        "scelle": False,
        "message": "Archivé vers ANNF — scellement via POST /annf/{ida}/sceller",
    }


@router.get("/dar/integrite", response_model=list)
async def verifier_integrite_dar_notaire(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'NOTAIRE', 'AUDITEUR'] + ["AUDITEUR"])),
):
    """Vérifie l'intégrité des archives DAR du module Notaire.
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
        """), {"mod": f"%notaire%"})
        row = r.mappings().first()
        return dict(row) if row else {"nb_scelles":0,"nb_en_attente":0,"nb_total":0}
    except Exception:
        return {"nb_scelles":0,"nb_en_attente":0,"nb_total":0}


# ══════════════════════════════════════════════════════════════════
# VÉRIFICATION CCFM OBLIGATOIRE — Module NOTAIRE
# Conforme à l'obligation réglementaire DNCD :
# tout acte foncier exige un certificat CCFM signé sur la parcelle.
# ══════════════════════════════════════════════════════════════════

@router.get("/verifier-ccfm/{parcelle_id}", response_model=dict)
async def verifier_ccfm_notaire(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Vérifie le certificat CCFM d'une parcelle depuis le module NOTAIRE.
    Obligatoire avant toute action transactionnelle sur la parcelle.

    Retourne : valide, nus, statut, sha256_verif, qr_code_url,
               demandeur_nom, nicad, surface_m2, message, parcelle_gele
    """
    from app.services.ccfm_verification import verifier_ccfm as _vcfm
    return await _vcfm(db=db, parcelle_id=parcelle_id)


@router.post("/verifier-ccfm-qr")
async def verifier_ccfm_qr_notaire(
    qr_code: str = Query(...,
        description="Données QR CODE scannées depuis le certificat CCFM"),
    parcelle_id: Optional[str] = Query(None,
        description="UUID parcelle optionnel — cross-check QR vs parcelle"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Vérification CCFM par QR CODE depuis le module NOTAIRE.
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
async def ccfm_gate_notaire(
    parcelle_id: str,
    action: str = Query("operation",
        description="Nom de l'action : vente, hypotheque, litige, regularisation…"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","NOTAIRE"])),
):
    """Gate CCFM — Bloque si CCFM absent, non signé, contesté ou parcelle gelée.
    À appeler avant tout acte transactionnel en module NOTAIRE.
    """
    from app.services.ccfm_verification import exiger_ccfm_valide
    ok, message = await exiger_ccfm_valide(
        db=db, parcelle_id=parcelle_id, contexte=f"NOTAIRE/{action}"
    )
    if not ok:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CCFM_GATE_FAILED",
                "message": message,
                "parcelle_id": parcelle_id,
                "action": action,
                "module": "NOTAIRE",
                "correction": "Émettre et signer un CCFM via POST /v1/ccfm/demandes",
            }
        )
    return {
        "gate_ok": True,
        "parcelle_id": parcelle_id,
        "message": message,
        "module": "NOTAIRE",
        "action": action,
    }
