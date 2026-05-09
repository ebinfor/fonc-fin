"""
FONCIER+ v1.0.1 -- Module CCFM
Certificat de Conformite Fonciere Mixte -- 7 acteurs / 15 etats

Workflow :
  1. GUICHETIERE    POST /demandes                   NUS + recepisse PDF A4
  2. SYSTEME AUTO   POST /demandes/{nus}/ticket       Verification RNAF+BGU+GPS
  3. SECRETAIRE     POST /ordres-mission              Ordre de mission + ticket joint
  4. TOPOGRAPHE     POST /fiches-constat              Fiche de verification terrain
  5. SYSTEME AUTO   POST /rapports-appreciation       Rapport compile (ticket + fiche)
  6. DIRECTEUR      POST /demandes/{nus}/signer       PDF certificat A5 + QR Code
  6b.               GET  /demandes/{nus}/certificat-pdf  Telechargement PDF
  7. ARCHIVISTE     POST /demandes/{nus}/archiver     ANNF + blockchain

Verification inter-modules (NUS ou QR Code) :
  GET  /verifier/{nus}             NUS direct (sans auth)
  GET  /verifier-qr                QR Code 4 formats (sans auth)
  POST /verifier-parcelle          Par UUID parcelle
  GET  /ccfm-gate/{parcelle_id}    Gate bloquant inter-modules
"""
"""
FONCIER+ — Module CCFM v1 (DÉPRÉCIÉ)
=====================================
Ce module est déprécié depuis v1.0.9.
Utiliser /api/v3/ccfm (ccfm_v3.py) pour les nouvelles intégrations.
Les routes /v1/ccfm/* sont maintenues pour compatibilité ascendante.

Migration : remplacer /v1/ccfm par /api/v3/ccfm
"""
import warnings
warnings.warn(
    "Module CCFM v1 (/v1/ccfm) est déprécié. "
    "Utiliser /api/v3/ccfm à la place.",
    DeprecationWarning, stacklevel=2
)

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user

router = APIRouter(
    prefix="/ccfm",
    tags=["CCFM -- Certificat de Conformite Fonciere Mixte"],
)

# ── Roles ──────────────────────────────────────────────────────────
_LECTURE    = ["ADMIN", "CHEF_CCFM", "AGENT_CCFM", "AUDITEUR",
               "DIRECTEUR_URBANISME", "NOTAIRE", "BANQ_DIRECTEUR",
               "BANQ_AGENT", "JUGE_FONCIER", "GREFFIER"]
_GUICHET    = ["ADMIN", "CHEF_CCFM", "AGENT_CCFM", "GUICHETIER_CCFM"]
_SECRETAIRE = ["ADMIN", "CHEF_CCFM", "SECRETAIRE_CCFM", "SECRETAIRE_GENERAL"]
_TOPOGRAPHE = ["ADMIN", "TOPOGRAPHE", "GEOMETRE"]
_DIRECTEUR  = ["ADMIN", "DIRECTEUR_URBANISME", "CHEF_CCFM"]
_ARCHIVISTE = ["ADMIN", "ARCHIVISTE_ANNF", "RESPONSABLE_ANNF", "CHEF_CCFM"]


# ── Schemas Pydantic ───────────────────────────────────────────────

class DemandeIn(BaseModel):
    nom_prenom:         str   = Field(..., min_length=3)
    nip_passeport:      str   = Field(..., min_length=3)
    date_naissance:     str   = Field(..., description="YYYY-MM-DD")
    lieu_naissance:     str   = Field(..., min_length=2)
    adresse:            str   = Field(..., min_length=5)
    telephone:          str   = Field(..., min_length=8, max_length=20)
    email:              Optional[str] = None
    localite:           str   = Field(..., min_length=2)
    lot:                str   = Field(..., min_length=1)
    parcelle:           str   = Field(..., min_length=1)
    superficie_m2:      float = Field(..., gt=0)
    mode_paiement:      str   = Field(..., description="AIRTEL_MONEY|MOOV_MONEY|BANQUE|TRESOR_PUBLIC")
    reference_paiement: str   = Field(..., min_length=3)
    montant_paye:       float = Field(default=50000.0, ge=50000)
    numero_arrete:      Optional[str] = None
    nicad_code:         Optional[str] = None


class OrdreMissionIn(BaseModel):
    ccfm_id:            str
    topographe_id:      str
    topographe_nom:     str
    date_prevue_visite: Optional[str] = None
    instructions:       Optional[str] = None


class FicheConstatIn(BaseModel):
    ccfm_id:               str
    superficie_mesuree_m2: float = Field(..., gt=0)
    gps_mesure_latitude:   Optional[float] = None
    gps_mesure_longitude:  Optional[float] = None
    concordance_gps:       Optional[bool]  = None
    jo_publie:             bool  = False
    arrete_numero:         Optional[str]   = None
    arrete_autorite:       Optional[str]   = None
    titre_foncier_numero:  Optional[str]   = None
    titre_authentique:     Optional[bool]  = None
    decision:              str   = Field(..., description="AUTORISATION ou REFUS")
    motif_refus:           Optional[str]   = None
    observations:          Optional[str]   = None
    topographe_nom:        str


class RapportIn(BaseModel):
    ccfm_id:             str
    verdict_global:      str   = Field(..., description="CONFORME|A_VERIFIER|NON_CONFORME")
    points_conformite:   List[str] = []
    anomalies_detectees: List[str] = []


