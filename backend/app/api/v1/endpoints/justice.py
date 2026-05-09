from app.services.workflow_orchestrator import ANNFArchiveService
"""
FONCIER+ — Module Justice COMPLET
Module indépendant — autorité judiciaire foncière nationale.

Tables couvertes :
  parcel_disputes (005)          — litiges sur droits de propriété
  parcel_freeze_events (004)     — historique gel/dégel complet
  justice_rnaf_decisions (004)   — décisions impactant les RNAF
  greffe_judiciaire (010)        — 9 TGI + Cour d'appel seedés
  objet_annule (010)             — objets frappés d'annulation (polymorphique)
  historique_annulation (010)    — trace complète avant/après
  signature_judiciaire (010)     — signatures PKI des jugements
  huissier_agree (020)           — huissiers titulaires de charge

Workflows portés :
  WF25 — Mise sous litige avec gel (4 étapes : dépôt→enregistrement→blocage→notification)
  WF26 — Levée de litige (3 étapes : décision→vérification→levée blocage)

Fonctions PL/pgSQL clés :
  verifier_authenticite_jugement()  — authentifie un jugement via greffe + SHA-256
  executer_annulation_complete()    — orchestre F_AJ1→F_AJ7 (annulation, restauration,
                                       CCFM invalidé, ANNF archivage)

Triggers :
  tg_conflit_degel_automatique  — dégel automatique si dernier litige résolu (018)
  tg_block_transfer_if_litige   — bloque les transferts si litige ouvert (006)
  tg_wl8_cascade_annulation     — cascade annulation sur droits/transferts (008)
"""
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user
from app.models.droits_fonciers import (
    ParcelDispute, TypeLitigeDroit, StatutLitigeDroit, AutoriteSaisie,
)
from app.models.workflows import ParcelFreezeEvent
from app.models.parcellaire import Parcelle, StatutParcelle

router = APIRouter(prefix="/justice", tags=["Justice — Litiges & Annulations"])

ROLES_JUSTICE = ["ADMIN", "JUGE_FONCIER", "GREFFIER", "HUISSIER"]
ROLES_JUGE    = ["ADMIN", "JUGE_FONCIER", "GREFFIER", "HUISSIER"]
ROLES_READ    = ["ADMIN", "JUGE_FONCIER", "GREFFIER", "HUISSIER",
                 "NOTAIRE", "AUDITEUR", "DIRECTEUR_CADASTRE",
                 "BANQ_DIRECTEUR", "CHEF_CCFM"]


def _sha(*parts) -> str:
    return hashlib.sha256(
        "|".join(str(p) for p in parts).encode()
    ).hexdigest()


# ══════════════════════════════════════════════════════════════════
# SCHÉMAS
# ══════════════════════════════════════════════════════════════════

class LitigeIn(BaseModel):
    parcelle_id: str
    rnp_parcelle_id: str
    type_litige: str = Field(...,
        description="limite|propriete|heritage|fraude|occupation")
    autorite_saisie: str = Field(...,
        description="TGI|PREFET|MAIRIE")
    description: str = Field(..., min_length=20)
    plaignant_id: Optional[str] = None
    defendeur_id: Optional[str] = None
    greffe_id: str = Field(...,
        description="UUID greffe_judiciaire")


class ResolutionIn(BaseModel):
    decision_reference: str
    num_jugement: str = Field(..., min_length=3)
    greffe_id: str
    motif_resolution: Optional[str] = None


class AnnulationJudiciaireIn(BaseModel):
    parcelle_id: str
    decision_id: str = Field(...,
        description="UUID decisions_judiciaires existante")
    num_jugement: str
    greffe_id: str
    motif: str = Field(..., min_length=10)
    type_annulation: str = Field(default="annulation_totale",
        description="annulation_totale|annulation_partielle|rectification|"
                    "suspension|retablissement")
    type_objet: str = Field(default="droit_foncier",
        description="mutation|rnaf|rnp|lotissement|hypotheque|ccfm|"
                    "acte_notarie|droit_foncier|transfert")
    reference_objet: Optional[str] = Field(None,
        description="UUID de l'objet dans sa table native")


class JusticeRNAFDecisionIn(BaseModel):
    rnaf_workflow_id: str
    decision_judiciaire_id: str
    numero_decision: str
    motif_decision: str = Field(..., min_length=10)
    retablit_rnaf: bool = False
    annule_rnaf: bool = False
    degele_parcelles: bool = False


class SignatureJudiciaire(BaseModel):
    decision_id: str
    greffe_id: str
    reference_jugement: str
    hash_document: str = Field(...,
        description="SHA-256 du document de jugement PDF")


class HuissierIn(BaseModel):
    user_id: Optional[str] = None
    numero_charge: str = Field(..., min_length=3)
    nom_huissier: str = Field(..., min_length=3)
    tgi_rattachement: str = Field(...,
        description="UUID greffe_judiciaire de rattachement")
    date_prestation_serment: str = Field(..., description="YYYY-MM-DD")
    adresse_etude: Optional[str] = None


