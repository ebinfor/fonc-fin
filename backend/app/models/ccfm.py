"""
FONCIER+ -- Modeles CCFM officiels
Schema reglementaire Direction Generale de l Urbanisme -- 7 acteurs / 15 etats / 5 tables

NUS format    : NUS-AAAA-NNNNN    (ex: NUS-2026-00089)
REF CCFM      : CCFM/AAAA/MM/NNNN (ex: CCFM/2026/02/0089)
"""
import enum, uuid
from sqlalchemy import Boolean, Column, Date, DateTime, Numeric, String, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.core.database import Base


class EtatCCFM(str, enum.Enum):
    DEMANDE_RECUE               = "DEMANDE_RECUE"
    VERIFICATION_AUTO           = "VERIFICATION_AUTO"
    TICKET_EMIS                 = "TICKET_EMIS"
    VERIFICATION_AUTO_KO        = "VERIFICATION_AUTO_KO"
    ORDRE_MISSION_EMIS          = "ORDRE_MISSION_EMIS"
    ATTENTE_VISITE_TERRAIN      = "ATTENTE_VISITE_TERRAIN"
    VISITE_EN_COURS             = "VISITE_EN_COURS"
    FICHE_CONSTAT_DEPOSEE       = "FICHE_CONSTAT_DEPOSEE"
    RAPPORT_APPRECIATION        = "RAPPORT_APPRECIATION"
    ATTENTE_SIGNATURE_DIRECTEUR = "ATTENTE_SIGNATURE_DIRECTEUR"
    SIGNE_DIRECTEUR             = "SIGNE_DIRECTEUR"
    ARCHIVE                     = "ARCHIVE"
    RETRAIT_EN_ATTENTE          = "RETRAIT_EN_ATTENTE"
    RETIRE                      = "RETIRE"
    REJETE                      = "REJETE"


class ModePaiementCCFM(str, enum.Enum):
    AIRTEL_MONEY  = "AIRTEL_MONEY"
    MOOV_MONEY    = "MOOV_MONEY"
    BANQUE        = "BANQUE"
    TRESOR_PUBLIC = "TRESOR_PUBLIC"


class VerdictCCFM(str, enum.Enum):
    CONFORME     = "CONFORME"
    A_VERIFIER   = "A_VERIFIER"
    NON_CONFORME = "NON_CONFORME"


ETATS_CCFM_VALIDES = frozenset({
    "SIGNE_DIRECTEUR", "ARCHIVE", "RETRAIT_EN_ATTENTE", "RETIRE"
})

ACTEUR_PAR_ETAT = {
    "DEMANDE_RECUE":               "SYSTEME",
    "TICKET_EMIS":                 "SECRETAIRE",
    "ORDRE_MISSION_EMIS":          "TOPOGRAPHE",
    "ATTENTE_VISITE_TERRAIN":      "TOPOGRAPHE",
    "VISITE_EN_COURS":             "TOPOGRAPHE",
    "FICHE_CONSTAT_DEPOSEE":       "SYSTEME",
    "RAPPORT_APPRECIATION":        "SYSTEME",
    "ATTENTE_SIGNATURE_DIRECTEUR": "DIRECTEUR",
    "SIGNE_DIRECTEUR":             "ARCHIVISTE",
}


class CCFMDemande(Base):
    __tablename__ = "demandes_ccfm"
    ccfm_id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nus                  = Column(String(50),  unique=True, nullable=False)
    reference_ccfm       = Column(String(100), unique=True)
    nom_prenom           = Column(String(200), nullable=False)
    nip_passeport        = Column(String(50),  nullable=False)
    date_naissance       = Column(Date,        nullable=False)
    lieu_naissance       = Column(String(200), nullable=False)
    nationalite          = Column(String(2),   server_default="NE")
    adresse              = Column(Text,        nullable=False)
    telephone            = Column(String(20),  nullable=False)
    email                = Column(String(200))
    localite             = Column(String(200), nullable=False)
    lot                  = Column(String(50),  nullable=False)
    parcelle             = Column(String(50),  nullable=False)
    superficie_m2        = Column(Numeric(12, 2), nullable=False)
    numero_arrete        = Column(String(100))
    numero_titre_foncier = Column(String(100))
    nicad_code           = Column(Text)
    mode_paiement        = Column(String(20), nullable=False)
    reference_paiement   = Column(String(200), nullable=False)
    montant_paye         = Column(Numeric(10, 2), server_default="50000")
    date_paiement        = Column(DateTime(timezone=True))
    etat                 = Column(String(40), nullable=False, server_default="DEMANDE_RECUE")
    motif_rejet          = Column(String(50))
    observations_rejet   = Column(Text)
    enregistre_par       = Column(Text)
    topographe_assigne   = Column(Text)
    directeur_validateur = Column(Text)
    archiviste           = Column(Text)
    rnaf_conforme        = Column(Boolean)
    rnaf_anomalies       = Column(Text)
    bgu_conforme         = Column(Boolean)
    bgu_anomalies        = Column(Text)
    latitude             = Column(Numeric(10, 7))
    longitude            = Column(Numeric(10, 7))
    ticket_hash          = Column(String(64))
    hash_ccfm            = Column(String(64))
    qr_code              = Column(Text)
    signature_directeur  = Column(Text)
    pdf_url              = Column(Text)
    pdf_demande_url      = Column(Text)
    annf_code            = Column(String(50))
    tx_blockchain        = Column(String(100))
    date_enregistrement         = Column(DateTime(timezone=True), server_default=func.now())
    date_verification_auto      = Column(DateTime(timezone=True))
    date_ticket_emis            = Column(DateTime(timezone=True))
    date_assignation_topographe = Column(DateTime(timezone=True))
    date_visite_terrain         = Column(DateTime(timezone=True))
    date_rapport_topographe     = Column(DateTime(timezone=True))
    date_rapport_apprec         = Column(DateTime(timezone=True))
    date_validation_directeur   = Column(DateTime(timezone=True))
    date_signature              = Column(DateTime(timezone=True))
    date_archivage              = Column(DateTime(timezone=True))
    date_retrait                = Column(DateTime(timezone=True))
    updated_at                  = Column(DateTime(timezone=True), server_default=func.now())

    def est_valide(self) -> bool:
        return self.etat in ETATS_CCFM_VALIDES

    def acteur_responsable(self) -> str:
        return ACTEUR_PAR_ETAT.get(self.etat, "AUCUN")


