"""
FONCIER+ v1.0.1 — CCFMVerificationService
Service partagé pour la vérification CCFM par NUS ou QR CODE.
Utilisé par : CCFM, Commune, Domaine, Justice, Banque, Notaire.

Formats QR CODE acceptés :
  1. URL directe  : https://foncier.gov.ne/verify/CCF-2026-0001234
  2. JSON         : {"nus":"CCF-2026-0001234","url":"..."}
  3. Pipe         : CCFM|CCF-2026-0001234|https://...
  4. NUS brut     : CCF-2026-0001234
"""
import json
import re
import hashlib
from typing import Optional, Tuple
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# Regex NUS officiel : CCF-YYYY-NNNNNNN
_NUS_PATTERN = re.compile(r'(?:NUS|CCF)-\d{4}-\d{5,7}', re.IGNORECASE)


def extraire_nus_depuis_qr(qr_data: str) -> Optional[str]:
    """Extrait le NUS depuis une donnée QR CODE dans les 4 formats supportés."""
    if not qr_data or not qr_data.strip():
        return None

    qr = qr_data.strip()

    # Format 4 : NUS brut direct
    if _NUS_PATTERN.fullmatch(qr.upper()):
        return qr.upper()

    # Format 1 : URL directe
    # https://foncier.gov.ne/verify/CCF-2026-0001234
    m = re.search(r'/verify/?(CCF-\d{4}-\d{7})', qr, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    # Format 2 : JSON {"nus":"CCF-..."}
    try:
        obj = json.loads(qr)
        nus_json = obj.get("nus") or obj.get("NUS") or obj.get("certificat")
        if nus_json and _NUS_PATTERN.fullmatch(str(nus_json).upper()):
            return str(nus_json).upper()
    except (json.JSONDecodeError, TypeError):
        pass

    # Format 3 : Pipe CCFM|CCF-2026-0001234|...
    if "|" in qr:
        parts = qr.split("|")
        for part in parts:
            m2 = _NUS_PATTERN.search(part.strip().upper())
            if m2:
                return m2.group(0)

    # Fallback : chercher un NUS n'importe où dans la chaîne
    m3 = _NUS_PATTERN.search(qr.upper())
    if m3:
        return m3.group(0)

    return None


async def verifier_ccfm(
    db: AsyncSession,
    nus: Optional[str] = None,
    qr_code: Optional[str] = None,
    parcelle_id: Optional[str] = None,
    exiger_statut_signe: bool = True,
) -> dict:
    """
    Vérifie un certificat CCFM par NUS ou QR CODE.

    Args:
        db: Session base de données
        nus: Numéro NUS direct (CCF-YYYY-NNNNNNN)
        qr_code: Données QR scannées (4 formats supportés)
        parcelle_id: UUID parcelle (vérifie le CCFM le plus récent signé)
        exiger_statut_signe: Si True, refuse les CCFM non signés

    Returns:
        dict avec : valide, nus, statut, sha256, demandeur_nom, nicad,
                    parcelle_id, qr_code_url, date_signature, message
    """
    # Résolution du NUS
    nus_resolu = None

    if nus:
        nus_resolu = nus.upper().strip()
    elif qr_code:
        nus_resolu = extraire_nus_depuis_qr(qr_code)
        if not nus_resolu:
            return {
                "valide": False,
                "nus": None,
                "message": (
                    "QR CODE non reconnu. "
                    "Formats acceptés : URL /verify/CCF-..., "
                    "JSON {nus:...}, Pipe CCF|NUS|..., ou NUS brut CCF-AAAA-NNNNNNN"
                ),
                "format_qr_recu": qr_code[:100] if qr_code else None,
            }
    elif parcelle_id:
        # Vérifier le CCFM le plus récent signé sur la parcelle
        try:
            r = await db.execute(text("""
                SELECT nus FROM demandes_ccfm
                WHERE parcelle_id=:pid AND statut='signe'
                ORDER BY created_at DESC LIMIT 1
            """), {"pid": parcelle_id})
            row = r.first()
            if row:
                nus_resolu = row[0]
            else:
                return {
                    "valide": False,
                    "nus": None,
                    "parcelle_id": parcelle_id,
                    "message": "Aucun certificat CCFM signé trouvé pour cette parcelle",
                }
        except Exception as e:
            return {"valide": False, "nus": None, "message": str(e)[:200]}
    else:
        return {"valide": False, "nus": None, "message": "Fournir nus, qr_code ou parcelle_id"}

    if not nus_resolu:
        return {"valide": False, "nus": None, "message": "NUS invalide"}

    # Validation format NUS
    if not _NUS_PATTERN.fullmatch(nus_resolu):
        return {
            "valide": False,
            "nus": nus_resolu,
            "message": f"Format NUS invalide : {nus_resolu}. Attendu : CCF-AAAA-NNNNNNN",
        }

    # Interrogation base de données
    try:
        r = await db.execute(text("""
            SELECT
                dc.id,
                dc.nus,
                dc.statut,
                dc.demandeur_nom,
                dc.demandeur_cni,
                dc.parcelle_id,
                dc.signe_par,
                dc.created_at,
                dc.updated_at,
                p.nicad,
                p.surface_m2,
                p.is_gele
            FROM demandes_ccfm dc
            LEFT JOIN parcelles p ON p.id = dc.parcelle_id
            WHERE dc.nus = :nus
        """), {"nus": nus_resolu})
        row = r.mappings().first()
    except Exception as e:
        return {"valide": False, "nus": nus_resolu, "message": str(e)[:200]}

    if not row:
        return {
            "valide": False,
            "nus": nus_resolu,
            "message": f"Certificat {nus_resolu} introuvable dans la base",
        }

    statut      = row["statut"]
    est_signe   = (statut == "signe")
    est_valide  = est_signe or (not exiger_statut_signe)
    est_gele    = bool(row.get("is_gele"))

    # SHA-256 de vérification (hash NUS + parcelle + statut)
    payload_sha = f"{nus_resolu}|{row.get('parcelle_id','')}|{statut}"
    sha256_verif = hashlib.sha256(payload_sha.encode()).hexdigest()

    # URL QR Code
    qr_url = f"https://foncier.gov.ne/verify/{nus_resolu}"

    # Construire le message
    if est_signe and not est_gele:
        message = "Certificat CCFM valide et signé"
    elif est_signe and est_gele:
        message = "Certificat CCFM signé mais parcelle gelée (litige ou saisie)"
    elif statut in ("conteste", "suspendu"):
        message = f"Certificat CCFM contesté — statut : {statut}"
    else:
        message = f"Certificat CCFM non signé — statut actuel : {statut}"

    return {
        "valide":        est_valide and not est_gele,
        "nus":           nus_resolu,
        "statut":        statut,
        "est_signe":     est_signe,
        "parcelle_gele": est_gele,
        "demandeur_nom": row.get("demandeur_nom"),
        "nicad":         row.get("nicad"),
        "parcelle_id":   str(row.get("parcelle_id")) if row.get("parcelle_id") else None,
        "surface_m2":    row.get("surface_m2"),
        "sha256_verif":  sha256_verif,
        "qr_code_url":   qr_url,
        "date_creation": row["created_at"].isoformat() if row.get("created_at") else None,
        "message":       message,
        "source_qr":     bool(qr_code),
        "nus_extrait_de_qr": nus_resolu if qr_code else None,
    }


async def exiger_ccfm_valide(
    db: AsyncSession,
    parcelle_id: str,
    contexte: str = "opération",
) -> Tuple[bool, str]:
    """
    Gate CCFM : vérifie qu'une parcelle a un CCFM signé et non gelé.
    À appeler en début de toute action transactionnelle.

    Returns:
        (ok: bool, message: str)
    """
    result = await verifier_ccfm(
        db=db,
        parcelle_id=parcelle_id,
        exiger_statut_signe=True,
    )
    if result["valide"]:
        return True, f"CCFM valide : {result['nus']}"
    return False, (
        f"CCFM gate échoué pour {contexte} : {result['message']}. "
        f"Un certificat CCFM signé est obligatoire avant toute "
        f"transaction foncière (commune, domaine, justice, banque, notaire)."
    )