# ══════════════════════════════════════════════════════════════════
# TABLEAU DE BORD JUSTICE
# ══════════════════════════════════════════════════════════════════

@router.get("/tableau-de-bord", response_model=list)
async def tableau_de_bord_justice(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_JUSTICE + ["AUDITEUR"])),
):
    """Vue consolidée du module Justice."""
    stats: dict = {}
    queries = {
        "litiges_ouverts":
            "SELECT COUNT(*) FROM parcel_disputes WHERE statut='ouvert'",
        "annulations_en_cours":
            "SELECT COUNT(*) FROM objet_annule WHERE statut_annulation='en_cours'",
        "parcelles_gelees":
            "SELECT COUNT(*) FROM parcelles WHERE is_gele=true",
        "decisions_rnaf_en_instruction":
            "SELECT COUNT(*) FROM justice_rnaf_decisions "
            "WHERE statut_decision='en_instruction'",
        "signatures_ce_mois":
            "SELECT COUNT(*) FROM signature_judiciaire "
            "WHERE created_at >= date_trunc('month',NOW())",
        "greffes_actifs":
            "SELECT COUNT(*) FROM greffe_judiciaire WHERE statut='actif'",
    }
    for key, sql in queries.items():
        try:
            r = await db.execute(text(sql))
            stats[key] = r.scalar() or 0
        except Exception:
            stats[key] = 0

    # Litiges récents
    litiges_recents = []
    try:
        r = await db.execute(text("""
            SELECT pd.id, pd.type_litige, pd.statut, pd.date_ouverture,
                   p.nicad
            FROM parcel_disputes pd
            LEFT JOIN parcelles p ON p.id = pd.parcelle_id
            WHERE pd.statut = 'ouvert'
            ORDER BY pd.date_ouverture DESC LIMIT 5
        """))
        litiges_recents = [dict(row) for row in r.mappings().all()]
    except Exception:
        pass

    return {
        "acteur": current_user.email,
        "role": current_user.role,
        "module": "Justice",
        "stats": stats,
        "litiges_recents": litiges_recents,
        "horodatage": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════
# GREFFES JUDICIAIRES
# ══════════════════════════════════════════════════════════════════

@router.get("/greffes", response_model=list)
async def lister_greffes(
    statut: str = Query(default="actif"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Liste les greffes judiciaires (9 TGI + Cour d'appel de Niamey)."""
    try:
        r = await db.execute(text("""
            SELECT id, nom_greffe, tribunal_nom, tribunal_code,
                   juridiction, statut, contact, adresse
            FROM greffe_judiciaire WHERE statut=:s
            ORDER BY tribunal_nom
        """), {"s": statut})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        await db.rollback()
        raise HTTPException(500, str(e)[:200])


@router.get("/greffes/{greffe_id}", response_model=dict)
async def get_greffe(
    greffe_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Détail d'un greffe avec ses huissiers rattachés et statistiques."""
    try:
        r = await db.execute(text(
            "SELECT * FROM greffe_judiciaire WHERE id=:id"
        ), {"id": greffe_id})
        row = r.mappings().first()
        if not row:
            raise HTTPException(404, "Greffe introuvable")
        result = dict(row)

        # Huissiers rattachés
        try:
            r_h = await db.execute(text("""
                SELECT nom_huissier, numero_charge, statut
                FROM huissier_agree
                WHERE tgi_rattachement=:gid AND statut='actif'
                ORDER BY nom_huissier
            """), {"gid": greffe_id})
            result["huissiers"] = [dict(h) for h in r_h.mappings().all()]
        except Exception:
            result["huissiers"] = []

        # Stats décisions
        try:
            r_s = await db.execute(text("""
                SELECT COUNT(*) nb_decisions
                FROM decisions_judiciaires WHERE greffe_id=:gid
            """), {"gid": greffe_id})
            result["nb_decisions"] = r_s.scalar() or 0
        except Exception:
            result["nb_decisions"] = 0

        return result
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(500, str(e)[:200])


# ══════════════════════════════════════════════════════════════════
# HUISSIERS AGRÉÉS (migration 020)
# ══════════════════════════════════════════════════════════════════

@router.get("/huissiers", response_model=list)
async def lister_huissiers(
    tgi_id: Optional[str] = Query(None),
    statut: str = Query(default="actif"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Liste des huissiers de justice agréés."""
    where = "WHERE statut=:s"
    params: dict = {"s": statut}
    if tgi_id:
        where += " AND tgi_rattachement=:tgi"; params["tgi"] = tgi_id
    try:
        r = await db.execute(text(
            f"SELECT * FROM huissier_agree {where} ORDER BY nom_huissier"
        ), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        await db.rollback()
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


@router.post("/huissiers", status_code=201)
async def enregistrer_huissier(
    payload: HuissierIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_URBANISME","MINISTRE_URBANISME"]
    )),
):
    """Enregistre un huissier titulaire de charge (migration 020)."""
    try:
        await db.execute(text("""
            INSERT INTO huissier_agree
                (id, user_id, numero_charge, nom_huissier,
                 tgi_rattachement, date_prestation_serment,
                 adresse_etude, statut, created_at)
            VALUES
                (gen_random_uuid(),:uid,:num,:nom,
                 :tgi,:serment,:adr,'actif',NOW())
        """), {
            "uid": payload.user_id,
            "num": payload.numero_charge,
            "nom": payload.nom_huissier,
            "tgi": payload.tgi_rattachement,
            "serment": payload.date_prestation_serment,
            "adr": payload.adresse_etude,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "uq_huissier_charge" in str(e):
            raise HTTPException(409,
                f"Numéro de charge {payload.numero_charge} déjà utilisé")
        raise HTTPException(400, str(e)[:200])
    return {"numero_charge": payload.numero_charge,
            "nom_huissier": payload.nom_huissier, "statut": "actif"}


# ══════════════════════════════════════════════════════════════════
# WF25 — LITIGES FONCIERS
# ══════════════════════════════════════════════════════════════════

@router.post("/litiges", status_code=201)
async def ouvrir_litige(
    payload: LitigeIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_JUSTICE)),
):
    """WF25 étape 1 — Ouvre un litige foncier.

    Actions automatiques :
    - Gel de la parcelle (is_gele=true)
    - Enregistrement dans parcel_freeze_events
    - Transaction_block posé via trigger tg_block_transfer_if_litige
    """
    # Valider type + autorité
    try:
        type_l = TypeLitigeDroit(payload.type_litige)
        autorite = AutoriteSaisie(payload.autorite_saisie)
    except ValueError as e:
        raise HTTPException(422, str(e))

    # Vérifier greffe actif
    r_g = await db.execute(text(
        "SELECT id FROM greffe_judiciaire WHERE id=:gid AND statut='actif'"
    ), {"gid": payload.greffe_id})
    if not r_g.first():
        raise HTTPException(404,
            f"Greffe {payload.greffe_id} introuvable ou inactif")

    sha = _sha(payload.parcelle_id, payload.type_litige,
               payload.description[:50])

    litige = ParcelDispute(
        id=uuid.uuid4(),
        rnp_parcelle_id=payload.rnp_parcelle_id,
        parcelle_id=payload.parcelle_id,
        type_litige=type_l,
        autorite_saisie=autorite,
        statut=StatutLitigeDroit.OUVERT,
        date_ouverture=datetime.now(timezone.utc),
        description=payload.description,
        plaignant_id=payload.plaignant_id,
        defendeur_id=payload.defendeur_id,
        cree_par=current_user.id,
    )
    db.add(litige)

    # Gel parcelle
    await db.execute(
        update(Parcelle).where(Parcelle.id == payload.parcelle_id)
        .values(is_gele=True)
    )

    # Enregistrer l'événement de gel
    gel_event = ParcelFreezeEvent(
        id=uuid.uuid4(),
        parcelle_id=payload.parcelle_id,
        type_gel="justice",
        litige_id=None,  # Sera mis à jour après commit
        gele=True,
        motif=f"Litige judiciaire ouvert — {payload.type_litige}: {payload.description[:100]}",
        gele_par=current_user.id,
        gele_at=datetime.now(timezone.utc),
    )
    db.add(gel_event)
    await db.commit()

    # Numéro jugement officiel (séquence)
    r_num = await db.execute(text(
        "SELECT generer_numero_officiel('JUG','seq_jugement_foncier')"
    ))
    num_ref = r_num.scalar()

    return {
        "id": str(litige.id),
        "reference": num_ref,
        "statut": "ouvert",
        "type_litige": payload.type_litige,
        "parcelle_gelee": True,
        "greffe_id": payload.greffe_id,
        "message": "Litige ouvert — parcelle gelée — tg_block_transfer_if_litige activé",
    }


@router.get("/litiges", response_model=list)
async def lister_litiges(
    statut:      Optional[str] = Query(None),
    type_litige: Optional[str] = Query(None),
    page:  int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_JUSTICE + ["AUDITEUR"])),
):
    """Liste les litiges filtrés par juridiction de l acteur."""
    from app.core.scope_filter import ScopeFilter
    scope = ScopeFilter.from_user(current_user)
    role  = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", "")

    where  = "WHERE 1=1"
    params: dict = {"l": limit, "o": (page - 1) * limit}

    if statut:
        where += " AND l.statut::TEXT = :st"
        params["st"] = statut

    if type_litige:
        where += " AND l.type_litige::TEXT = :tl"
        params["tl"] = type_litige

    # Filtre juridictionnel
    if not scope.peut_voir_national:
        if role in ("JUGE_FONCIER", "GREFFIER"):
            # Filtre par TGI de l agent (stocké dans entite_id ou via agent_profil)
            tgi_id = scope.entite_id
            if tgi_id:
                where += " AND l.tgi_id = :_tgi"
                params["_tgi"] = tgi_id
            elif scope.region:
                where += " AND l.region = :_region"
                params["_region"] = scope.region
        elif role == "HUISSIER":
            # L huissier voit uniquement les litiges ou il intervient
            where += " AND l.huissier_id = :_uid"
            params["_uid"] = scope.user_id
        elif scope.region:
            where += " AND l.region = :_region"
            params["_region"] = scope.region

    try:
        r = await db.execute(text(f"""
            SELECT l.id, l.parcelle_id, l.type_litige::TEXT, l.statut::TEXT,
                   l.date_ouverture, l.demandeur, l.defendeur,
                   l.juge_id, l.tgi_id,
                   p.nicad, p.lot, p.parcelle, p.region
            FROM litiges l
            LEFT JOIN parcelles p ON p.id = l.parcelle_id
            {where}
            ORDER BY l.date_ouverture DESC
            LIMIT :l OFFSET :o
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        await db.rollback()
        import logging as _l
        _l.getLogger("justice").warning("lister_litiges: %s", e)
        return []


@router.get("/litiges/{litige_id}", response_model=dict)
async def get_litige(
    litige_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Détail d'un litige avec son historique de gel."""
    r = await db.execute(
        select(ParcelDispute).where(ParcelDispute.id == litige_id)
    )
    litige = r.scalar_one_or_none()
    if not litige:
        raise HTTPException(404, "Litige introuvable")

    result = {
        "id": str(litige.id),
        "type_litige": litige.type_litige,
        "statut": litige.statut,
        "autorite_saisie": litige.autorite_saisie,
        "description": litige.description,
        "date_ouverture": litige.date_ouverture.isoformat(),
        "date_cloture": litige.date_cloture.isoformat() if litige.date_cloture else None,
        "decision_reference": litige.decision_reference,
        "parcelle_id": str(litige.parcelle_id) if litige.parcelle_id else None,
    }

    # Historique des gels liés à la parcelle
    if litige.parcelle_id:
        try:
            r_gel = await db.execute(text("""
                SELECT type_gel, gele, motif, gele_at
                FROM parcel_freeze_events
                WHERE parcelle_id=:pid
                ORDER BY gele_at DESC LIMIT 10
            """), {"pid": str(litige.parcelle_id)})
            result["historique_gel"] = [dict(row) for row in r_gel.mappings().all()]
        except Exception:
            result["historique_gel"] = []

    return result


@router.get("/litiges/parcelle/{parcelle_id}", response_model=dict)
async def lister_litiges_parcelle(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Tous les litiges d'une parcelle (ouverts + clôturés)."""
    r = await db.execute(
        select(ParcelDispute)
        .where(ParcelDispute.parcelle_id == parcelle_id)
        .order_by(ParcelDispute.date_ouverture.desc())
    )
    return [{
        "id": str(l.id),
        "type_litige": l.type_litige,
        "statut": l.statut,
        "date_ouverture": l.date_ouverture.isoformat(),
        "description": l.description[:150],
        "decision_reference": l.decision_reference,
    } for l in r.scalars().all()]


@router.post("/litiges/{litige_id}/resoudre")
async def resoudre_litige(
    litige_id: str,
    payload: ResolutionIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_JUGE)),
):
    """WF26 — Résout un litige et déclenche le dégel automatique.

    Le trigger tg_conflit_degel_automatique (migration 018) gère
    le dégel si aucun autre litige actif sur la parcelle.
    """
    # Valider greffe
    r_g = await db.execute(text(
        "SELECT id FROM greffe_judiciaire WHERE id=:gid AND statut='actif'"
    ), {"gid": payload.greffe_id})
    if not r_g.first():
        raise HTTPException(404, f"Greffe {payload.greffe_id} introuvable ou inactif")

    r = await db.execute(
        select(ParcelDispute).where(ParcelDispute.id == litige_id)
    )
    litige = r.scalar_one_or_none()
    if not litige:
        raise HTTPException(404, "Litige introuvable")
    if litige.statut != StatutLitigeDroit.OUVERT:
        raise HTTPException(404,
            f"Statut '{litige.statut}' — seul OUVERT peut être résolu")

    litige.statut = StatutLitigeDroit.RESOLU
    litige.decision_reference = payload.decision_reference
    litige.date_cloture = datetime.now(timezone.utc)
    await db.flush()

    # Vérifier et dégeler si dernier litige
    r_autres = await db.execute(text("""
        SELECT COUNT(*) FROM parcel_disputes
        WHERE parcelle_id=:pid AND statut='ouvert' AND id!=:lid
    """), {"pid": str(litige.parcelle_id), "lid": litige_id})
    nb_autres = r_autres.scalar() or 0

    if nb_autres == 0 and litige.parcelle_id:
        await db.execute(
            update(Parcelle).where(Parcelle.id == litige.parcelle_id)
            .values(is_gele=False)
        )
        # Enregistrer l'événement de dégel
        db.add(ParcelFreezeEvent(
            id=uuid.uuid4(),
            parcelle_id=litige.parcelle_id,
            type_gel="justice",
            gele=False,
            motif=f"Litige résolu — {payload.decision_reference}",
            gele_par=current_user.id,
            gele_at=datetime.now(timezone.utc),
        ))

    await db.commit()
    return {
        "id": litige_id,
        "statut": "resolu",
        "decision_reference": payload.decision_reference,
        "parcelle_degelee": nb_autres == 0,
        "message": "tg_conflit_degel_automatique déclenché" if nb_autres == 0
                   else f"{nb_autres} autre(s) litige(s) actif(s) — gel maintenu",
    }


# ══════════════════════════════════════════════════════════════════
# GEL / DÉGEL JUDICIAIRE
# ══════════════════════════════════════════════════════════════════

@router.post("/gel/{parcelle_id}")
async def geler_parcelle_judiciaire(
    parcelle_id: str,
    motif: str = Query(..., min_length=10),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_JUGE + ["GREFFIER"])),
):
    """Gèle judiciaire d'une parcelle — tracé dans parcel_freeze_events."""
    await db.execute(
        update(Parcelle).where(Parcelle.id == parcelle_id).values(is_gele=True)
    )
    sha = _sha(parcelle_id, "GEL", motif[:50])
    db.add(ParcelFreezeEvent(
        id=uuid.uuid4(), parcelle_id=parcelle_id,
        type_gel="justice", gele=True, motif=motif,
        gele_par=current_user.id, gele_at=datetime.now(timezone.utc),
    ))
    await db.commit()
    return {"parcelle_id": parcelle_id, "gele": True, "sha": sha}


@router.post("/degeler/{parcelle_id}")
async def degeler_parcelle(
    parcelle_id: str,
    motif: str = Query(..., min_length=10),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_JUGE)),
):
    """Dégèle judiciaire d'une parcelle — tracé dans parcel_freeze_events."""
    # Vérifier absence de litiges actifs
    r = await db.execute(text("""
        SELECT COUNT(*) FROM parcel_disputes
        WHERE parcelle_id=:pid AND statut='ouvert'
    """), {"pid": parcelle_id})
    nb_litiges = r.scalar() or 0
    if nb_litiges > 0:
        raise HTTPException(400,
            f"Dégel impossible : {nb_litiges} litige(s) ouvert(s) sur cette parcelle. "
            "Résoudre tous les litiges avant le dégel.")

    now = datetime.now(timezone.utc)
    await db.execute(
        update(Parcelle).where(Parcelle.id == parcelle_id).values(is_gele=False)
    )
    db.add(ParcelFreezeEvent(
        id=uuid.uuid4(), parcelle_id=parcelle_id,
        type_gel="justice", gele=False, motif=motif,
        gele_par=current_user.id, gele_at=now,
        degele_par=current_user.id, degele_at=now,
    ))
    await db.commit()
    return {"parcelle_id": parcelle_id, "gele": False, "degele_at": now.isoformat()}


@router.get("/gel/historique/{parcelle_id}", response_model=dict)
async def historique_gel_parcelle(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Historique complet des gels/dégels d'une parcelle."""
    r = await db.execute(
        select(ParcelFreezeEvent)
        .where(ParcelFreezeEvent.parcelle_id == parcelle_id)
        .order_by(ParcelFreezeEvent.gele_at.desc())
    )
    return [{
        "id": str(e.id), "type_gel": e.type_gel,
        "gele": e.gele, "motif": e.motif,
        "gele_at": e.gele_at.isoformat(),
        "degele_at": e.degele_at.isoformat() if e.degele_at else None,
    } for e in r.scalars().all()]


# ══════════════════════════════════════════════════════════════════
# ANNULATIONS JUDICIAIRES (migration 010)
# ══════════════════════════════════════════════════════════════════

@router.post("/annulations", status_code=201)
async def prononcer_annulation_judiciaire(
    payload: AnnulationJudiciaireIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_JUGE)),
):
    """Prononce une annulation judiciaire d'un objet foncier.

    Pipeline via executer_annulation_complete() :
    F_AJ1 verifier_authenticite_jugement → SHA-256 greffe
    F_AJ2 bloquer_objet_judiciaire       → blocage immédiat
    F_AJ3 appliquer_annulation_objet     → statut 'annule' (jamais DELETE)
    F_AJ4 restaurer_proprietaire_precedent → droit précédent restauré
    F_AJ5 invalider_ccfm_apres_annulation → nouveau CCFM requis
    F_AJ6 archiver_annulation_annf        → ANNF archivage obligatoire
    F_AJ7 debloquer_objet_apres_decision  → dégel si décision favorable
    """
    TYPES_DECISION = {
        "annulation_totale","annulation_partielle","rectification",
        "suspension","retablissement","classement"
    }
    TYPES_OBJET = {
        "mutation","rnaf","rnp","lotissement","hypotheque",
        "ccfm","acte_notarie","droit_foncier","transfert"
    }
    if payload.type_annulation not in TYPES_DECISION:
        raise HTTPException(422, f"type_annulation : {TYPES_DECISION}")
    if payload.type_objet not in TYPES_OBJET:
        raise HTTPException(422, f"type_objet : {TYPES_OBJET}")

    sha = _sha(payload.decision_id, payload.num_jugement,
               payload.motif[:50])
    num_off = None

    try:
        # Appel à la fonction PL/pgSQL d'orchestration
        r = await db.execute(text("""
            SELECT executer_annulation_complete(
                :did::UUID,
                :ref_jugt,
                :greffe_code,
                :motif
            )
        """), {
            "did":        payload.decision_id,
            "ref_jugt":   payload.num_jugement,
            "greffe_code": payload.greffe_id,
            "motif":      payload.motif,
        })
        resultat = r.scalar()
    except Exception as e:
        err = str(e)
        # Fallback : enregistrement manuel si la fonction PL n'existe pas encore
        try:
            obj_id = str(uuid.uuid4())
            await db.execute(text("""
                INSERT INTO objet_annule
                    (id, decision_id, type_objet, reference_objet,
                     table_source, type_decision, motif,
                     sha256_annulation, annule_par, date_annulation)
                VALUES
                    (:id, :did, :tobj, :robj,
                     :tbl, :tdec, :motif,
                     :sha, :uid, NOW())
            """), {
                "id": obj_id, "did": payload.decision_id,
                "tobj": payload.type_objet,
                "robj": payload.reference_objet or payload.parcelle_id,
                "tbl": payload.type_objet + "s",
                "tdec": payload.type_annulation,
                "motif": payload.motif, "sha": sha,
                "uid": str(current_user.id),
            })

            # Annuler la parcelle si annulation_totale
            if payload.type_annulation == "annulation_totale":
                await db.execute(
                    update(Parcelle)
                    .where(Parcelle.id == payload.parcelle_id)
                    .values(statut=StatutParcelle.ANNULE, is_gele=True)
                )
            resultat = {"statut": "annule_manuel", "sha": sha}
        except Exception as e2:
            await db.rollback()
            raise HTTPException(400, f"Annulation échouée : {str(e2)[:300]}")

    # Numéro officiel
    try:
        r_num = await db.execute(text(
            "SELECT generer_numero_officiel('JUG','seq_jugement_foncier')"
        ))
        num_off = r_num.scalar()
    except Exception:
        num_off = f"JUG-{datetime.now().year}-MANUEL"

    await db.commit()
    return {
        "numero_jugement_officiel": num_off,
        "parcelle_id": payload.parcelle_id,
        "type_annulation": payload.type_annulation,
        "type_objet": payload.type_objet,
        "sha256": sha,
        "resultat": resultat,
    }


@router.get("/annulations/{decision_id}", response_model=dict)
async def get_annulation(
    decision_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Détail d'une annulation judiciaire avec historique complet."""
    try:
        r = await db.execute(text("""
            SELECT oa.*, ha.ancien_statut, ha.nouveau_statut,
                   ha.snapshot_avant, ha.sha256_avant, ha.date_modification
            FROM objet_annule oa
            LEFT JOIN historique_annulation ha ON ha.objet_annule_id = oa.id
            WHERE oa.decision_id = :did
            ORDER BY ha.date_modification
        """), {"did": decision_id})
        rows = [dict(row) for row in r.mappings().all()]
        if not rows:
            raise HTTPException(404,
                "Aucune annulation pour cette décision")
        return {"decision_id": decision_id, "objets_annules": rows}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(500, str(e)[:200])


# ══════════════════════════════════════════════════════════════════
# DÉCISIONS RNAF
# ══════════════════════════════════════════════════════════════════

@router.post("/decisions-rnaf", status_code=201)
async def creer_decision_rnaf(
    payload: JusticeRNAFDecisionIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_JUGE + ["GREFFIER"])),
):
    """Enregistre une décision judiciaire impactant un RNAF.

    Si retablit_rnaf=true → RNAF repasse à PUBLIE
    Si annule_rnaf=true   → RNAF passe à ANNULE
    Si degele_parcelles=true → dégel des parcelles liées
    """
    sha = _sha(payload.rnaf_workflow_id, payload.decision_judiciaire_id,
               payload.motif_decision[:50])
    try:
        await db.execute(text("""
            INSERT INTO justice_rnaf_decisions
                (id, decision_judiciaire_id, rnaf_workflow_id,
                 statut_decision, numero_decision, date_decision,
                 motif_decision, retablit_rnaf, annule_rnaf,
                 degele_parcelles, sha256_decision, president_id, created_at)
            VALUES
                (gen_random_uuid(), :did, :rwid,
                 'rendue', :num, NOW(),
                 :motif, :ret, :ann,
                 :deg, :sha, :uid, NOW())
        """), {
            "did": payload.decision_judiciaire_id,
            "rwid": payload.rnaf_workflow_id,
            "num": payload.numero_decision,
            "motif": payload.motif_decision,
            "ret": payload.retablit_rnaf,
            "ann": payload.annule_rnaf,
            "deg": payload.degele_parcelles,
            "sha": sha, "uid": str(current_user.id),
        })

        # Appliquer l'effet sur le RNAF
        if payload.annule_rnaf:
            await db.execute(text("""
                UPDATE rnaf_workflow SET statut='annule',updated_at=NOW()
                WHERE id=:rwid
            """), {"rwid": payload.rnaf_workflow_id})
        elif payload.retablit_rnaf:
            await db.execute(text("""
                UPDATE rnaf_workflow SET statut='publie',updated_at=NOW()
                WHERE id=:rwid
            """), {"rwid": payload.rnaf_workflow_id})

        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    return {
        "numero_decision": payload.numero_decision,
        "rnaf_workflow_id": payload.rnaf_workflow_id,
        "sha256": sha,
        "retablit_rnaf": payload.retablit_rnaf,
        "annule_rnaf": payload.annule_rnaf,
    }


@router.get("/decisions-rnaf", response_model=list)
async def lister_decisions_rnaf(
    statut: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Liste les décisions judiciaires sur les RNAF."""
    where = "WHERE 1=1"
    params: dict = {"l": limit, "o": (page-1)*limit}
    if statut:
        where += " AND statut_decision=:s"; params["s"] = statut
    try:
        r = await db.execute(text(
            f"SELECT * FROM justice_rnaf_decisions {where} "
            "ORDER BY created_at DESC LIMIT :l OFFSET :o"), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        await db.rollback()
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


# ══════════════════════════════════════════════════════════════════
# SIGNATURES JUDICIAIRES (PKI)
# ══════════════════════════════════════════════════════════════════

@router.post("/signatures", status_code=201)
async def signer_jugement(
    payload: SignatureJudiciaire,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_JUGE + ["GREFFIER"])),
):
    """Enregistre la signature PKI d'un jugement dans signature_judiciaire."""
    sha_doc = _sha(payload.decision_id, payload.reference_jugement,
                   payload.hash_document)
    try:
        await db.execute(text("""
            INSERT INTO signature_judiciaire
                (id, decision_id, greffe_id, signataire_id,
                 role_signataire, reference_jugement,
                 sha256_document, created_at)
            VALUES
                (gen_random_uuid(), :did, :gid, :uid,
                 :role, :ref, :sha, NOW())
            ON CONFLICT (decision_id) DO UPDATE
                SET sha256_document=EXCLUDED.sha256_document,
                    reference_jugement=EXCLUDED.reference_jugement
        """), {
            "did": payload.decision_id,
            "gid": payload.greffe_id,
            "uid": str(current_user.id),
            "role": current_user.role,
            "ref": payload.reference_jugement,
            "sha": sha_doc,
        })

        # Marquer la décision comme authentifiée
        await db.execute(text("""
            UPDATE decisions_judiciaires
            SET sha256_jugement=:sha,
                reference_jugement=:ref,
                authenticite_verifiee=true,
                verifiee_par=:uid
            WHERE id=:did
        """), {
            "sha": payload.hash_document,
            "ref": payload.reference_jugement,
            "uid": str(current_user.id),
            "did": payload.decision_id,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    return {
        "decision_id": payload.decision_id,
        "reference_jugement": payload.reference_jugement,
        "sha256_document": sha_doc,
        "authenticite_verifiee": True,
    }


@router.get("/signatures/{decision_id}", response_model=dict)
async def get_signature_jugement(
    decision_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Vérifie l'authenticité d'un jugement signé."""
    try:
        r = await db.execute(text("""
            SELECT sj.*, gj.tribunal_nom, gj.tribunal_code
            FROM signature_judiciaire sj
            JOIN greffe_judiciaire gj ON gj.id = sj.greffe_id
            WHERE sj.decision_id = :did
        """), {"did": decision_id})
        row = r.mappings().first()
        if not row:
            raise HTTPException(404, "Aucune signature pour cette décision")
        return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(500, str(e)[:200])


# ══════════════════════════════════════════════════════════════════
# STATISTIQUES
# ══════════════════════════════════════════════════════════════════

@router.get("/stats", response_model=list)
async def stats_module_justice(
    annee: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Statistiques du module Justice."""
    where_yr = ""
    params: dict = {}
    if annee:
        where_yr = "AND EXTRACT(YEAR FROM date_ouverture)=:yr"
        params["yr"] = annee

    stats: dict = {}
    for key, sql in [
        ("litiges_par_type",
         f"SELECT type_litige, COUNT(*) nb FROM parcel_disputes "
         f"WHERE 1=1 {where_yr} GROUP BY type_litige ORDER BY nb DESC"),
        ("litiges_par_statut",
         f"SELECT statut, COUNT(*) nb FROM parcel_disputes "
         f"WHERE 1=1 {where_yr} GROUP BY statut"),
        ("annulations_par_type",
         "SELECT type_decision, COUNT(*) nb FROM objet_annule "
         "GROUP BY type_decision ORDER BY nb DESC"),
        ("gels_par_source",
         "SELECT type_gel, COUNT(*) nb FROM parcel_freeze_events "
         "WHERE gele=true GROUP BY type_gel"),
    ]:
        try:
            r = await db.execute(text(sql), params)
            stats[key] = [dict(row) for row in r.mappings().all()]
        except Exception:
            stats[key] = []

    return {"annee_filtre": annee, "stats": stats}


# ══════════════════════════════════════════════════════════════════
# SOUS-MODULE DAR — JUSTICE
# Direction des Archives — liaison automatique vers ANNF
# ══════════════════════════════════════════════════════════════════

@router.get("/dar/archives", response_model=list)
async def lister_archives_dar_justice(
    type_document: Optional[str] = Query(None,
        description="decision_justice|jugement|annulation|levee_litige"),
    scelle: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'JUGE_FONCIER', 'GREFFIER', 'AUDITEUR'] + [
        "ARCHIVISTE_ANNF","RESPONSABLE_ANNF","AUDITEUR"
    ])),
):
    """Archives du module Justice (sous-module DAR).
    Retourne les archives liées à ce module depuis annf_archive_links.
    Filtrables par type de document et statut de scellement.
    """
    where = "WHERE entite_table LIKE :mod"
    params: dict = {
        "mod": f"%justice%",
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
        await db.rollback()
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


@router.post("/dar/archiver", status_code=201)
async def archiver_vers_annf_justice(
    type_document: str = Query(...,
        description="decision_justice|jugement|annulation|levee_litige"),
    entite_id: str = Query(..., description="UUID de l'entité à archiver"),
    entite_table: str = Query(..., description="Nom de la table source"),
    snapshot: Optional[dict] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'JUGE_FONCIER', 'ARCHIVISTE_ANNF'])),
):
    """Archive un document du module Justice vers l'ANNF national.
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
        "module_source": "justice",
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
        "module": "justice",
        "sous_module": "DAR",
        "type_archive": type_document,
        "scelle": False,
        "message": "Archivé vers ANNF — scellement via POST /annf/{ida}/sceller",
    }


@router.get("/dar/integrite", response_model=list)
async def verifier_integrite_dar_justice(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'JUGE_FONCIER', 'GREFFIER', 'AUDITEUR'] + ["AUDITEUR"])),
):
    """Vérifie l'intégrité des archives DAR du module Justice.
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
        """), {"mod": f"%justice%"})
        row = r.mappings().first()
        return dict(row) if row else {"nb_scelles":0,"nb_en_attente":0,"nb_total":0}
    except Exception:
        return {"nb_scelles":0,"nb_en_attente":0,"nb_total":0}


# ══════════════════════════════════════════════════════════════════
# VÉRIFICATION CCFM OBLIGATOIRE — Module JUSTICE
# Conforme à l'obligation réglementaire DNCD :
# tout acte foncier exige un certificat CCFM signé sur la parcelle.
# ══════════════════════════════════════════════════════════════════

@router.get("/verifier-ccfm/{parcelle_id}", response_model=dict)
async def verifier_ccfm_justice(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Vérifie le certificat CCFM d'une parcelle depuis le module JUSTICE.
    Obligatoire avant toute action transactionnelle sur la parcelle.

    Retourne : valide, nus, statut, sha256_verif, qr_code_url,
               demandeur_nom, nicad, surface_m2, message, parcelle_gele
    """
    from app.services.ccfm_verification import verifier_ccfm as _vcfm
    return await _vcfm(db=db, parcelle_id=parcelle_id)


