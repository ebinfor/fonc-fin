"""
FONCIER+ — Espaces de travail personnalisés
Routes /mon-espace pour chaque acteur secondaire :
  TOPOGRAPHE, HUISSIER, SECRETARIAT_CADASTRE, AGENT_COMMUNE
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.core.scope_filter import ScopeFilter
from app.core.database import get_db
from app.core.security import require_role, get_current_user

router = APIRouter(prefix="/espace", tags=["Espace de travail — Acteurs"])


class ConstaterTerrainIn(BaseModel):
    """Formulaire de constat terrain pour le TOPOGRAPHE."""
    parcelle_id: str
    date_constat: str    # ISO date
    coordonnees_gps: Optional[str] = None  # ex: "13.5137,2.1098"
    surface_mesuree_m2: float = Field(..., gt=0)
    ecart_surface_pct: Optional[float] = None
    observations: str = Field(..., min_length=10)
    materiaux_reperes: Optional[str] = None  # bornes posées ?


class SuiviDemandeCommuneIn(BaseModel):
    """Mettre à jour le suivi d'un dépôt foncier communal."""
    numero_recepisse: str           = Field(..., min_length=3,
        description="Numéro de récépissé REC-AAAA-NNNNN")
    statut_suivi:     str           = Field(...,
        pattern="^(recu|en_instruction|transmis|complete|rejete|clos)$")
    note:             Optional[str] = Field(None, min_length=5)
    transmis_a:       Optional[str] = Field(None, min_length=2,
        description="Service destinataire")


async def espace_topographe(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","TOPOGRAPHE","GEOMETRE"])),
):
    """Tableau de bord TOPOGRAPHE : levés en attente, constats récents."""
    try:
        r = await db.execute(text("""
            SELECT p.id, p.nicad, p.statut, p.surface_m2
            FROM parcelles p
            WHERE p.statut = 'EN_ATTENTE_VALIDATION'
            ORDER BY p.created_at DESC
            LIMIT 20
        """))
        en_attente = [dict(row) for row in r.mappings().all()]
    except Exception:
        en_attente = []
    try:
        r2 = await db.execute(text("""
            SELECT * FROM constats_terrain
            WHERE topographe_id = :uid
            ORDER BY date_constat DESC LIMIT 10
        """), {"uid": str(current_user.id)})
        recents = [dict(row) for row in r2.mappings().all()]
    except Exception:
        recents = []
    return {
        "acteur": current_user.email,
        "role": "TOPOGRAPHE",
        "parcelles_en_attente_constat": len(en_attente),
        "constats_recents": recents,
        "file_attente": en_attente,
    }