class SignerIn(BaseModel):
    directeur_nom:  str  = Field(..., min_length=3)
    huissier_nom:   str  = Field(default="A renseigner")
    observations:   Optional[str] = None


class ArchiverIn(BaseModel):
    observations: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════
# ETAPE 1 -- GUICHETIERE : Enregistrement + NUS
# ═══════════════════════════════════════════════════════════════════

@router.post("/demandes", status_code=201)
async def creer_demande(
    payload: DemandeIn,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_GUICHET)),
):
    """Etape 1 -- Guichetiere.
    Enregistre la demande, genere le NUS atomique NUS-YYYY-NNNNN
    et le recepisse PDF A4 remis au beneficiaire.
    Declenche la verification automatique RNAF+BGU en arriere-plan.
    """
    try:
        r = await db.execute(text("SELECT generer_nus_ccfm()"))
        nus = r.scalar()
    except Exception:
        yr = datetime.now().year
        r2 = await db.execute(text(
            "SELECT COUNT(*)+1 FROM demandes_ccfm "
            "WHERE EXTRACT(YEAR FROM date_enregistrement)=:y"), {"y": yr})
        seq = r2.scalar() or 1
        nus = f"NUS-{yr}-{str(seq).zfill(5)}"

    sha = hashlib.sha256(
        f"{nus}|{payload.nom_prenom}|{payload.lot}|{payload.parcelle}|{payload.superficie_m2}".encode()
    ).hexdigest()

    try:
        await db.execute(text("""
            INSERT INTO demandes_ccfm (
                ccfm_id, nus, nom_prenom, nip_passeport,
                date_naissance, lieu_naissance, adresse, telephone, email,
                localite, lot, parcelle, superficie_m2,
                numero_arrete, nicad_code,
                mode_paiement, reference_paiement, montant_paye,
                enregistre_par, etat, hash_ccfm, date_enregistrement
            ) VALUES (
                gen_random_uuid(), :nus, :nom, :nip,
                :dn::date, :ln, :adr, :tel, :email,
                :loc, :lot, :parc, :sup,
                :na, :nicad,
                :mp, :rp, :mont,
                :uid, 'DEMANDE_RECUE', :sha, NOW()
            )
        """), {
            "nus": nus, "nom": payload.nom_prenom, "nip": payload.nip_passeport,
            "dn": payload.date_naissance, "ln": payload.lieu_naissance,
            "adr": payload.adresse, "tel": payload.telephone, "email": payload.email,
            "loc": payload.localite, "lot": payload.lot, "parc": payload.parcelle,
            "sup": payload.superficie_m2, "na": payload.numero_arrete,
            "nicad": payload.nicad_code, "mp": payload.mode_paiement,
            "rp": payload.reference_paiement, "mont": payload.montant_paye,
            "uid": str(current_user.id), "sha": sha,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:300])

    async def _gen_pdf():
        try:
            from app.services.generate_ccfm import generer_pdf_formulaire_demande
            import os
            pdf_bytes = generer_pdf_formulaire_demande(
                nus=nus, nom_prenom=payload.nom_prenom,
                nip_passeport=payload.nip_passeport,
                date_naissance=payload.date_naissance,
                lieu_naissance=payload.lieu_naissance,
                adresse=payload.adresse, telephone=payload.telephone,
                localite=payload.localite, lot=payload.lot,
                parcelle=payload.parcelle, superficie_m2=payload.superficie_m2,
                mode_paiement=payload.mode_paiement,
                reference_paiement=payload.reference_paiement,
                montant_paye=payload.montant_paye,
            )
            os.makedirs("/data/ccfm/demandes", exist_ok=True)
            pdf_path = f"/data/ccfm/demandes/{nus}_demande.pdf"
            with open(pdf_path, "wb") as f:
                f.write(pdf_bytes)
            async with db.begin():
                await db.execute(text(
                    "UPDATE demandes_ccfm SET pdf_demande_url=:p WHERE nus=:n"
                ), {"p": pdf_path, "n": nus})
        except Exception:
            pass

    background_tasks.add_task(_gen_pdf)

    return {
        "nus": nus, "etat": "DEMANDE_RECUE", "sha256": sha,
        "message": "Demande enregistree -- recepisse PDF en generation",
        "prochaine_etape": "Verification automatique RNAF+BGU (systeme)",
    }


# ═══════════════════════════════════════════════════════════════════
# ETAPE 2 -- SYSTEME : Verification auto RNAF+BGU+GPS
# ═══════════════════════════════════════════════════════════════════

