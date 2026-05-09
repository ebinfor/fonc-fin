"""
FONCIER+ v3 — Service CCFM
Certificat de Conformité Foncière Mixte

Workflow officiel 7 étapes :
  1. GUICHETIÈRE    → enregistrer_demande()        → PDF formulaire A4 généré
  2. SYSTÈME AUTO   → lancer_verification_auto()   → Ticket de réponse (GPS + RNAF + BGU)
  3. SECRÉTAIRE     → emettre_ordre_mission()       → ticket joint automatiquement
  4. TOPOGRAPHE     → deposer_fiche_constat()       → Fiche de Vérification Administrative Foncière
  5. SYSTÈME AUTO   → generer_rapport_appreciation() → compile ticket + fiche → rapport Directeur
  6. DIRECTEUR      → valider_et_signer()           → signe + génère PDF certificat A5
  7. ARCHIVISTE     → archiver()                    → RETRAIT_EN_ATTENTE → ANNF + blockchain

Intégration PDF (ccfm_pdf_generator.py) :
  - Étape 1 : generer_demande_ccfm()     → PDF formulaire A4 (pdf_demande_url)
  - Étape 6 : generer_certificat_ccfm() → PDF certificat A5 (pdf_url)
  Résolution armoiries : chemin relatif au script → variable d'env → fallback vectoriel
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

MONTANT_CCFM = Decimal("50000")  # 50 000 FCFA forfaitaire

# ── Résolution chemin armoiries ─────────────────────────────────────────────
_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
_ARMOIRIES_DEFAULT = os.path.join(_SERVICE_DIR, "armoiries_niger.png")


def _get_armoiries_path() -> str | None:
    """
    Résolution prioritaire :
      1. Variable d'environnement CCFM_ARMOIRIES_PATH
      2. armoiries_niger.png dans le même dossier que ce fichier
      3. None → fallback vectoriel dans le générateur PDF
    """
    env = os.environ.get("CCFM_ARMOIRIES_PATH")
    if env and os.path.exists(env):
        return env
    if os.path.exists(_ARMOIRIES_DEFAULT):
        return _ARMOIRIES_DEFAULT
    return None


def _get_pdf_dir() -> str:
    """Dossier de sortie des PDFs — variable d'env ou /tmp/ccfm_pdfs."""
    d = os.environ.get("CCFM_PDF_DIR", "/tmp/ccfm_pdfs")
    os.makedirs(d, exist_ok=True)
    return d


def _nus_safe(nus: str) -> str:
    """NUS utilisable dans un nom de fichier."""
    return str(nus).replace("/", "-").replace(" ", "_")