@router.post("/topographe/constat", status_code=201)
async def enregistrer_constat_terrain(
    payload: ConstaterTerrainIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","TOPOGRAPHE","GEOMETRE"])),
):
    """Enregistre un constat terrain par le topographe."""
    import uuid
    constat_id = str(uuid.uuid4())
    try:
        await db.execute(text("""
            INSERT INTO constats_terrain (
                id, parcelle_id, topographe_id, date_constat,
                coordonnees_gps, surface_mesuree_m2, ecart_surface_pct,
                observations, materiaux_reperes, created_at
            ) VALUES (
                :id, :pid, :uid, :date,
                :gps, :surf, :ecart,
                :obs, :mat, NOW()
            )
        """), {
            "id": constat_id, "pid": payload.parcelle_id,
            "uid": str(current_user.id),
            "date": payload.date_constat, "gps": payload.coordonnees_gps,
            "surf": payload.surface_mesuree_m2,
            "ecart": payload.ecart_surface_pct,
            "obs": payload.observations, "mat": payload.materiaux_reperes,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, f"Erreur constat : {str(e)[:200]}")
    return {"id": constat_id, "parcelle_id": payload.parcelle_id,
            "surface_mesuree_m2": payload.surface_mesuree_m2}


# ─── HUISSIER — Espace significations ─────────────────────────────

@router.get("/huissier", response_model=list)
async def espace_huissier(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","HUISSIER"])),
):
    """Tableau de bord HUISSIER : litiges actifs, significations à effectuer."""
    try:
        r = await db.execute(text("""
            SELECT pd.id, pd.type_litige, pd.statut,
                   pd.date_ouverture, pd.description
            FROM parcel_disputes pd
            WHERE pd.statut = 'ouvert'
            ORDER BY pd.date_ouverture ASC
            LIMIT 20
        """))
        litiges = [dict(row) for row in r.mappings().all()]
    except Exception:
        litiges = []
    try:
        r2 = await db.execute(text("""
            SELECT * FROM recepisse_depot
            WHERE guichet_depot_id = :uid
            ORDER BY date_depot DESC LIMIT 10
        """), {"uid": str(current_user.id)})
        recepisse_recents = [dict(row) for row in r2.mappings().all()]
    except Exception:
        recepisse_recents = []
    return {
        "acteur": current_user.email,
        "role": "HUISSIER",
        "litiges_actifs": len(litiges),
        "litiges": litiges,
        "recepisse_recents": recepisse_recents,
    }


@router.get("/huissier/litiges/{parcelle_id}", response_model=dict)
async def litiges_parcelle_huissier(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","HUISSIER","JUGE_FONCIER","GREFFIER"])),
):
    """Consultation des litiges d'une parcelle par l'huissier."""
    try:
        r = await db.execute(text("""
            SELECT * FROM parcel_disputes
            WHERE parcelle_id = :pid
            ORDER BY date_ouverture DESC
        """), {"pid": parcelle_id})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


# ─── AGENT_COMMUNE — Suivi des dépôts ─────────────────────────────

@router.get("/commune", response_model=list)
async def espace_agent_commune(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN","ADMIN_COMMUNE","MAIRE","AGENT_COMMUNE"
    ])),
):
    """Espace de travail commune : dépôts du jour, historique."""
    today_depots, historique = [], []
    try:
        r = await db.execute(text("""
            SELECT * FROM recepisse_depot
            WHERE date_depot::date = CURRENT_DATE
            ORDER BY date_depot DESC
        """))
        today_depots = [dict(row) for row in r.mappings().all()]
    except Exception:
        pass
    try:
        r2 = await db.execute(text("""
            SELECT * FROM recepisse_depot
            WHERE guichet_depot_id = :uid
            ORDER BY date_depot DESC LIMIT 20
        """), {"uid": str(current_user.id)})
        historique = [dict(row) for row in r2.mappings().all()]
    except Exception:
        pass
    return {
        "acteur": current_user.email,
        "depots_aujourd_hui": len(today_depots),
        "depots_du_jour": today_depots,
        "mes_derniers_depots": historique,
    }


@router.patch("/commune/suivi/{numero_recepisse}")
async def mettre_a_jour_suivi(
    numero_recepisse: str,
    payload: SuiviDemandeCommuneIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN","ADMIN_COMMUNE","MAIRE","AGENT_COMMUNE"
    ])),
):
    """Met à jour le statut de suivi d'une demande déposée en commune."""
    if payload.statut_suivi not in ("recu","en_instruction","transmis","clos"):
        raise HTTPException(422, "statut_suivi invalide")
    try:
        await db.execute(text("""
            UPDATE recepisse_depot
            SET statut_suivi = :statut, note_suivi = :note,
                transmis_a = :ta, updated_at = NOW()
            WHERE numero_recepisse = :num
        """), {
            "statut": payload.statut_suivi, "note": payload.note,
            "ta": payload.transmis_a, "num": numero_recepisse,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, f"Erreur mise à jour : {str(e)[:200]}")
    return {"numero_recepisse": numero_recepisse,
            "statut_suivi": payload.statut_suivi}


# ─── SECRETARIAT_CADASTRE — Enregistrement courriers ──────────────

@router.get("/secretariat-cadastre", response_model=list)
async def espace_secretariat_cadastre(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN","SECRETARIAT_CADASTRE","CHEF_SERVICE_CADASTRE"
    ])),
):
    """Espace de travail secrétariat cadastre : courriers et rapports."""
    parcelles_recentes = []
    try:
        r = await db.execute(text("""
            SELECT id, nicad, statut, created_at
            FROM parcelles
            ORDER BY created_at DESC LIMIT 20
        """))
        parcelles_recentes = [dict(row) for row in r.mappings().all()]
    except Exception:
        pass
    return {
        "acteur": current_user.email,
        "role": "SECRETARIAT_CADASTRE",
        "parcelles_recentes": parcelles_recentes,
        "message": "Tableau de bord secrétariat cadastral",
    }