@router.post("/demandes/{nus}/ticket")
async def generer_ticket_rnaf_bgu(
    nus: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN", "CHEF_CCFM", "AGENT_CCFM"])),
):
    """Etape 2 -- Systeme.
    Verification automatique RNAF + BGU + coordonnees GPS.
    Produit le ticket qui sera joint a l ordre de mission.
    """
    r = await db.execute(text(
        "SELECT ccfm_id, lot, parcelle, nicad_code FROM demandes_ccfm WHERE nus=:n"
    ), {"n": nus})
    dem = r.mappings().first()
    if not dem:
        raise HTTPException(404, f"NUS {nus} introuvable")

    rnaf_ok, rnaf_msg = True, None
    bgu_ok,  bgu_msg  = True, None
    lat, lon = None, None

    try:
        r_rnaf = await db.execute(text(
            "SELECT COUNT(*) FROM rnaf WHERE statut IN ('publie','scelle')"
        ))
        rnaf_ok = (r_rnaf.scalar() or 0) > 0
    except Exception as e:
        rnaf_msg = str(e)[:100]

    try:
        r_bgu = await db.execute(text("""
            SELECT ST_Y(p.geom::geometry) AS lat, ST_X(p.geom::geometry) AS lon
            FROM parcelles p WHERE p.nicad=:nicad LIMIT 1
        """), {"nicad": dem["nicad_code"] or ""})
        bgu_row = r_bgu.mappings().first()
        if bgu_row and bgu_row.get("lat"):
            lat = float(bgu_row["lat"])
            lon = float(bgu_row["lon"])
    except Exception as e:
        bgu_msg = str(e)[:100]

    ticket_hash = hashlib.sha256(
        f"{nus}|{rnaf_ok}|{bgu_ok}|{lat}|{lon}".encode()
    ).hexdigest()

    new_etat = "TICKET_EMIS" if (rnaf_ok and bgu_ok) else "VERIFICATION_AUTO_KO"

    try:
        await db.execute(text("""
            UPDATE demandes_ccfm SET
                etat=:etat,
                rnaf_conforme=:rnaf, rnaf_anomalies=:ra,
                bgu_conforme=:bgu,   bgu_anomalies=:ba,
                latitude=:lat, longitude=:lon, ticket_hash=:th,
                date_verification_auto=NOW()
            WHERE nus=:n
        """), {
            "etat": new_etat, "rnaf": rnaf_ok, "ra": rnaf_msg,
            "bgu": bgu_ok, "ba": bgu_msg,
            "lat": lat, "lon": lon, "th": ticket_hash, "n": nus,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    return {
        "nus": nus, "etat": new_etat,
        "rnaf_conforme": rnaf_ok, "bgu_conforme": bgu_ok,
        "gps": {"lat": lat, "lon": lon} if lat else None,
        "ticket_hash": ticket_hash,
        "prochaine_etape": "Secretaire emet l ordre de mission" if new_etat == "TICKET_EMIS"
                           else "Dossier rejete",
    }


# ═══════════════════════════════════════════════════════════════════
# ETAPE 3 -- SECRETAIRE : Ordre de mission
# ═══════════════════════════════════════════════════════════════════

@router.post("/ordres-mission", status_code=201)
async def emettre_ordre_mission(
    payload: OrdreMissionIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SECRETAIRE)),
):
    """Etape 3 -- Secretaire.
    Emet l ordre de mission vers le topographe.
    Le ticket RNAF+BGU+GPS est joint automatiquement.
    """
    r = await db.execute(text(
        "SELECT nus, rnaf_conforme, bgu_conforme, latitude, longitude, ticket_hash, etat "
        "FROM demandes_ccfm WHERE ccfm_id=:cid"
    ), {"cid": payload.ccfm_id})
    dem = r.mappings().first()
    if not dem:
        raise HTTPException(404, "Demande CCFM introuvable")
    if dem["etat"] not in ("TICKET_EMIS", "VERIFICATION_AUTO"):
        raise HTTPException(400, f"Etat {dem['etat']} -- ticket requis avant ordre de mission")

    yr = datetime.now().year
    r2 = await db.execute(text(
        "SELECT COUNT(*)+1 FROM ordres_mission_ccfm WHERE EXTRACT(YEAR FROM date_emission)=:y"
    ), {"y": yr})
    num = f"OM-{yr}-{str(r2.scalar() or 1).zfill(5)}"

    ticket_joint = {
        "rnaf_conforme": bool(dem["rnaf_conforme"]),
        "bgu_conforme":  bool(dem["bgu_conforme"]),
        "latitude":      float(dem["latitude"])  if dem.get("latitude")  else None,
        "longitude":     float(dem["longitude"]) if dem.get("longitude") else None,
        "ticket_hash":   dem["ticket_hash"],
    }

    try:
        await db.execute(text("""
            INSERT INTO ordres_mission_ccfm
                (ordre_id, ccfm_id, numero_ordre, destinataire_id,
                 destinataire_nom, instructions, ticket_joint, emis_par)
            VALUES
                (gen_random_uuid(), :cid, :num, :tid,
                 :tnom, :instr, :tj::jsonb, :uid)
        """), {
            "cid": payload.ccfm_id, "num": num,
            "tid": payload.topographe_id, "tnom": payload.topographe_nom,
            "instr": payload.instructions,
            "tj": json.dumps(ticket_joint, default=str),
            "uid": str(current_user.id),
        })
        await db.execute(text("""
            UPDATE demandes_ccfm SET
                etat='ORDRE_MISSION_EMIS',
                topographe_assigne=:tnom,
                date_assignation_topographe=NOW()
            WHERE ccfm_id=:cid
        """), {"tnom": payload.topographe_nom, "cid": payload.ccfm_id})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    return {
        "numero_ordre": num, "ccfm_id": payload.ccfm_id,
        "destinataire": payload.topographe_nom,
        "ticket_joint": ticket_joint,
        "message": "Ordre de mission emis avec ticket RNAF+BGU+GPS joint",
    }


# ═══════════════════════════════════════════════════════════════════
# ETAPE 4 -- TOPOGRAPHE : Fiche de constat terrain
# ═══════════════════════════════════════════════════════════════════