class CCFMWorkflowService:
    def __init__(self, pool, cache=None):
        self.pool  = pool
        self.cache = cache

    # ═══════════════════════════════════════════════════════════════════════
    # ÉTAPE 1 — GUICHETIÈRE : Enregistrement demande + PDF formulaire A4
    # ═══════════════════════════════════════════════════════════════════════

    async def enregistrer_demande(self, data: dict, guichetiere_id: str = None) -> dict:
        """
        La guichetière reçoit et enregistre la demande CCFM.
        Paiement 50 000 FCFA obligatoire. NUS généré automatiquement.
        Génère le PDF du formulaire de demande A4 (remis au demandeur).
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO demandes_ccfm
                   (nom_prenom, nip_passeport, date_naissance, lieu_naissance,
                    nationalite, adresse, telephone, email,
                    localite, lot, parcelle, superficie_m2,
                    numero_arrete, numero_titre_foncier, nicad_code,
                    mode_paiement, reference_paiement, montant_paye,
                    enregistre_par, date_paiement)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
                           $16::mode_paiement_ccfm,$17,$18,$19,NOW())
                   RETURNING ccfm_id, nus, etat::TEXT, montant_paye""",
                data["nom_prenom"],    data["nip_passeport"],
                data["date_naissance"], data["lieu_naissance"],
                data.get("nationalite", "NE"), data["adresse"],
                data["telephone"],     data.get("email"),
                data["localite"],      data["lot"],
                data["parcelle"],      data["superficie_m2"],
                data.get("numero_arrete"),
                data.get("numero_titre_foncier"),
                data.get("nicad_code"),
                data["mode_paiement"], data["reference_paiement"],
                MONTANT_CCFM,          guichetiere_id,
            )
            result = dict(row)

        # ── Génération PDF formulaire A4 ──────────────────────────────────
        pdf_demande_url = await self._generer_pdf_demande(data, result["nus"])
        if pdf_demande_url:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE demandes_ccfm SET pdf_demande_url = $2 WHERE ccfm_id = $1",
                    result["ccfm_id"], pdf_demande_url,
                )
            result["pdf_demande_url"] = pdf_demande_url

        return result

    async def _generer_pdf_demande(self, data: dict, nus: str) -> str | None:
        """
        Génère le PDF du formulaire de demande CCFM (A4).
        Retourne le chemin du fichier généré, ou None en cas d'échec.
        """
        try:
            from .ccfm_pdf_generator import generer_demande_ccfm

            pdf_data = {
                "nus":                 str(nus),
                "nom_prenom":          data.get("nom_prenom", ""),
                "nip_passeport":       data.get("nip_passeport", ""),
                "date_naissance":      data.get("date_naissance", ""),
                "lieu_naissance":      data.get("lieu_naissance", ""),
                "nationalite":         data.get("nationalite", "NE"),
                "adresse":             data.get("adresse", ""),
                "telephone":           data.get("telephone", ""),
                "email":               data.get("email", ""),
                "localite":            data.get("localite", ""),
                "lot":                 data.get("lot", ""),
                "parcelle":            data.get("parcelle", ""),
                "superficie_m2":       str(data.get("superficie_m2", "")),
                "latitude":            str(data.get("latitude", "")),
                "longitude":           str(data.get("longitude", "")),
                "reference_paiement":  data.get("reference_paiement", ""),
                "mode_paiement":       data.get("mode_paiement", ""),
            }

            out_path = os.path.join(_get_pdf_dir(), f"CCFM_DEMANDE_{_nus_safe(nus)}.pdf")
            generer_demande_ccfm(pdf_data, out_path, armoiries_path=_get_armoiries_path())
            return out_path

        except Exception as e:
            # Non bloquant : la demande est créée même si le PDF échoue
            print(f"[CCFM] Avertissement — PDF formulaire non généré pour {nus} : {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════════
    # ÉTAPE 2 — SYSTÈME : Vérification RNAF + BGU → Ticket de réponse
    # ═══════════════════════════════════════════════════════════════════════

    async def lancer_verification_auto(self, ccfm_id: str) -> dict:
        """
        Vérification administrative automatique :
          - RNAF : Registre National des Arrêtés Fonciers
          - BGU  : Base Géospatiale Unique v4.0.0 → extraction coordonnées GPS parcelle
        Génère le Ticket de Réponse (RNAF + BGU + GPS) transmis à la secrétaire.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM demandes_ccfm WHERE ccfm_id = $1", ccfm_id)
            if not row:
                raise ValueError("Demande CCFM non trouvée")
            if row["etat"] != "DEMANDE_RECUE":
                raise ValueError(f"Gate étape 2 : état actuel={row['etat']}, attendu=DEMANDE_RECUE")

            await conn.execute(
                "UPDATE demandes_ccfm SET etat = 'VERIFICATION_AUTO'::etat_ccfm, "
                "date_verification_auto = NOW() WHERE ccfm_id = $1",
                ccfm_id,
            )

            anomalies_rnaf, anomalies_bgu = [], []
            gps_lat, gps_lon = None, None

            # ── Vérification RNAF ──────────────────────────────────────────
            if row["numero_arrete"]:
                arrete = await conn.fetchrow(
                    "SELECT arrete_id, statut FROM registre_arretes WHERE numero_arrete = $1",
                    row["numero_arrete"],
                )
                if not arrete:
                    anomalies_rnaf.append("Arrêté introuvable dans le RNAF")
                elif arrete["statut"] not in ("SIGNE", "PUBLIE"):
                    anomalies_rnaf.append(f"Arrêté non valide (statut: {arrete['statut']})")

            # ── Vérification BGU + extraction GPS ──────────────────────────
            bgu_row = await conn.fetchrow(
                """SELECT entite_id,
                          ST_Y(geom::geometry) AS lat,
                          ST_X(geom::geometry) AS lon
                   FROM entites_bgu
                   WHERE proprietes->>'lot' = $1
                     AND proprietes->>'parcelle' = $2
                   LIMIT 1""",
                row["lot"], row["parcelle"],
            )
            bgu_ok = bgu_row is not None
            if bgu_ok:
                gps_lat = bgu_row["lat"]
                gps_lon = bgu_row["lon"]
            else:
                anomalies_bgu.append("Parcelle non trouvée dans la BGU")

            rnaf_ok     = len(anomalies_rnaf) == 0
            tout_ok     = rnaf_ok and bgu_ok
            nouvel_etat = "TICKET_EMIS" if tout_ok else "VERIFICATION_AUTO_KO"

            ticket_hash = hashlib.sha256(
                json.dumps({
                    "ccfm_id": ccfm_id,
                    "rnaf_ok": rnaf_ok,
                    "bgu_ok":  bgu_ok,
                    "lat":     str(gps_lat),
                    "lon":     str(gps_lon),
                    "date":    str(datetime.now(timezone.utc).date()),
                }, sort_keys=True).encode()
            ).hexdigest()

            await conn.execute(
                """UPDATE demandes_ccfm SET
                   etat = $2::etat_ccfm,
                   rnaf_conforme = $3, rnaf_anomalies = $4,
                   bgu_conforme = $5,  bgu_anomalies = $6,
                   latitude = $7, longitude = $8,
                   ticket_hash = $9, date_ticket_emis = NOW()
                   WHERE ccfm_id = $1""",
                ccfm_id, nouvel_etat,
                rnaf_ok, "; ".join(anomalies_rnaf) or None,
                bgu_ok,  "; ".join(anomalies_bgu)  or None,
                gps_lat, gps_lon,
                ticket_hash,
            )
            return {
                "ccfm_id": ccfm_id,
                "etat":    nouvel_etat,
                "ticket": {
                    "rnaf_conforme": rnaf_ok,
                    "bgu_conforme":  bgu_ok,
                    "gps_lat":       gps_lat,
                    "gps_lon":       gps_lon,
                    "ticket_hash":   ticket_hash,
                    "anomalies":     anomalies_rnaf + anomalies_bgu,
                },
            }

    # ═══════════════════════════════════════════════════════════════════════
    # ÉTAPE 3 — SECRÉTAIRE : Ordre de mission (ticket joint automatiquement)
    # ═══════════════════════════════════════════════════════════════════════

    async def emettre_ordre_mission(self, data: dict, secretaire_id: str = None) -> dict:
        """
        Après ticket OK, la secrétaire émet l'ordre de mission au topographe.
        Le ticket RNAF/BGU/GPS est joint automatiquement à l'ordre de mission.
        """
        ccfm_id = data["ccfm_id"]
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM demandes_ccfm WHERE ccfm_id = $1", ccfm_id)
            if not row:
                raise ValueError("Demande CCFM non trouvée")
            if row["etat"] != "TICKET_EMIS":
                raise ValueError(f"Gate étape 3 : état actuel={row['etat']}, attendu=TICKET_EMIS")

            ticket_joint = {
                "rnaf_conforme":  row["rnaf_conforme"],
                "bgu_conforme":   row["bgu_conforme"],
                "gps_lat":        str(row["latitude"])  if row["latitude"]  else None,
                "gps_lon":        str(row["longitude"]) if row["longitude"] else None,
                "ticket_hash":    row.get("ticket_hash"),
                "rnaf_anomalies": row["rnaf_anomalies"],
                "bgu_anomalies":  row["bgu_anomalies"],
            }

            seq = await conn.fetchval(
                "SELECT COUNT(*) + 1 FROM ordres_mission_ccfm "
                "WHERE EXTRACT(YEAR FROM date_emission) = $1",
                datetime.now(timezone.utc).year,
            )
            numero_ordre = f"OM-{datetime.now(timezone.utc).year}-{seq:05d}"

            await conn.execute(
                """INSERT INTO ordres_mission_ccfm
                   (ccfm_id, numero_ordre, type_mission, destinataire_id, destinataire_nom,
                    date_prevue_visite, instructions, ticket_joint, emis_par)
                   VALUES ($1,$2,'TOPOGRAPHE',$3,$4,$5,$6,$7::jsonb,$8)""",
                ccfm_id, numero_ordre,
                data.get("topographe_id"), data.get("topographe_nom"),
                data.get("date_prevue_visite"), data.get("instructions"),
                json.dumps(ticket_joint), secretaire_id,
            )
            await conn.execute(
                "UPDATE demandes_ccfm SET etat = 'ORDRE_MISSION_EMIS'::etat_ccfm, "
                "topographe_assigne = $2 WHERE ccfm_id = $1",
                ccfm_id, data.get("topographe_id"),
            )
            return {
                "ccfm_id":      ccfm_id,
                "etat":         "ORDRE_MISSION_EMIS",
                "numero_ordre": numero_ordre,
                "ticket_joint": ticket_joint,
            }

    # ═══════════════════════════════════════════════════════════════════════
    # ÉTAPE 4 — TOPOGRAPHE : Fiche de Vérification Administrative Foncière
    # ═══════════════════════════════════════════════════════════════════════

    async def demarrer_visite(self, ccfm_id: str) -> dict:
        """Le topographe signale le début de la visite terrain."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT etat FROM demandes_ccfm WHERE ccfm_id = $1", ccfm_id)
            if not row or row["etat"] not in ("ORDRE_MISSION_EMIS", "ATTENTE_VISITE_TERRAIN"):
                raise ValueError(f"Gate étape 4 : état actuel={row['etat'] if row else 'N/A'}")
            await conn.execute(
                "UPDATE demandes_ccfm SET etat = 'VISITE_EN_COURS'::etat_ccfm, "
                "date_visite_terrain = NOW() WHERE ccfm_id = $1",
                ccfm_id,
            )
            return {"ccfm_id": ccfm_id, "etat": "VISITE_EN_COURS"}

    async def deposer_fiche_constat(self, data: dict) -> dict:
        """
        Le topographe dépose la Fiche de Vérification Administrative Foncière.
        §3 : gps_mesure_latitude/longitude comparé au GPS du ticket (étape 2).
        §7 : decision = AUTORISATION ou REFUS.
        Si REFUS → dossier rejeté. Si AUTORISATION → étape 5 (rapport auto).
        """
        ccfm_id = data["ccfm_id"]
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM demandes_ccfm WHERE ccfm_id = $1", ccfm_id)
            if not row:
                raise ValueError("Demande CCFM non trouvée")
            if row["etat"] not in ("ORDRE_MISSION_EMIS", "ATTENTE_VISITE_TERRAIN", "VISITE_EN_COURS"):
                raise ValueError(f"Gate étape 4 : état actuel={row['etat']}")

            # Calcul écart superficie
            ecart_pct = None
            if row["superficie_m2"] and data.get("superficie_mesuree_m2"):
                ecart_pct = round(
                    abs(float(data["superficie_mesuree_m2"]) - float(row["superficie_m2"]))
                    / float(row["superficie_m2"]) * 100, 2
                )

            # Concordance GPS : ticket vs mesuré terrain
            concordance_gps = None
            if row.get("latitude") and data.get("gps_mesure_latitude"):
                ecart_gps = abs(float(data["gps_mesure_latitude"]) - float(row["latitude"]))
                concordance_gps = ecart_gps < 0.001  # ~100 m de tolérance

            hash_fiche = hashlib.sha256(json.dumps({
                "ccfm_id":    ccfm_id,
                "decision":   data["decision"],
                "superficie": str(data.get("superficie_mesuree_m2")),
                "jo_publie":  data.get("jo_publie", False),
                "gps_lat":    str(data.get("gps_mesure_latitude")),
            }, sort_keys=True).encode()).hexdigest()

            await conn.execute(
                """INSERT INTO fiches_constat_ccfm
                   (ccfm_id, objet_demande,
                    arrete_numero, arrete_date_signature, arrete_autorite, jo_publie, jo_reference,
                    parcelle_ref_titre, superficie_mesuree_m2,
                    gps_mesure_latitude, gps_mesure_longitude,
                    parcelle_conforme, concordance_gps, ecart_superficie_pct,
                    titre_foncier_numero, titre_foncier_emetteur, titre_authentique,
                    observations, decision, motif_refus,
                    topographe_nom, topographe_fonction, topographe_id,
                    photos_terrain, hash_fiche)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24::jsonb,$25)""",
                ccfm_id, data.get("objet_demande"),
                data.get("arrete_numero"), data.get("arrete_date_signature"),
                data.get("arrete_autorite"), data.get("jo_publie", False), data.get("jo_reference"),
                data.get("parcelle_ref_titre"), data.get("superficie_mesuree_m2"),
                data.get("gps_mesure_latitude"), data.get("gps_mesure_longitude"),
                data.get("parcelle_conforme"), concordance_gps, ecart_pct,
                data.get("titre_foncier_numero"), data.get("titre_foncier_emetteur"),
                data.get("titre_authentique"),
                data.get("observations"), data["decision"], data.get("motif_refus"),
                data["topographe_nom"], data.get("topographe_fonction", "Topographe agréé"),
                data.get("topographe_id"),
                json.dumps(data.get("photos_terrain", [])), hash_fiche,
            )

            if data["decision"] == "AUTORISATION":
                nouvel_etat = "FICHE_CONSTAT_DEPOSEE"
            else:
                nouvel_etat = "REJETE"
                await conn.execute(
                    "UPDATE demandes_ccfm SET motif_rejet = 'AUTRE'::motif_rejet_ccfm, "
                    "observations_rejet = $2 WHERE ccfm_id = $1",
                    ccfm_id, data.get("motif_refus", "Refus topographe — fiche de constat"),
                )

            await conn.execute(
                "UPDATE demandes_ccfm SET etat = $2::etat_ccfm, "
                "date_rapport_topographe = NOW() WHERE ccfm_id = $1",
                ccfm_id, nouvel_etat,
            )
            return {
                "ccfm_id":         ccfm_id,
                "etat":            nouvel_etat,
                "hash_fiche":      hash_fiche,
                "ecart_pct":       ecart_pct,
                "concordance_gps": concordance_gps,
            }

    # ═══════════════════════════════════════════════════════════════════════
    # ÉTAPE 5 — SYSTÈME : Rapport d'appréciation (ticket + fiche → Directeur)
    # ═══════════════════════════════════════════════════════════════════════

    async def generer_rapport_appreciation(self, ccfm_id: str) -> dict:
        """
        Compilation automatique :
          - Ticket étape 2 : RNAF, BGU, GPS officiel parcelle
          - Fiche étape 4  : GPS mesuré, superficie mesurée, §7 décision topographe
        → Rapport d'appréciation transmis au Directeur.
        """
        async with self.pool.acquire() as conn:
            demande = await conn.fetchrow("SELECT * FROM demandes_ccfm WHERE ccfm_id = $1", ccfm_id)
            if not demande:
                raise ValueError("Demande CCFM non trouvée")
            if demande["etat"] != "FICHE_CONSTAT_DEPOSEE":
                raise ValueError(
                    f"Gate étape 5 : état actuel={demande['etat']}, attendu=FICHE_CONSTAT_DEPOSEE"
                )

            fiche = await conn.fetchrow(
                "SELECT * FROM fiches_constat_ccfm WHERE ccfm_id = $1 ORDER BY created_at DESC LIMIT 1",
                ccfm_id,
            )
            if not fiche:
                raise ValueError("Fiche de constat introuvable")

            # ── Points de conformité et anomalies ─────────────────────────
            points_conformite, anomalies = [], []

            if demande["rnaf_conforme"]:
                points_conformite.append("RNAF : arrêté foncier conforme et enregistré")
            else:
                anomalies.append(f"RNAF : {demande['rnaf_anomalies'] or 'non conforme'}")

            if demande["bgu_conforme"]:
                points_conformite.append("BGU : parcelle enregistrée et géométrie valide")
            else:
                anomalies.append(f"BGU : {demande['bgu_anomalies'] or 'parcelle introuvable'}")

            if fiche["concordance_gps"] is True:
                points_conformite.append(
                    f"GPS : ticket ({demande['latitude']}, {demande['longitude']}) ↔ terrain concordants"
                )
            elif fiche["concordance_gps"] is False:
                anomalies.append(
                    f"GPS : écart ticket vs terrain "
                    f"(ticket: {demande['latitude']},{demande['longitude']} | "
                    f"mesuré: {fiche['gps_mesure_latitude']},{fiche['gps_mesure_longitude']})"
                )

            if fiche["jo_publie"]:
                points_conformite.append("Arrêté de lotissement publié au Journal Officiel")
            else:
                anomalies.append("Arrêté non publié au Journal Officiel")

            if fiche["ecart_superficie_pct"] is not None:
                if fiche["ecart_superficie_pct"] <= 2.0:
                    points_conformite.append(
                        f"Superficie concordante (écart {fiche['ecart_superficie_pct']}%)"
                    )
                elif fiche["ecart_superficie_pct"] <= 5.0:
                    anomalies.append(f"Écart superficie à vérifier : {fiche['ecart_superficie_pct']}%")
                else:
                    anomalies.append(f"Écart superficie significatif : {fiche['ecart_superficie_pct']}%")

            if fiche["titre_authentique"] is True:
                points_conformite.append("Titre foncier authentifié aux archives domaniales")
            elif fiche["titre_authentique"] is False:
                anomalies.append("Titre foncier contestable")

            # ── Verdict global ────────────────────────────────────────────
            if len(anomalies) == 0:
                verdict = "CONFORME"
            elif len(anomalies) <= 1 and len(points_conformite) >= 3:
                verdict = "A_VERIFIER"
            else:
                verdict = "NON_CONFORME"

            rapport_hash = hashlib.sha256(json.dumps({
                "ccfm_id":   ccfm_id,
                "verdict":   verdict,
                "conformite": points_conformite,
                "anomalies": anomalies,
                "date":      str(datetime.now(timezone.utc).date()),
            }, sort_keys=True).encode()).hexdigest()

            await conn.execute(
                """INSERT INTO rapports_appreciation_ccfm
                   (ccfm_id,
                    rnaf_conforme, bgu_conforme, gps_ticket_lat, gps_ticket_lon,
                    superficie_mesuree_m2, ecart_superficie_pct,
                    gps_mesure_lat, gps_mesure_lon, concordance_gps,
                    jo_publie, titre_authentique,
                    decision_topographe, observations_topographe,
                    verdict_global, points_conformite, anomalies_detectees, rapport_hash)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::verdict_ccfm,$16::jsonb,$17::jsonb,$18)""",
                ccfm_id,
                demande["rnaf_conforme"], demande["bgu_conforme"],
                demande["latitude"], demande["longitude"],
                fiche["superficie_mesuree_m2"], fiche["ecart_superficie_pct"],
                fiche["gps_mesure_latitude"], fiche["gps_mesure_longitude"], fiche["concordance_gps"],
                fiche["jo_publie"], fiche["titre_authentique"],
                fiche["decision"], fiche["observations"],
                verdict,
                json.dumps(points_conformite), json.dumps(anomalies),
                rapport_hash,
            )
            await conn.execute(
                "UPDATE demandes_ccfm SET etat = 'ATTENTE_SIGNATURE_DIRECTEUR'::etat_ccfm, "
                "date_rapport_apprec = NOW() WHERE ccfm_id = $1",
                ccfm_id,
            )
            return {
                "ccfm_id":           ccfm_id,
                "etat":              "ATTENTE_SIGNATURE_DIRECTEUR",
                "verdict":           verdict,
                "points_conformite": points_conformite,
                "anomalies":         anomalies,
                "rapport_hash":      rapport_hash,
            }

    # ═══════════════════════════════════════════════════════════════════════
    # ÉTAPE 6 — DIRECTEUR : Consultation rapport + signature + PDF certificat A5
    # ═══════════════════════════════════════════════════════════════════════

    async def valider_et_signer(self, data: dict, directeur_id: str = None) -> dict:
        """
        Le Directeur consulte le rapport d'appréciation (ticket + fiche),
        puis signe → génère hash SHA-256 + QR Code + PDF certificat A5.
        """
        ccfm_id = data["ccfm_id"]
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM demandes_ccfm WHERE ccfm_id = $1", ccfm_id)
            if not row:
                raise ValueError("Demande CCFM non trouvée")
            if row["etat"] != "ATTENTE_SIGNATURE_DIRECTEUR":
                raise ValueError(
                    f"Gate étape 6 : état actuel={row['etat']}, attendu=ATTENTE_SIGNATURE_DIRECTEUR"
                )

            if data["approuve"]:
                payload = json.dumps({
                    "ccfm_id":      ccfm_id,
                    "nus":          row["nus"],
                    "beneficiaire": row["nom_prenom"],
                    "parcelle":     f"{row['lot']}/{row['parcelle']}",
                    "date":         str(datetime.now(timezone.utc).date()),
                }, sort_keys=True)
                h   = hashlib.sha256(payload.encode()).hexdigest()
                qr  = f"CCFM|{row['nus']}|https://foncier.ne/verify/{row['nus']}"
                pdf = await self._generer_pdf_certificat(row, h, qr)

                await conn.execute(
                    """UPDATE demandes_ccfm SET
                       etat = 'SIGNE_DIRECTEUR'::etat_ccfm,
                       directeur_validateur = $2, date_validation_directeur = NOW(),
                       date_signature = NOW(), hash_ccfm = $3, qr_code = $4,
                       signature_directeur = $5, pdf_url = $6
                       WHERE ccfm_id = $1""",
                    ccfm_id, directeur_id, h, qr, data.get("observations"), pdf,
                )
                return {
                    "ccfm_id": ccfm_id,
                    "etat":    "SIGNE_DIRECTEUR",
                    "hash":    h,
                    "pdf_url": pdf,
                    "qr_code": qr,
                }
            else:
                if not data.get("motif_rejet"):
                    raise ValueError("Gate : motif_rejet obligatoire si rejeté")
                await conn.execute(
                    """UPDATE demandes_ccfm SET
                       etat = 'REJETE'::etat_ccfm,
                       directeur_validateur = $2, date_validation_directeur = NOW(),
                       motif_rejet = $3::motif_rejet_ccfm, observations_rejet = $4
                       WHERE ccfm_id = $1""",
                    ccfm_id, directeur_id, data["motif_rejet"], data.get("observations"),
                )
                return {"ccfm_id": ccfm_id, "etat": "REJETE", "motif": data["motif_rejet"]}

    async def _generer_pdf_certificat(self, row, hash_ccfm: str, qr_data: str) -> str | None:
        """
        Génère le PDF du certificat CCFM signé (A5 portrait).
        Récupère automatiquement le nom du topographe et de l'huissier.
        Retourne le chemin du fichier, ou None en cas d'échec.
        """
        try:
            from .ccfm_pdf_generator import generer_certificat_ccfm

            async with self.pool.acquire() as conn:
                fiche = await conn.fetchrow(
                    """SELECT fc.topographe_nom,
                              om.destinataire_nom AS huissier_nom
                       FROM fiches_constat_ccfm fc
                       LEFT JOIN ordres_mission_ccfm om
                         ON om.ccfm_id = fc.ccfm_id AND om.type_mission = 'HUISSIER'
                       WHERE fc.ccfm_id = $1
                       ORDER BY fc.created_at DESC LIMIT 1""",
                    str(row["ccfm_id"]),
                )

            nus    = str(row["nus"])
            qr_url = f"https://foncier.ne/verify/{nus}"

            # Référence CCFM — générée par trigger SQL sur SIGNE_DIRECTEUR
            reference = row.get("reference_ccfm") or f"CCFM/{datetime.now(timezone.utc).year}/{nus}"

            pdf_data = {
                "reference_ccfm": reference,
                "date_signature": datetime.now(timezone.utc),
                "nom_prenom":     row["nom_prenom"],
                "nip_passeport":  row["nip_passeport"],
                "date_naissance": str(row.get("date_naissance", "")),
                "lieu_naissance": row.get("lieu_naissance", ""),
                "localite":       row.get("localite", ""),
                "lot":            row.get("lot", ""),
                "parcelle":       row.get("parcelle", ""),
                "superficie_m2":  str(row.get("superficie_m2", "")),
                "latitude":       str(row["latitude"])  if row.get("latitude")  else "",
                "longitude":      str(row["longitude"]) if row.get("longitude") else "",
                "nom_topographe": fiche["topographe_nom"]             if fiche else "",
                "nom_huissier":   fiche["huissier_nom"]               if fiche and fiche["huissier_nom"] else "",
                "qr_url":         qr_url,
                "nus":            nus,
                "hash_ccfm":      hash_ccfm,
            }

            out_path = os.path.join(_get_pdf_dir(), f"CCFM_{_nus_safe(nus)}.pdf")
            generer_certificat_ccfm(pdf_data, out_path, armoiries_path=_get_armoiries_path())
            return out_path

        except Exception as e:
            print(f"[CCFM] Erreur génération PDF certificat pour {row.get('nus')} : {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════════
    # ÉTAPE 7 — ARCHIVISTE : RETRAIT_EN_ATTENTE → ANNF + blockchain → ARCHIVE
    # ═══════════════════════════════════════════════════════════════════════

    async def preparer_retrait(self, ccfm_id: str, archiviste_id: str = None) -> dict:
        """
        Première action archiviste : passe l'état à RETRAIT_EN_ATTENTE.
        Le certificat physique est préparé pour remise au bénéficiaire.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT etat FROM demandes_ccfm WHERE ccfm_id = $1", ccfm_id)
            if not row or row["etat"] != "SIGNE_DIRECTEUR":
                raise ValueError("Gate étape 7 : CCFM doit être signé par le Directeur")
            await conn.execute(
                "UPDATE demandes_ccfm SET etat = 'RETRAIT_EN_ATTENTE'::etat_ccfm, "
                "archiviste = $2 WHERE ccfm_id = $1",
                ccfm_id, archiviste_id,
            )
            return {"ccfm_id": ccfm_id, "etat": "RETRAIT_EN_ATTENTE"}

    async def archiver(self, ccfm_id: str, archiviste_id: str = None) -> dict:
        """
        Archivage final ANNF + ancrage blockchain SHA-256.
        Accepte : SIGNE_DIRECTEUR ou RETRAIT_EN_ATTENTE.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT etat, hash_ccfm FROM demandes_ccfm WHERE ccfm_id = $1", ccfm_id
            )
            if not row or row["etat"] not in ("SIGNE_DIRECTEUR", "RETRAIT_EN_ATTENTE"):
                raise ValueError(
                    f"Gate étape 7 : état actuel={row['etat'] if row else 'N/A'}, "
                    "attendu=SIGNE_DIRECTEUR ou RETRAIT_EN_ATTENTE"
                )

            annee = datetime.now(timezone.utc).year
            seq   = await conn.fetchval(
                "SELECT COUNT(*) + 1 FROM demandes_ccfm "
                "WHERE annf_code IS NOT NULL AND EXTRACT(YEAR FROM date_archivage) = $1",
                annee,
            )
            annf_code = f"ANNF-CCFM-{annee}-{seq:05d}"
            tx        = f"0x{row['hash_ccfm']}" if row["hash_ccfm"] else None

            await conn.execute(
                """UPDATE demandes_ccfm SET etat = 'ARCHIVE'::etat_ccfm,
                   date_archivage = NOW(), archiviste = $2,
                   annf_code = $3, tx_blockchain = $4
                   WHERE ccfm_id = $1""",
                ccfm_id, archiviste_id, annf_code, tx,
            )
            return {
                "ccfm_id":   ccfm_id,
                "etat":      "ARCHIVE",
                "annf_code": annf_code,
                "tx":        tx,
            }

    async def marquer_retrait(self, ccfm_id: str) -> dict:
        """Retrait du certificat physique par le bénéficiaire."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT etat FROM demandes_ccfm WHERE ccfm_id = $1", ccfm_id)
            if not row or row["etat"] not in ("ARCHIVE", "RETRAIT_EN_ATTENTE"):
                raise ValueError("Gate : CCFM doit être archivé ou en attente de retrait")
            await conn.execute(
                "UPDATE demandes_ccfm SET etat = 'RETIRE'::etat_ccfm, "
                "date_retrait = NOW() WHERE ccfm_id = $1",
                ccfm_id,
            )
            return {"ccfm_id": ccfm_id, "etat": "RETIRE"}

    # ═══════════════════════════════════════════════════════════════════════
    # RÉGÉNÉRATION PDF — Utilitaire (recréer un PDF sans relancer le workflow)
    # ═══════════════════════════════════════════════════════════════════════

    async def regenerer_pdf(self, ccfm_id: str, type_pdf: str = "certificat") -> dict:
        """
        Régénère un PDF sans toucher à l'état du dossier.
        type_pdf : 'certificat' (A5) ou 'demande' (A4)
        Utile en cas de perte du fichier ou de mise à jour des armoiries.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT *, etat::TEXT AS etat_text FROM demandes_ccfm WHERE ccfm_id = $1",
                ccfm_id,
            )
            if not row:
                raise ValueError("Demande CCFM non trouvée")

        if type_pdf == "certificat":
            if row["etat_text"] not in (
                "SIGNE_DIRECTEUR", "ARCHIVE", "RETRAIT_EN_ATTENTE", "RETIRE"
            ):
                raise ValueError(
                    f"PDF certificat disponible uniquement après signature. État : {row['etat_text']}"
                )
            h   = row.get("hash_ccfm", "")
            qr  = row.get("qr_code", f"CCFM|{row['nus']}|https://foncier.ne/verify/{row['nus']}")
            pdf = await self._generer_pdf_certificat(row, h, qr)
            if pdf:
                async with self.pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE demandes_ccfm SET pdf_url = $2 WHERE ccfm_id = $1",
                        ccfm_id, pdf,
                    )
            return {"ccfm_id": ccfm_id, "type": "certificat", "pdf_url": pdf}

        elif type_pdf == "demande":
            pdf = await self._generer_pdf_demande(dict(row), row["nus"])
            if pdf:
                async with self.pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE demandes_ccfm SET pdf_demande_url = $2 WHERE ccfm_id = $1",
                        ccfm_id, pdf,
                    )
            return {"ccfm_id": ccfm_id, "type": "demande", "pdf_url": pdf}

        else:
            raise ValueError(f"type_pdf invalide : '{type_pdf}'. Attendu : 'certificat' ou 'demande'")

    # ═══════════════════════════════════════════════════════════════════════
    # VÉRIFICATION PUBLIQUE (gate inter-modules)
    # ═══════════════════════════════════════════════════════════════════════

    async def verifier_ccfm(
        self,
        nus: str = None,
        reference: str = None,
        lot: str = None,
        parcelle: str = None,
    ) -> dict:
        """Vérifie la validité d'un CCFM — gate pour tous les modules consommateurs."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM verifier_ccfm_valide($1, $2, $3, $4)",
                nus, reference, lot, parcelle,
            )
            return dict(row) if row else {
                "valide": False, "statut": "INTROUVABLE", "message": "CCFM non trouvé"
            }

    # ═══════════════════════════════════════════════════════════════════════
    # WORKSPACES PAR RÔLE
    # ═══════════════════════════════════════════════════════════════════════

    async def workspace_guichetiere(self) -> list:
        """Guichetière : demandes récentes + états initiaux."""
        async with self.pool.acquire() as conn:
            return [dict(r) for r in await conn.fetch(
                "SELECT ccfm_id, nus, nom_prenom, localite, lot, parcelle, etat::TEXT, "
                "date_enregistrement, montant_paye, pdf_demande_url FROM demandes_ccfm "
                "WHERE etat::TEXT IN ('DEMANDE_RECUE','VERIFICATION_AUTO','TICKET_EMIS','VERIFICATION_AUTO_KO') "
                "ORDER BY date_enregistrement DESC LIMIT 50"
            )]

    async def workspace_secretaire(self) -> list:
        """Secrétaire : tickets émis à traiter → ordres de mission à émettre."""
        async with self.pool.acquire() as conn:
            return [dict(r) for r in await conn.fetch(
                "SELECT ccfm_id, nus, nom_prenom, localite, lot, parcelle, "
                "etat::TEXT, rnaf_conforme, bgu_conforme, latitude, longitude, "
                "date_ticket_emis FROM demandes_ccfm "
                "WHERE etat::TEXT IN ('TICKET_EMIS','ORDRE_MISSION_EMIS','ATTENTE_VISITE_TERRAIN') "
                "ORDER BY date_ticket_emis ASC NULLS LAST LIMIT 100"
            )]

    async def workspace_topographe(self, topographe_id: str) -> list:
        """Topographe : ordres de mission reçus avec ticket GPS joint."""
        async with self.pool.acquire() as conn:
            return [dict(r) for r in await conn.fetch(
                """SELECT c.ccfm_id, c.nus, c.nom_prenom, c.localite, c.lot, c.parcelle,
                          c.superficie_m2, c.latitude AS gps_ticket_lat, c.longitude AS gps_ticket_lon,
                          c.rnaf_conforme, c.bgu_conforme,
                          c.etat::TEXT,
                          o.numero_ordre, o.date_prevue_visite, o.instructions,
                          o.ticket_joint, o.statut AS statut_ordre
                   FROM demandes_ccfm c
                   JOIN ordres_mission_ccfm o ON c.ccfm_id = o.ccfm_id AND o.type_mission = 'TOPOGRAPHE'
                   WHERE o.destinataire_id = $1
                     AND o.statut != 'ANNULE'
                     AND c.etat::TEXT IN ('ORDRE_MISSION_EMIS','ATTENTE_VISITE_TERRAIN','VISITE_EN_COURS')
                   ORDER BY o.date_emission ASC""",
                topographe_id,
            )]

    async def workspace_directeur(self) -> list:
        """Directeur : rapports d'appréciation en attente de signature."""
        async with self.pool.acquire() as conn:
            return [dict(r) for r in await conn.fetch(
                """SELECT c.ccfm_id, c.nus, c.nom_prenom, c.localite, c.lot, c.parcelle,
                          c.superficie_m2, c.etat::TEXT,
                          c.rnaf_conforme, c.bgu_conforme,
                          c.latitude AS gps_ticket_lat, c.longitude AS gps_ticket_lon,
                          r.verdict_global, r.points_conformite, r.anomalies_detectees,
                          r.ecart_superficie_pct, r.concordance_gps,
                          r.gps_mesure_lat, r.gps_mesure_lon,
                          r.decision_topographe, r.observations_topographe,
                          c.pdf_url, c.hash_ccfm
                   FROM demandes_ccfm c
                   LEFT JOIN rapports_appreciation_ccfm r ON c.ccfm_id = r.ccfm_id
                   WHERE c.etat::TEXT IN ('ATTENTE_SIGNATURE_DIRECTEUR','SIGNE_DIRECTEUR')
                   ORDER BY c.date_rapport_apprec ASC NULLS LAST""",
            )]

    async def workspace_archiviste(self) -> list:
        """Archiviste : certificats signés à archiver + archivés."""
        async with self.pool.acquire() as conn:
            return [dict(r) for r in await conn.fetch(
                "SELECT ccfm_id, nus, reference_ccfm, nom_prenom, localite, lot, parcelle, "
                "etat::TEXT, date_signature, date_archivage, annf_code, pdf_url, pdf_demande_url "
                "FROM demandes_ccfm "
                "WHERE etat::TEXT IN ('SIGNE_DIRECTEUR','RETRAIT_EN_ATTENTE','ARCHIVE','RETIRE') "
                "ORDER BY date_archivage DESC NULLS FIRST LIMIT 200"
            )]

    # ═══════════════════════════════════════════════════════════════════════
    # STATISTIQUES & CONSULTATION
    # ═══════════════════════════════════════════════════════════════════════

    async def stats(self, localite: str = None) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM stats_ccfm($1)", localite)
            return dict(row) if row else {}

    async def get_demande(self, ccfm_id: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT *, etat::TEXT AS etat FROM demandes_ccfm WHERE ccfm_id = $1", ccfm_id
            )
            if not row:
                return None
            d       = dict(row)
            fiche   = await conn.fetchrow(
                "SELECT * FROM fiches_constat_ccfm WHERE ccfm_id = $1 ORDER BY created_at DESC LIMIT 1",
                ccfm_id,
            )
            rapport = await conn.fetchrow(
                "SELECT * FROM rapports_appreciation_ccfm WHERE ccfm_id = $1 ORDER BY created_at DESC LIMIT 1",
                ccfm_id,
            )
            ordres  = await conn.fetch(
                "SELECT * FROM ordres_mission_ccfm WHERE ccfm_id = $1 ORDER BY date_emission", ccfm_id
            )
            hist    = await conn.fetch(
                "SELECT * FROM historique_ccfm WHERE ccfm_id = $1 ORDER BY created_at", ccfm_id
            )
            if fiche:   d["fiche_constat"]        = dict(fiche)
            if rapport: d["rapport_appreciation"]  = dict(rapport)
            d["ordres_mission"] = [dict(o) for o in ordres]
            d["historique"]     = [dict(h) for h in hist]
            return d

    async def lister_demandes(
        self, etat: str = None, localite: str = None, limit: int = 50
    ) -> list:
        async with self.pool.acquire() as conn:
            q      = (
                "SELECT ccfm_id, nus, reference_ccfm, nom_prenom, localite, lot, parcelle, "
                "superficie_m2, etat::TEXT, date_enregistrement, date_signature, "
                "pdf_url, pdf_demande_url FROM demandes_ccfm WHERE 1=1"
            )
            params = []; idx = 1
            if etat:
                q += f" AND etat::TEXT = ${idx}"; params.append(etat); idx += 1
            if localite:
                q += f" AND localite = ${idx}"; params.append(localite); idx += 1
            q += f" ORDER BY date_enregistrement DESC LIMIT ${idx}"; params.append(limit)
            return [dict(r) for r in await conn.fetch(q, *params)]

    async def dashboard(self) -> list:
        async with self.pool.acquire() as conn:
            return [dict(r) for r in await conn.fetch(
                "SELECT * FROM v_dashboard_ccfm ORDER BY date_enregistrement DESC LIMIT 100"
            )]