@router.post("/secretariat-cadastre/courrier", status_code=201)
async def enregistrer_courrier_cadastre(
    objet: str,
    expediteur: str,
    type_courrier: str = "entrant",
    reference: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN","SECRETARIAT_CADASTRE","CHEF_SERVICE_CADASTRE"
    ])),
):
    """Enregistre un courrier entrant ou sortant au cadastre."""
    import uuid
    r_num = await db.execute(
        text("SELECT generer_numero_officiel('REC','seq_recepisse')")
    )
    num = r_num.scalar()
    courrier_id = str(uuid.uuid4())
    try:
        await db.execute(text("""
            INSERT INTO recepisse_depot (
                id, numero_recepisse, module, type_demande,
                demandeur_nom, guichet_depot_id, date_depot
            ) VALUES (
                :id, :num, 'cadastre', :type,
                :exp, :uid, NOW()
            )
        """), {
            "id": courrier_id, "num": num,
            "type": f"courrier_{type_courrier}",
            "exp": expediteur, "uid": str(current_user.id),
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, f"Erreur courrier : {str(e)[:200]}")
    return {"id": courrier_id, "numero": num,
            "type": type_courrier, "objet": objet}


# ─── Workspaces CCFM ──────────────────────────────────────────

@router.get("/ccfm/guichetiere", tags=["Espace Travail — CCFM Guichetière"])
async def workspace_ccfm_guichetiere(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","GUICHETIER_CCFM","AGENT_CCFM","CHEF_CCFM"])),
):
    """Guichetière CCFM : demandes reçues en attente de traitement."""
    try:
        r = await db.execute(text("""
            SELECT ccfm_id, nus, nom_prenom, localite, lot, parcelle,
                   etat::TEXT, date_enregistrement, montant_paye
            FROM demandes_ccfm
            WHERE etat::TEXT IN ('DEMANDE_RECUE','VERIFICATION_AUTO','TICKET_EMIS','VERIFICATION_AUTO_KO')
            ORDER BY date_enregistrement DESC LIMIT 50
        """))
        return {"role":"guichetiere","demandes":[dict(row) for row in r.mappings().all()]}
    except Exception as e:
        import logging; logging.getLogger("espace").warning("ws_ccfm_guichetiere: %s", e)
        return {"role":"guichetiere","demandes":[]}


@router.get("/ccfm/secretaire", tags=["Espace Travail — CCFM Secrétaire"])
async def workspace_ccfm_secretaire(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","SECRETAIRE_CCFM","AGENT_CCFM","CHEF_CCFM"])),
):
    """Secrétaire CCFM : tickets émis — ordres de mission à préparer."""
    try:
        r = await db.execute(text("""
            SELECT ccfm_id, nus, nom_prenom, localite, lot, parcelle,
                   etat::TEXT, rnaf_conforme, bgu_conforme,
                   latitude, longitude, date_ticket_emis
            FROM demandes_ccfm
            WHERE etat::TEXT IN ('TICKET_EMIS','ORDRE_MISSION_EMIS','ATTENTE_VISITE_TERRAIN')
            ORDER BY date_ticket_emis ASC NULLS LAST LIMIT 100
        """))
        return {"role":"secretaire","demandes":[dict(row) for row in r.mappings().all()]}
    except Exception as e:
        import logging; logging.getLogger("espace").warning("ws_ccfm_secretaire: %s", e)
        return {"role":"secretaire","demandes":[]}


@router.get("/ccfm/directeur", tags=["Espace Travail — CCFM Directeur"])
async def workspace_ccfm_directeur(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","DIRECTEUR_URBANISME","CHEF_CCFM"])),
):
    """Directeur CCFM : rapports d appréciation en attente de signature."""
    try:
        r = await db.execute(text("""
            SELECT c.ccfm_id, c.nus, c.nom_prenom, c.localite, c.lot, c.parcelle,
                   c.superficie_m2, c.etat::TEXT,
                   r.verdict_global, r.ecart_superficie_pct, r.concordance_gps,
                   c.hash_ccfm, c.pdf_url
            FROM demandes_ccfm c
            LEFT JOIN rapports_appreciation_ccfm r ON c.ccfm_id = r.ccfm_id
            WHERE c.etat::TEXT IN ('ATTENTE_SIGNATURE_DIRECTEUR','RAPPORT_APPRECIATION')
            ORDER BY c.date_rapport_apprec ASC NULLS LAST
        """))
        return {"role":"directeur","demandes":[dict(row) for row in r.mappings().all()]}
    except Exception as e:
        import logging; logging.getLogger("espace").warning("ws_ccfm_directeur: %s", e)
        return {"role":"directeur","demandes":[]}


@router.get("/ccfm/archiviste", tags=["Espace Travail — CCFM Archiviste"])
async def workspace_ccfm_archiviste(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","ARCHIVISTE_ANNF","RESPONSABLE_ANNF","CHEF_CCFM"])),
):
    """Archiviste CCFM : certificats signés à archiver."""
    try:
        r = await db.execute(text("""
            SELECT ccfm_id, nus, reference_ccfm, nom_prenom,
                   localite, lot, parcelle, etat::TEXT,
                   date_signature, date_archivage, annf_code
            FROM demandes_ccfm
            WHERE etat::TEXT IN ('SIGNE_DIRECTEUR','RETRAIT_EN_ATTENTE','ARCHIVE','RETIRE')
            ORDER BY date_archivage DESC NULLS FIRST LIMIT 200
        """))
        return {"role":"archiviste","demandes":[dict(row) for row in r.mappings().all()]}
    except Exception as e:
        import logging; logging.getLogger("espace").warning("ws_ccfm_archiviste: %s", e)
        return {"role":"archiviste","demandes":[]}


# ─── Workspaces Banque, Justice, Domaine, Notaire, ANNF ────────

@router.get("/banque", tags=["Espace Travail — Banque"])
async def workspace_banque(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","BANQ_DIRECTEUR","BANQ_AGENT"])),
):
    """Banque : dossiers hypothèque en cours + mainlevées en attente."""
    try:
        r = await db.execute(text("""
            SELECT h.id, h.parcelle_id, h.montant, h.statut::TEXT,
                   h.date_creation, h.date_mainlevee,
                   p.nicad, p.lot, p.parcelle
            FROM hypotheques h
            LEFT JOIN parcelles p ON p.id = h.parcelle_id
            WHERE h.statut::TEXT IN ('ACTIF','EN_COURS','MAINLEVEE_DEMANDEE')
            ORDER BY h.date_creation DESC LIMIT 100
        """))
        return {"role":"banque","dossiers":[dict(row) for row in r.mappings().all()]}
    except Exception as e:
        import logging; logging.getLogger("espace").warning("ws_banque: %s", e)
        return {"role":"banque","dossiers":[]}


@router.get("/justice", tags=["Espace Travail — Justice"])
async def workspace_justice(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","JUGE_FONCIER","GREFFIER","HUISSIER"])),
):
    """Justice : litiges actifs + parcelles gelées."""
    try:
        r_lit = await db.execute(text("""
            SELECT l.id, l.parcelle_id, l.type_litige::TEXT,
                   l.statut::TEXT, l.date_ouverture,
                   p.nicad, p.lot, p.parcelle
            FROM litiges l
            LEFT JOIN parcelles p ON p.id = l.parcelle_id
            WHERE l.statut::TEXT NOT IN ('CLOS','ARCHIVE')
            ORDER BY l.date_ouverture DESC LIMIT 100
        """))
        r_gel = await db.execute(text("""
            SELECT parcelle_id, date_gel, motif, statut::TEXT
            FROM parcel_freeze_events
            WHERE statut::TEXT = 'GELE'
            ORDER BY date_gel DESC LIMIT 50
        """))
        return {
            "role":"justice",
            "litiges":[dict(row) for row in r_lit.mappings().all()],
            "gels":[dict(row) for row in r_gel.mappings().all()],
        }
    except Exception as e:
        import logging; logging.getLogger("espace").warning("ws_justice: %s", e)
        return {"role":"justice","litiges":[],"gels":[]}


@router.get("/domaine", tags=["Espace Travail — Domaine"])
async def workspace_domaine(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","AGENT_DOMAINE","DIRECTEUR_DOMAINE","DIRECTEUR_URBANISME"])),
):
    """Domaine : expropriations + régularisations en cours."""
    try:
        r_exp = await db.execute(text("""
            SELECT id, parcelle_id, type_expropriation::TEXT,
                   statut::TEXT, date_ouverture
            FROM expropriations
            WHERE statut::TEXT NOT IN ('ARCHIVE','ANNULE')
            ORDER BY date_ouverture DESC LIMIT 50
        """))
        r_reg = await db.execute(text("""
            SELECT id, parcelle_id, type_regularisation::TEXT,
                   statut::TEXT, date_ouverture
            FROM regularisations
            WHERE statut::TEXT NOT IN ('ARCHIVE','ANNULE')
            ORDER BY date_ouverture DESC LIMIT 50
        """))
        return {
            "role":"domaine",
            "expropriations":[dict(row) for row in r_exp.mappings().all()],
            "regularisations":[dict(row) for row in r_reg.mappings().all()],
        }
    except Exception as e:
        import logging; logging.getLogger("espace").warning("ws_domaine: %s", e)
        return {"role":"domaine","expropriations":[],"regularisations":[]}


@router.get("/notaire", tags=["Espace Travail — Notaire"])
async def workspace_notaire(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","NOTAIRE","ARCHIVISTE_ANNF"])),
):
    """Notaire : mutations en cours + successions ouvertes."""
    try:
        r_mut = await db.execute(text("""
            SELECT id, parcelle_id, type_acte::TEXT,
                   statut::TEXT, date_creation
            FROM property_transfers
            WHERE statut::TEXT IN ('en_cours','valide','EN_ATTENTE_SIGNATURE')
            ORDER BY date_creation DESC LIMIT 100
        """))
        return {"role":"notaire","mutations":[dict(row) for row in r_mut.mappings().all()]}
    except Exception as e:
        import logging; logging.getLogger("espace").warning("ws_notaire: %s", e)
        return {"role":"notaire","mutations":[]}


@router.get("/annf", tags=["Espace Travail — ANNF"])
async def workspace_annf(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","ARCHIVISTE_ANNF","RESPONSABLE_ANNF"])),
):
    """ANNF : archives en attente de scellement + anomalies signalées."""
    try:
        r_arc = await db.execute(text("""
            SELECT id, ida, type_archive::TEXT, entite_table,
                   scelle, created_at
            FROM annf_archive_links
            WHERE scelle = FALSE
            ORDER BY created_at ASC LIMIT 100
        """))
        return {"role":"annf","archives_en_attente":[dict(row) for row in r_arc.mappings().all()]}
    except Exception as e:
        import logging; logging.getLogger("espace").warning("ws_annf: %s", e)
        return {"role":"annf","archives_en_attente":[]}