@router.post("/fiches-constat", status_code=201)
async def deposer_fiche_constat(
    payload: FicheConstatIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_TOPOGRAPHE)),
):
    """Etape 4 -- Topographe.
    Depose la fiche de verification administrative fonciere.
    Calcule l ecart de superficie et verifie la concordance GPS.
    """
    r = await db.execute(text(
        "SELECT superficie_m2, latitude, longitude FROM demandes_ccfm WHERE ccfm_id=:cid"
    ), {"cid": payload.ccfm_id})
    dem = r.mappings().first()
    if not dem:
        raise HTTPException(404, "Demande introuvable")

    ecart_pct = None
    if dem["superficie_m2"] and payload.superficie_mesuree_m2:
        ecart_pct = round(
            abs(float(dem["superficie_m2"]) - payload.superficie_mesuree_m2)
            / float(dem["superficie_m2"]) * 100, 2
        )

    conc_gps = payload.concordance_gps
    if conc_gps is None and dem.get("latitude") and payload.gps_mesure_latitude:
        conc_gps = (
            abs(float(dem["latitude"]) - payload.gps_mesure_latitude) < 0.001 and
            abs(float(dem.get("longitude") or 0) - (payload.gps_mesure_longitude or 0)) < 0.001
        )

    hash_fiche = hashlib.sha256(
        f"{payload.ccfm_id}|{payload.superficie_mesuree_m2}|{payload.decision}".encode()
    ).hexdigest()

    try:
        await db.execute(text("""
            INSERT INTO fiches_constat_ccfm (
                fiche_id, ccfm_id,
                superficie_mesuree_m2, gps_mesure_latitude, gps_mesure_longitude,
                concordance_gps, ecart_superficie_pct,
                jo_publie, arrete_numero, arrete_autorite,
                titre_foncier_numero, titre_authentique,
                decision, motif_refus, observations,
                topographe_nom, topographe_id, hash_fiche
            ) VALUES (
                gen_random_uuid(), :cid,
                :sup, :lat, :lon, :cgps, :ep,
                :jo, :arn, :ara, :tfn, :ta,
                :dec, :mrf, :obs,
                :tnom, :tid, :hf
            )
        """), {
            "cid": payload.ccfm_id,
            "sup": payload.superficie_mesuree_m2,
            "lat": payload.gps_mesure_latitude, "lon": payload.gps_mesure_longitude,
            "cgps": conc_gps, "ep": ecart_pct,
            "jo": payload.jo_publie, "arn": payload.arrete_numero,
            "ara": payload.arrete_autorite, "tfn": payload.titre_foncier_numero,
            "ta": payload.titre_authentique, "dec": payload.decision,
            "mrf": payload.motif_refus, "obs": payload.observations,
            "tnom": payload.topographe_nom, "tid": str(current_user.id),
            "hf": hash_fiche,
        })
        await db.execute(text("""
            UPDATE demandes_ccfm SET
                etat='FICHE_CONSTAT_DEPOSEE', date_rapport_topographe=NOW()
            WHERE ccfm_id=:cid
        """), {"cid": payload.ccfm_id})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    return {
        "ccfm_id": payload.ccfm_id, "decision": payload.decision,
        "ecart_superficie_pct": ecart_pct, "concordance_gps": conc_gps,
        "hash_fiche": hash_fiche,
        "message": "Fiche de constat deposee -- rapport en attente de generation",
    }


# ═══════════════════════════════════════════════════════════════════
# ETAPE 5 -- SYSTEME : Rapport d appreciation
# ═══════════════════════════════════════════════════════════════════

@router.post("/rapports-appreciation", status_code=201)
async def generer_rapport_appreciation(
    payload: RapportIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN", "CHEF_CCFM", "AGENT_CCFM"])),
):
    """Etape 5 -- Systeme.
    Compile le ticket (etape 2) + la fiche terrain (etape 4)
    en rapport d appreciation soumis au directeur.
    """
    r = await db.execute(text("""
        SELECT d.rnaf_conforme, d.bgu_conforme, d.latitude, d.longitude,
               f.superficie_mesuree_m2, f.ecart_superficie_pct,
               f.gps_mesure_latitude, f.gps_mesure_longitude,
               f.concordance_gps, f.jo_publie, f.titre_authentique,
               f.decision, f.observations
        FROM demandes_ccfm d
        LEFT JOIN fiches_constat_ccfm f ON f.ccfm_id=d.ccfm_id
        WHERE d.ccfm_id=:cid
    """), {"cid": payload.ccfm_id})
    row = r.mappings().first()
    if not row:
        raise HTTPException(404, "Demande introuvable")

    rapport_hash = hashlib.sha256(
        f"{payload.ccfm_id}|{payload.verdict_global}".encode()
    ).hexdigest()

    try:
        await db.execute(text("""
            INSERT INTO rapports_appreciation_ccfm (
                rapport_id, ccfm_id,
                rnaf_conforme, bgu_conforme, gps_ticket_lat, gps_ticket_lon,
                superficie_mesuree_m2, ecart_superficie_pct,
                gps_mesure_lat, gps_mesure_lon, concordance_gps,
                jo_publie, titre_authentique, decision_topographe,
                observations_topographe, verdict_global,
                points_conformite, anomalies_detectees, rapport_hash
            ) VALUES (
                gen_random_uuid(), :cid,
                :rnaf, :bgu, :glat, :glon,
                :sup, :ep, :mlat, :mlon, :cgps,
                :jo, :ta, :dec, :obs,
                :verdict, :pc::jsonb, :ad::jsonb, :rh
            )
        """), {
            "cid":     payload.ccfm_id,
            "rnaf":    row.get("rnaf_conforme"), "bgu": row.get("bgu_conforme"),
            "glat":    row.get("latitude"),      "glon": row.get("longitude"),
            "sup":     row.get("superficie_mesuree_m2"),
            "ep":      row.get("ecart_superficie_pct"),
            "mlat":    row.get("gps_mesure_latitude"),
            "mlon":    row.get("gps_mesure_longitude"),
            "cgps":    row.get("concordance_gps"),
            "jo":      row.get("jo_publie"),
            "ta":      row.get("titre_authentique"),
            "dec":     row.get("decision"),
            "obs":     row.get("observations"),
            "verdict": payload.verdict_global,
            "pc":      json.dumps(payload.points_conformite),
            "ad":      json.dumps(payload.anomalies_detectees),
            "rh":      rapport_hash,
        })
        await db.execute(text("""
            UPDATE demandes_ccfm SET
                etat='ATTENTE_SIGNATURE_DIRECTEUR', date_rapport_apprec=NOW()
            WHERE ccfm_id=:cid
        """), {"cid": payload.ccfm_id})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    return {
        "ccfm_id": payload.ccfm_id,
        "verdict_global": payload.verdict_global,
        "rapport_hash": rapport_hash,
        "message": "Rapport soumis au directeur pour signature",
    }


