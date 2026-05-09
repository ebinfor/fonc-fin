"""
FONCIER+ v3 — Schémas Pydantic CCFM
Certificat de Conformité Foncière Mixte

Workflow officiel 7 étapes :
  1. GUICHETIÈRE    → Enregistrement demande + paiement 50 000 FCFA → NUS
  2. SYSTÈME AUTO   → Vérification RNAF + BGU → Ticket de réponse (GPS + résultats)
  3. SECRÉTAIRE     → Consulte ticket → Émet ordre de mission (ticket joint)
  4. TOPOGRAPHE     → Fiche de Vérification Administrative Foncière (formulaire officiel 9 sections)
  5. SYSTÈME AUTO   → Compile ticket + fiche → Rapport d'appréciation pour Directeur
  6. DIRECTEUR      → Consulte rapport → Signe → Télécharge certificat PDF
  7. ARCHIVISTE     → Archivage ANNF + ancrage blockchain
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ─── Enums ──────────────────────────────────────────────────────────────────

class EtatCCFM(str, Enum):
    # Étape 1 — Guichetière
    DEMANDE_RECUE                 = "DEMANDE_RECUE"
    # Étape 2 — Système
    VERIFICATION_AUTO             = "VERIFICATION_AUTO"
    TICKET_EMIS                   = "TICKET_EMIS"           # OK : ticket RNAF+BGU+GPS transmis
    VERIFICATION_AUTO_KO          = "VERIFICATION_AUTO_KO"  # KO : rejet automatique
    # Étape 3 — Secrétaire
    ORDRE_MISSION_EMIS            = "ORDRE_MISSION_EMIS"
    ATTENTE_VISITE_TERRAIN        = "ATTENTE_VISITE_TERRAIN"
    # Étape 4 — Topographe
    VISITE_EN_COURS               = "VISITE_EN_COURS"
    FICHE_CONSTAT_DEPOSEE         = "FICHE_CONSTAT_DEPOSEE"
    # Étape 5 — Système
    RAPPORT_APPRECIATION          = "RAPPORT_APPRECIATION"
    ATTENTE_SIGNATURE_DIRECTEUR   = "ATTENTE_SIGNATURE_DIRECTEUR"
    # Étape 6 — Directeur
    SIGNE_DIRECTEUR               = "SIGNE_DIRECTEUR"
    # Étape 7 — Archiviste
    ARCHIVE                       = "ARCHIVE"
    RETRAIT_EN_ATTENTE            = "RETRAIT_EN_ATTENTE"
    RETIRE                        = "RETIRE"
    # Terminal — rejet à tout stade
    REJETE                        = "REJETE"


class ModePaiementCCFM(str, Enum):
    AIRTEL_MONEY  = "AIRTEL_MONEY"
    MOOV_MONEY    = "MOOV_MONEY"
    BANQUE        = "BANQUE"
    TRESOR_PUBLIC = "TRESOR_PUBLIC"


class MotifRejetCCFM(str, Enum):
    PARCELLE_INTROUVABLE        = "PARCELLE_INTROUVABLE"
    ARRETE_INVALIDE             = "ARRETE_INVALIDE"
    ARRETE_NON_PUBLIE           = "ARRETE_NON_PUBLIE"
    RNAF_NON_CONFORME           = "RNAF_NON_CONFORME"
    BGU_NON_CONFORME            = "BGU_NON_CONFORME"
    GPS_NON_CONCORDANT          = "GPS_NON_CONCORDANT"
    OCCUPATION_ILLEGALE         = "OCCUPATION_ILLEGALE"
    CONFLIT_TERRAIN             = "CONFLIT_TERRAIN"
    DOCUMENTS_INCOMPLETS        = "DOCUMENTS_INCOMPLETS"
    PAIEMENT_INVALIDE           = "PAIEMENT_INVALIDE"
    SUPERFICIE_NON_CONCORDANTE  = "SUPERFICIE_NON_CONCORDANTE"
    TITRE_CONTESTABLE           = "TITRE_CONTESTABLE"
    AUTRE                       = "AUTRE"


class RoleCCFM(str, Enum):
    GUICHETIERE  = "GUICHETIERE"
    SYSTEME      = "SYSTEME"
    SECRETAIRE   = "SECRETAIRE"
    TOPOGRAPHE   = "TOPOGRAPHE"
    DIRECTEUR    = "DIRECTEUR"
    ARCHIVISTE   = "ARCHIVISTE"


class VerdictCCFM(str, Enum):
    CONFORME      = "CONFORME"
    A_VERIFIER    = "A_VERIFIER"
    NON_CONFORME  = "NON_CONFORME"


class StatutVerifCCFM(str, Enum):
    VALIDE      = "VALIDE"
    EXPIRE      = "EXPIRE"
    REVOQUE     = "REVOQUE"
    INTROUVABLE = "INTROUVABLE"
    EN_COURS    = "EN_COURS"


class DecisionTopographe(str, Enum):
    AUTORISATION = "AUTORISATION"
    REFUS        = "REFUS"


# ─── Étape 1 : Guichetière — Enregistrement demande ─────────────────────────

class DemandeCCFMCreate(BaseModel):
    """Enregistrement d'une demande CCFM (Guichetière — Étape 1)."""
    # Identité demandeur
    nom_prenom:      str = Field(..., min_length=3, max_length=200)
    nip_passeport:   str = Field(..., min_length=5, max_length=50)
    date_naissance:  date
    lieu_naissance:  str = Field(..., max_length=200)
    nationalite:     str = Field(default="NE", max_length=2)
    adresse:         str = Field(..., max_length=500)
    telephone:       str = Field(..., max_length=20)
    email:           Optional[str] = None
    # Parcelle
    localite:                str = Field(..., max_length=200)
    lot:                     str = Field(..., max_length=50)
    parcelle:                str = Field(..., max_length=50)
    superficie_m2:           Decimal = Field(..., gt=0)
    # Références foncières (vérifiées par le système à l'étape 2)
    numero_arrete:           Optional[str] = None
    numero_titre_foncier:    Optional[str] = None
    nicad_code:              Optional[str] = None
    # Paiement forfaitaire 50 000 FCFA
    mode_paiement:           ModePaiementCCFM
    reference_paiement:      str = Field(..., max_length=200)