@router.post("/verifier-ccfm-qr")
async def verifier_ccfm_qr_justice(
    qr_code: str = Query(...,
        description="Données QR CODE scannées depuis le certificat CCFM"),
    parcelle_id: Optional[str] = Query(None,
        description="UUID parcelle optionnel — cross-check QR vs parcelle"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Vérification CCFM par QR CODE depuis le module JUSTICE.
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
async def ccfm_gate_justice(
    parcelle_id: str,
    action: str = Query("operation",
        description="Nom de l'action : vente, hypotheque, litige, regularisation…"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","JUGE_FONCIER","GREFFIER","HUISSIER"])),
):
    """Gate CCFM — Bloque si CCFM absent, non signé, contesté ou parcelle gelée.
    À appeler avant tout acte transactionnel en module JUSTICE.
    """
    from app.services.ccfm_verification import exiger_ccfm_valide
    ok, message = await exiger_ccfm_valide(
        db=db, parcelle_id=parcelle_id, contexte=f"JUSTICE/{action}"
    )
    if not ok:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CCFM_GATE_FAILED",
                "message": message,
                "parcelle_id": parcelle_id,
                "action": action,
                "module": "JUSTICE",
                "correction": "Émettre et signer un CCFM via POST /v1/ccfm/demandes",
            }
        )
    return {
        "gate_ok": True,
        "parcelle_id": parcelle_id,
        "message": message,
        "module": "JUSTICE",
        "action": action,
    }