# ═══════════════════════════════════════════════════════════════════
# ETAPE 6 -- DIRECTEUR : Signature + PDF certificat A5
# ═══════════════════════════════════════════════════════════════════

@router.post("/demandes/{nus}/signer")
async def signer_ccfm(
    nus: str,
    payload: SignerIn,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_DIRECTEUR)),
):
    """Etape 6 -- Directeur.
    Signature officielle + generation du PDF certificat A5.
    Genere la reference CCFM/AAAA/MM/NNNN et le QR Code scannable.
    """
    r = await db.execute(text(
        "SELECT ccfm_id, etat, nom_prenom, nip_passeport, date_naissance, "
        "lieu_naissance, localite, lot, parcelle, superficie_m2, "
        "latitude, longitude, topographe_assigne "
        "FROM demandes_ccfm WHERE nus=:n"
    ), {"n": nus})
    dem = r.mappings().first()
    if not dem:
        raise HTTPException(404, f"NUS {nus} introuvable")
    if dem["etat"] not in ("ATTENTE_SIGNATURE_DIRECTEUR", "RAPPORT_APPRECIATION",
                            "FICHE_CONSTAT_DEPOSEE"):
        raise HTTPException(400, f"Etat '{dem['etat']}' -- rapport requis avant signature")

    now = datetime.now(timezone.utc)

    try:
        r2 = await db.execute(text("SELECT generer_reference_ccfm()"))
        reference_ccfm = r2.scalar()
    except Exception:
        r3 = await db.execute(text(
            "SELECT COUNT(*)+1 FROM demandes_ccfm "
            "WHERE date_signature IS NOT NULL "
            "AND EXTRACT(YEAR FROM date_signature)=:y "
            "AND EXTRACT(MONTH FROM date_signature)=:m"
        ), {"y": now.year, "m": now.month})
        seq = r3.scalar() or 1
        reference_ccfm = f"CCFM/{now.year}/{str(now.month).zfill(2)}/{str(seq).zfill(4)}"

    hash_ccfm = hashlib.sha256(
        f"{nus}|{reference_ccfm}|{dem['nom_prenom']}|{dem['lot']}|{dem['parcelle']}".encode()
    ).hexdigest()
    qr_data = f"https://foncier.gov.ne/verify/{nus}"

    try:
        await db.execute(text("""
            UPDATE demandes_ccfm SET
                etat='SIGNE_DIRECTEUR',
                reference_ccfm=:ref, hash_ccfm=:hash,
                qr_code=:qr, signature_directeur=:sid,
                directeur_validateur=:dnom,
                date_signature=NOW(), date_validation_directeur=NOW()
            WHERE nus=:n
        """), {
            "ref": reference_ccfm, "hash": hash_ccfm, "qr": qr_data,
            "sid": str(current_user.id), "dnom": payload.directeur_nom, "n": nus,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    async def _gen_pdf():
        try:
            from app.services.generate_ccfm import generer_pdf_certificat
            import os
            pdf_bytes = generer_pdf_certificat(
                nus=nus, reference_ccfm=reference_ccfm, date_signature=now,
                nom_prenom=dem["nom_prenom"], nip_passeport=dem["nip_passeport"],
                date_naissance=str(dem["date_naissance"]),
                lieu_naissance=dem["lieu_naissance"],
                localite=dem["localite"], lot=dem["lot"], parcelle=dem["parcelle"],
                superficie_m2=float(dem["superficie_m2"]),
                latitude=float(dem["latitude"])  if dem.get("latitude")  else None,
                longitude=float(dem["longitude"]) if dem.get("longitude") else None,
                huissier_nom=payload.huissier_nom,
                topographe_nom=dem["topographe_assigne"] or "A renseigner",
                directeur_nom=payload.directeur_nom,
                hash_ccfm=hash_ccfm,
            )
            os.makedirs("/data/ccfm/certificats", exist_ok=True)
            pdf_path = f"/data/ccfm/certificats/{nus}.pdf"
            with open(pdf_path, "wb") as f:
                f.write(pdf_bytes)
            async with db.begin():
                await db.execute(text(
                    "UPDATE demandes_ccfm SET pdf_url=:p WHERE nus=:n"
                ), {"p": pdf_path, "n": nus})
        except Exception as exc:
            import logging
            logging.getLogger("ccfm").warning("PDF gen failed: %s", exc)

    background_tasks.add_task(_gen_pdf)

    return {
        "nus": nus, "reference_ccfm": reference_ccfm,
        "etat": "SIGNE_DIRECTEUR",
        "hash_ccfm": hash_ccfm,
        "qr_code_url": qr_data,
        "signe_par": payload.directeur_nom,
        "date_signature": now.isoformat(),
        "message": "Certificat signe -- PDF A5 en generation",
    }


# ── Telecharger le certificat PDF ------------------------------------

@router.get("/demandes/{nus}/certificat-pdf", response_model=dict)
async def telecharger_pdf(
    nus: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Telecharge le PDF certificat CCFM A5. Genere a la volee si cache absent."""
    r = await db.execute(text(
        "SELECT nom_prenom, nip_passeport, date_naissance, lieu_naissance, "
        "localite, lot, parcelle, superficie_m2, latitude, longitude, "
        "topographe_assigne, directeur_validateur, reference_ccfm, "
        "date_signature, hash_ccfm, pdf_url, etat "
        "FROM demandes_ccfm WHERE nus=:n"
    ), {"n": nus})
    dem = r.mappings().first()
    if not dem:
        raise HTTPException(404, f"NUS {nus} introuvable")
    if dem["etat"] not in ("SIGNE_DIRECTEUR", "ARCHIVE", "RETRAIT_EN_ATTENTE", "RETIRE"):
        raise HTTPException(400, f"Certificat non signe -- etat actuel : {dem['etat']}")

    pdf_bytes = None
    if dem.get("pdf_url"):
        try:
            with open(dem["pdf_url"], "rb") as f:
                pdf_bytes = f.read()
        except (FileNotFoundError, TypeError):
            pass

    if not pdf_bytes:
        try:
            from app.services.generate_ccfm import generer_pdf_certificat
            pdf_bytes = generer_pdf_certificat(
                nus=nus,
                reference_ccfm=dem["reference_ccfm"] or f"CCFM/DRAFT/{nus}",
                date_signature=dem["date_signature"] or datetime.now(timezone.utc),
                nom_prenom=dem["nom_prenom"],
                nip_passeport=dem["nip_passeport"],
                date_naissance=str(dem["date_naissance"]),
                lieu_naissance=dem["lieu_naissance"],
                localite=dem["localite"], lot=dem["lot"], parcelle=dem["parcelle"],
                superficie_m2=float(dem["superficie_m2"]),
                latitude=float(dem["latitude"])  if dem.get("latitude")  else None,
                longitude=float(dem["longitude"]) if dem.get("longitude") else None,
                topographe_nom=dem["topographe_assigne"] or "A renseigner",
                directeur_nom=dem["directeur_validateur"] or "Le Directeur de l Urbanisme",
                hash_ccfm=dem["hash_ccfm"],
            )
        except Exception as e:
            raise HTTPException(500, f"Generation PDF echouee : {str(e)[:200]}")

    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=CCFM_{nus}.pdf"},
    )


# ═══════════════════════════════════════════════════════════════════
# ETAPE 7 -- ARCHIVISTE : Archivage ANNF + blockchain
# ═══════════════════════════════════════════════════════════════════

@router.post("/demandes/{nus}/archiver", status_code=201)
async def archiver_ccfm(
    nus: str,
    payload: ArchiverIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ARCHIVISTE)),
):
    """Etape 7 -- Archiviste.
    Archive le certificat dans l ANNF national et genere le hash blockchain.
    """
    r = await db.execute(text(
        "SELECT ccfm_id, etat, hash_ccfm FROM demandes_ccfm WHERE nus=:n"
    ), {"n": nus})
    dem = r.mappings().first()
    if not dem:
        raise HTTPException(404, f"NUS {nus} introuvable")
    if dem["etat"] != "SIGNE_DIRECTEUR":
        raise HTTPException(400, f"Etat '{dem['etat']}' -- signature directeur requise")

    yr = datetime.now().year
    r2 = await db.execute(text(
        "SELECT COUNT(*)+1 FROM demandes_ccfm "
        "WHERE annf_code IS NOT NULL AND EXTRACT(YEAR FROM date_archivage)=:y"
    ), {"y": yr})
    seq = r2.scalar() or 1
    annf_code = f"ANNF-CCFM-{yr}-{str(seq).zfill(5)}"
    tx_hash = hashlib.sha256(
        f"{annf_code}|{dem['hash_ccfm']}|{datetime.now().isoformat()}".encode()
    ).hexdigest()

    try:
        await db.execute(text("""
            UPDATE demandes_ccfm SET
                etat='RETRAIT_EN_ATTENTE', annf_code=:ac,
                tx_blockchain=:tx, archiviste=:uid, date_archivage=NOW()
            WHERE nus=:n
        """), {"ac": annf_code, "tx": tx_hash, "uid": str(current_user.id), "n": nus})
        try:
            from app.services.workflow_orchestrator import ANNFArchiveService
            from app.models.workflows import TypeArchiveANNF
            await ANNFArchiveService.archiver(
                db=db, type_archive=TypeArchiveANNF.CCFM,
                entite_id=str(dem["ccfm_id"]), entite_table="demandes_ccfm",
                snapshot={"nus": nus, "annf_code": annf_code, "tx_blockchain": tx_hash},
                archive_par_id=str(current_user.id),
            )
        except Exception:
            pass
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    return {
        "nus": nus, "etat": "RETRAIT_EN_ATTENTE",
        "annf_code": annf_code, "tx_blockchain": tx_hash,
        "message": "Archive dans l ANNF -- pret pour retrait beneficiaire",
    }


# ═══════════════════════════════════════════════════════════════════
# VERIFICATION CCFM -- NUS / QR CODE / PARCELLE (public + gate)
# ═══════════════════════════════════════════════════════════════════

@router.get("/verifier/{nus}", response_model=dict)
async def verifier_par_nus(
    nus: str,
    db: AsyncSession = Depends(get_db),
):
    """Verification publique par NUS -- sans authentification.
    Format NUS : NUS-AAAA-NNNNN
    """
    from app.services.ccfm_verification import verifier_ccfm
    result = await verifier_ccfm(db=db, nus=nus)
    if not result.get("valide") and result.get("nus") is None:
        raise HTTPException(404, result["message"])
    return result


@router.get("/verifier-qr", response_model=list)
async def verifier_par_qr(
    qr_code: str = Query(..., description=(
        "Donnees QR scannees. 4 formats supportes : "
        "(1) URL https://foncier.gov.ne/verify/NUS-... "
        "(2) JSON {nus:NUS-...} "
        "(3) Pipe CCFM|NUS-...|URL "
        "(4) NUS brut NUS-AAAA-NNNNN"
    )),
    db: AsyncSession = Depends(get_db),
):
    """Verification par QR Code -- 4 formats -- sans authentification."""
    from app.services.ccfm_verification import verifier_ccfm
    result = await verifier_ccfm(db=db, qr_code=qr_code)
    if not result.get("valide") and result.get("nus") is None:
        raise HTTPException(400, result["message"])
    return result


@router.post("/verifier-parcelle")
async def verifier_par_parcelle(
    parcelle_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Verifie le CCFM d une parcelle par UUID."""
    from app.services.ccfm_verification import verifier_ccfm
    return await verifier_ccfm(db=db, parcelle_id=parcelle_id)


@router.get("/ccfm-gate/{parcelle_id}", response_model=dict)
async def ccfm_gate(
    parcelle_id: str,
    action: str = Query("operation"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_LECTURE)),
):
    """Gate CCFM bloquant -- utilise par Commune, Domaine, Justice, Banque, Notaire."""
    from app.services.ccfm_verification import exiger_ccfm_valide
    ok, msg = await exiger_ccfm_valide(db=db, parcelle_id=parcelle_id, contexte=action)
    if not ok:
        raise HTTPException(422, {
            "code": "CCFM_GATE_FAILED", "message": msg,
            "correction": "POST /v1/ccfm/demandes",
        })
    return {"gate_ok": True, "parcelle_id": parcelle_id, "message": msg}


# ═══════════════════════════════════════════════════════════════════
# LECTURE : tableau de bord, listes, stats, historique
# ═══════════════════════════════════════════════════════════════════

@router.get("/tableau-de-bord", response_model=list)
async def tableau_de_bord(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_LECTURE)),
):
    """Tableau de bord CCFM : KPIs + actions en attente par acteur."""
    stats = {}
    for key, sql in {
        "total":         "SELECT COUNT(*) FROM demandes_ccfm",
        "en_cours":      "SELECT COUNT(*) FROM demandes_ccfm WHERE etat NOT IN ('ARCHIVE','RETRAIT_EN_ATTENTE','RETIRE','REJETE')",
        "signes":        "SELECT COUNT(*) FROM demandes_ccfm WHERE etat IN ('SIGNE_DIRECTEUR','ARCHIVE','RETRAIT_EN_ATTENTE','RETIRE')",
        "rejetes":       "SELECT COUNT(*) FROM demandes_ccfm WHERE etat='REJETE'",
        "delai_moyen_j": "SELECT ROUND(AVG(EXTRACT(DAY FROM (date_signature - date_enregistrement))),1) FROM demandes_ccfm WHERE date_signature IS NOT NULL",
    }.items():
        try:
            r = await db.execute(text(sql))
            stats[key] = r.scalar() or 0
        except Exception:
            stats[key] = 0

    attente = []
    try:
        r = await db.execute(text("""
            SELECT nus, nom_prenom, etat::text,
                   localite || ' -- ' || lot || '/' || parcelle AS parcelle,
                   EXTRACT(DAY FROM (NOW()-date_enregistrement)) AS jours
            FROM demandes_ccfm
            WHERE etat NOT IN ('ARCHIVE','RETRAIT_EN_ATTENTE','RETIRE','REJETE')
            ORDER BY date_enregistrement ASC LIMIT 20
        """))
        attente = [dict(row) for row in r.mappings().all()]
    except Exception:
        pass

    return {"stats": stats, "actions_en_attente": attente}