# ─── Étape 3 : Secrétaire — Ordre de mission ────────────────────────────────

class OrdreMissionCreate(BaseModel):
    """
    Émission ordre de mission (Secrétaire — Étape 3).
    Le ticket RNAF/BGU/GPS est joint automatiquement côté service.
    """
    ccfm_id:              str
    topographe_id:        str
    topographe_nom:       Optional[str] = None
    date_prevue_visite:   Optional[date] = None
    instructions:         Optional[str] = None


# ─── Étape 4 : Topographe — Fiche de Vérification Administrative Foncière ───

class FicheConstatCreate(BaseModel):
    """
    Fiche de Vérification Administrative Foncière (Topographe — Étape 4).
    Formulaire officiel du Ministère de l'Urbanisme — 9 sections.

    §3 : gps_mesure_latitude/longitude à comparer avec le GPS du ticket (étape 2).
    §7 : decision = AUTORISATION (poursuivre) ou REFUS (motif obligatoire).
    """
    ccfm_id: str

    # §1 — Références
    objet_demande: Optional[str] = None

    # §2 — Arrêté de lotissement
    arrete_numero:          Optional[str]  = None
    arrete_date_signature:  Optional[date] = None
    arrete_autorite:        Optional[str]  = None
    jo_publie:              bool           = False
    jo_reference:           Optional[str]  = None

    # §3 — Assiette foncière (GPS mesuré terrain)
    parcelle_ref_titre:     Optional[str]          = None
    superficie_mesuree_m2:  Decimal                = Field(..., gt=0)
    gps_mesure_latitude:    Optional[Decimal]      = None
    gps_mesure_longitude:   Optional[Decimal]      = None
    parcelle_conforme:      Optional[bool]         = None

    # §4 — Titre foncier
    titre_foncier_numero:   Optional[str]  = None
    titre_foncier_emetteur: Optional[str]  = None
    titre_authentique:      Optional[bool] = None

    # §6 — Observations
    observations:           Optional[str]  = None

    # §7 — Décision
    decision:               DecisionTopographe
    motif_refus:            Optional[str] = None    # Obligatoire si decision=REFUS

    # §8 — Signatures
    topographe_nom:         str = Field(..., min_length=3)
    topographe_fonction:    str = Field(default="Topographe agréé")
    topographe_id:          Optional[str] = None

    # Photos géolocalisées
    photos_terrain:         List[str] = Field(default_factory=list)


# ─── Étape 6 : Directeur — Validation + signature ───────────────────────────

class ValidationDirecteur(BaseModel):
    """
    Consultation rapport d'appréciation + signature (Directeur — Étape 6).
    Si approuvé : génère le hash SHA-256 + QR Code + PDF certificat.
    """
    ccfm_id:     str
    approuve:    bool
    observations: Optional[str] = None
    motif_rejet:  Optional[MotifRejetCCFM] = None  # Obligatoire si approuve=False


# ─── Vérification publique ───────────────────────────────────────────────────

class VerificationCCFMIn(BaseModel):
    """Vérification publique d'un CCFM (gate inter-modules)."""
    nus:            Optional[str] = None
    reference_ccfm: Optional[str] = None
    lot:            Optional[str] = None
    parcelle:       Optional[str] = None


# ─── Retrait ────────────────────────────────────────────────────────────────

class RetraitCCFM(BaseModel):
    """Marquage retrait du certificat par le bénéficiaire."""
    ccfm_id: str