class OrdreMissionCCFM(Base):
    __tablename__ = "ordres_mission_ccfm"
    ordre_id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ccfm_id            = Column(UUID(as_uuid=True), ForeignKey("demandes_ccfm.ccfm_id", ondelete="CASCADE"), nullable=False)
    numero_ordre       = Column(String(50), unique=True, nullable=False)
    type_mission       = Column(String(20), server_default="TOPOGRAPHE")
    destinataire_id    = Column(Text, nullable=False)
    destinataire_nom   = Column(Text)
    date_emission      = Column(DateTime(timezone=True), server_default=func.now())
    date_prevue_visite = Column(Date)
    instructions       = Column(Text)
    ticket_joint       = Column(JSONB)
    emis_par           = Column(Text)
    statut             = Column(String(20), server_default="EN_ATTENTE")
    date_execution     = Column(DateTime(timezone=True))


class FicheConstatCCFM(Base):
    __tablename__ = "fiches_constat_ccfm"
    fiche_id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ccfm_id                = Column(UUID(as_uuid=True), ForeignKey("demandes_ccfm.ccfm_id", ondelete="CASCADE"), nullable=False)
    arrete_numero          = Column(String(100))
    arrete_date_signature  = Column(Date)
    arrete_autorite        = Column(Text)
    jo_publie              = Column(Boolean, server_default="false")
    jo_reference           = Column(Text)
    parcelle_ref_titre     = Column(Text)
    superficie_mesuree_m2  = Column(Numeric(12, 2))
    gps_mesure_latitude    = Column(Numeric(10, 7))
    gps_mesure_longitude   = Column(Numeric(10, 7))
    parcelle_conforme      = Column(Boolean)
    concordance_gps        = Column(Boolean)
    ecart_superficie_pct   = Column(Numeric(5, 2))
    titre_foncier_numero   = Column(Text)
    titre_foncier_emetteur = Column(Text)
    titre_authentique      = Column(Boolean)
    observations           = Column(Text)
    decision               = Column(String(20))
    motif_refus            = Column(Text)
    topographe_nom         = Column(Text, nullable=False)
    topographe_fonction    = Column(Text, server_default="Topographe agree")
    topographe_id          = Column(Text)
    photos_terrain         = Column(JSONB, server_default="'[]'")
    hash_fiche             = Column(String(64))
    created_at             = Column(DateTime(timezone=True), server_default=func.now())


class RapportAppreciationCCFM(Base):
    __tablename__ = "rapports_appreciation_ccfm"
    rapport_id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ccfm_id                 = Column(UUID(as_uuid=True), ForeignKey("demandes_ccfm.ccfm_id", ondelete="CASCADE"), nullable=False)
    rnaf_conforme           = Column(Boolean)
    bgu_conforme            = Column(Boolean)
    gps_ticket_lat          = Column(Numeric(10, 7))
    gps_ticket_lon          = Column(Numeric(10, 7))
    superficie_mesuree_m2   = Column(Numeric(12, 2))
    ecart_superficie_pct    = Column(Numeric(5, 2))
    gps_mesure_lat          = Column(Numeric(10, 7))
    gps_mesure_lon          = Column(Numeric(10, 7))
    concordance_gps         = Column(Boolean)
    jo_publie               = Column(Boolean)
    titre_authentique       = Column(Boolean)
    decision_topographe     = Column(String(20))
    observations_topographe = Column(Text)
    verdict_global          = Column(String(20), nullable=False)
    points_conformite       = Column(JSONB, server_default="'[]'")
    anomalies_detectees     = Column(JSONB, server_default="'[]'")
    rapport_hash            = Column(String(64))
    created_at              = Column(DateTime(timezone=True), server_default=func.now())


class HistoriqueCCFM(Base):
    __tablename__ = "historique_ccfm"
    historique_id   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ccfm_id         = Column(UUID(as_uuid=True), ForeignKey("demandes_ccfm.ccfm_id", ondelete="CASCADE"), nullable=False)
    etat_precedent  = Column(String(50))
    etat_nouveau    = Column(String(50), nullable=False)
    action          = Column(String(100), nullable=False)
    commentaire     = Column(Text)
    effectue_par    = Column(Text)
    role_effectueur = Column(String(50))
    metadata        = Column(JSONB)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