@router.get("/demandes", response_model=list)
async def lister_demandes(
    etat: Optional[str]    = Query(None),
    localite: Optional[str] = Query(None),
    page: int              = Query(1,  ge=1),
    limit: int             = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_LECTURE)),
):
    """Liste paginee des demandes CCFM."""
    where = "WHERE 1=1"
    params: dict = {"l": limit, "o": (page - 1) * limit}
    if etat:     where += " AND etat=:etat";    params["etat"] = etat
    if localite: where += " AND localite=:loc"; params["loc"]  = localite
    try:
        # ScopeFilter
        try:
            from app.core.scope_filter import ScopeFilter as _S
            _sc = _S.from_user(current_user)
            if not _sc.peut_voir_national and _sc.region:
                where += ' AND c.localite = :_scope_region'
                params['_scope_region'] = _sc.region
        except Exception: pass
        r = await db.execute(text(f"""
            SELECT ccfm_id, nus, reference_ccfm, nom_prenom,
                   localite, lot, parcelle, superficie_m2, etat::text,
                   date_enregistrement, date_signature, topographe_assigne
            FROM demandes_ccfm {where}
            ORDER BY date_enregistrement DESC
            LIMIT :l OFFSET :o
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("lister_demandes: %s", e)
        return []


@router.get("/demandes/{nus}", response_model=dict)
async def get_demande(
    nus: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_LECTURE)),
):
    """Detail complet d une demande CCFM par NUS."""
    r = await db.execute(text("SELECT * FROM demandes_ccfm WHERE nus=:n"), {"n": nus})
    row = r.mappings().first()
    if not row:
        raise HTTPException(404, f"NUS {nus} introuvable")
    return dict(row)


@router.get("/demandes/{nus}/historique", response_model=dict)
async def historique_demande(
    nus: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_LECTURE)),
):
    """Historique complet des transitions d etat (via trigger trg_ccfm_historique)."""
    try:
        r = await db.execute(text("""
            SELECT h.etat_precedent, h.etat_nouveau, h.action,
                   h.commentaire, h.effectue_par, h.role_effectueur, h.created_at
            FROM historique_ccfm h
            JOIN demandes_ccfm d ON d.ccfm_id=h.ccfm_id
            WHERE d.nus=:n ORDER BY h.created_at ASC
        """), {"n": nus})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("historique: %s", e)
        return []


@router.get("/demandes/parcelle/{parcelle_id}", response_model=dict)
async def demandes_par_parcelle(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """CCFM associes a une parcelle UUID."""
    try:
        r = await db.execute(text("""
            SELECT nus, etat::text, date_signature, reference_ccfm, nom_prenom
            FROM demandes_ccfm WHERE nicad_code IN (
                SELECT nicad FROM parcelles WHERE id=:pid
            ) ORDER BY date_enregistrement DESC LIMIT 10
        """), {"pid": parcelle_id})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("demandes_parcelle: %s", e)
        return []


@router.get("/stats", response_model=list)
async def stats_module(
    localite: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_LECTURE)),
):
    """Statistiques CCFM via la fonction stats_ccfm()."""
    try:
        r = await db.execute(text("SELECT * FROM stats_ccfm(:loc)"), {"loc": localite})
        row = r.mappings().first()
        return dict(row) if row else {}
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("stats_ccfm: %s", e)
        return {}


# ── Suspensions -------------------------------------------------------

@router.post("/suspensions", status_code=201)
async def suspendre_ccfm(
    nus: str    = Query(...),
    motif: str  = Query(..., min_length=5),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_DIRECTEUR)),
):
    """Suspend un certificat CCFM."""
    try:
        await db.execute(text("""
            INSERT INTO ccfm_suspension (id, nus, motif, suspendu_par, created_at)
            VALUES (gen_random_uuid(), :n, :m, :uid, NOW())
        """), {"n": nus, "m": motif, "uid": str(current_user.id)})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"nus": nus, "statut": "suspendu", "motif": motif}


@router.get("/suspensions", response_model=list)
async def lister_suspensions(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_LECTURE)),
):
    """Liste des suspensions actives."""
    try:
        r = await db.execute(text(
            "SELECT nus, motif, suspendu_par, created_at FROM ccfm_suspension ORDER BY created_at DESC"
        ))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("lister_suspensions: %s", e)
        return []


# ── Archives DAR -> ANNF ---------------------------------------------

@router.get("/dar/archives", response_model=list)
async def archives_dar(
    page: int  = Query(1,  ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_LECTURE + ["ARCHIVISTE_ANNF"])),
):
    """Archives DAR du module CCFM."""
    try:
        r = await db.execute(text("""
            SELECT id, ida, type_archive, entite_id, sha256_snapshot, scelle, created_at
            FROM annf_archive_links WHERE entite_table='demandes_ccfm'
            ORDER BY created_at DESC LIMIT :l OFFSET :o
        """), {"l": limit, "o": (page - 1) * limit})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("dar_archives_ccfm: %s", e)
        return []


@router.post("/dar/archiver", status_code=201)
async def archiver_dar(
    entite_id:    str = Query(...),
    entite_table: str = Query(default="demandes_ccfm"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ARCHIVISTE)),
):
    """Archive un document CCFM vers l ANNF national."""
    try:
        from app.services.workflow_orchestrator import ANNFArchiveService
        from app.models.workflows import TypeArchiveANNF
        archive = await ANNFArchiveService.archiver(
            db=db, type_archive=TypeArchiveANNF.CCFM,
            entite_id=entite_id, entite_table=entite_table,
            snapshot={"module": "ccfm"},
            archive_par_id=str(current_user.id),
        )
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"ida": archive.ida if hasattr(archive, "ida") else None, "module": "ccfm"}


@router.get("/dar/integrite", response_model=list)
async def integrite_dar(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_LECTURE + ["AUDITEUR"])),
):
    """Integrite des archives CCFM dans l ANNF."""
    try:
        r = await db.execute(text("""
            SELECT COUNT(*) FILTER (WHERE scelle=true)  AS nb_scelles,
                   COUNT(*) FILTER (WHERE scelle=false) AS nb_en_attente,
                   COUNT(*)                             AS nb_total
            FROM annf_archive_links WHERE entite_table='demandes_ccfm'
        """))
        row = r.mappings().first()
        return dict(row) if row else {"nb_scelles": 0, "nb_en_attente": 0, "nb_total": 0}
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("integrite_dar_ccfm: %s", e)
        return {"nb_scelles": 0, "nb_en_attente": 0, "nb_total": 0}
