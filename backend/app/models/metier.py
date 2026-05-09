"""
FONCIER+ — Modèles SQLAlchemy pour tables métier critiques
Couvre les 14 tables les plus utilisées via text() SQL brut.
"""
import enum
import uuid
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Enum, ForeignKey,
    Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.sql import func
from app.core.database import Base


# ── BanqueAgree ───────────────────────────────────────────────────
class BanqueAgree(Base):
    """Banques agréées BCEAO (migration 020)."""
    __tablename__ = "banque_agreee"

    id                     = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    numero_agrement_bceao  = Column(String(50), nullable=False, unique=True)
    raison_sociale         = Column(String(300), nullable=False)
    sigle                  = Column(String(20))
    siege_social           = Column(Text)
    pays_origine           = Column(String(50), server_default="NER")
    type_etablissement     = Column(String(30))
    date_agrement          = Column(Date, nullable=False)
    date_retrait_agrement  = Column(Date)
    statut                 = Column(String(20), server_default="actif")
    liste_agences          = Column(JSONB, server_default="[]")
    contact_direction      = Column(String(200))
    created_at             = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<BanqueAgree {self.sigle} statut={self.statut}>"


# ── HypothequesDossier ────────────────────────────────────────────
class HypothequesDossier(Base):
    """Dossiers d'hypothèque WF8 (migration 008)."""
    __tablename__ = "hypotheque_dossier"

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parcelle_id          = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"), nullable=False)
    rnp_parcelle_id      = Column(UUID(as_uuid=True), ForeignKey("rnp_demandes.id"), nullable=False)
    debiteur_id          = Column(UUID(as_uuid=True), ForeignKey("right_holders.id"), nullable=False)
    banque_id            = Column(UUID(as_uuid=True), ForeignKey("right_holders.id"), nullable=False)
    montant_fcfa         = Column(Numeric(18, 2), nullable=False)
    taux_interet         = Column(Numeric(5, 2))
    duree_mois           = Column(Integer)
    date_debut           = Column(DateTime(timezone=True))
    date_fin             = Column(DateTime(timezone=True))
    statut               = Column(String(30), server_default="initie", nullable=False)
    ccfm_validation_id   = Column(UUID(as_uuid=True), ForeignKey("demandes_ccfm.id"))
    hypotheque_id        = Column(UUID(as_uuid=True), ForeignKey("hypotheques.id"))
    mortgage_registry_id = Column(UUID(as_uuid=True), ForeignKey("mortgage_registry.id"))
    workflow_instance_id = Column(UUID(as_uuid=True), ForeignKey("workflow_instance.id"))
    sha256_dossier       = Column(String(64))
    cree_par             = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at           = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<HypothequesDossier {self.montant_fcfa} XOF statut={self.statut}>"


# ── AnomalieFonciere ─────────────────────────────────────────────
class AnomalieFonciere(Base):
    """Anomalies foncières détectées (migration 013)."""
    __tablename__ = "anomalie_fonciere"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type_anomalie   = Column(String(50), nullable=False)
    gravite         = Column(String(20), nullable=False)
    parcelle_id     = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"))
    entite_type     = Column(String(50))
    entite_id       = Column(UUID(as_uuid=True))
    nicad           = Column(String(30))
    description     = Column(Text, nullable=False)
    valeur_mesuree  = Column(JSONB)
    sha256_anomalie = Column(String(64))
    corrige         = Column(Boolean, server_default="false")
    action_requise  = Column(Text)
    corrige_par     = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    corrige_at      = Column(DateTime(timezone=True))
    motif_correction= Column(Text)
    alerte_id       = Column(UUID(as_uuid=True), ForeignKey("alertes_systeme.id"))
    detecte_at      = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<AnomalieFonciere {self.type_anomalie} gravite={self.gravite}>"


# ── IndicateurNational ────────────────────────────────────────────
class IndicateurNational(Base):
    """KPIs fonciers nationaux (migration 013)."""
    __tablename__ = "indicateur_national"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type_indicateur  = Column(String(50), nullable=False)
    valeur           = Column(Numeric(18, 4), nullable=False)
    unite            = Column(String(30))
    region_id        = Column(UUID(as_uuid=True), ForeignKey("regions.id"))
    commune_id       = Column(UUID(as_uuid=True), ForeignKey("communes.id"))
    periode          = Column(String(20), nullable=False)
    date_calcul      = Column(DateTime(timezone=True), server_default=func.now())
    methode_calcul   = Column(Text)
    sha256_calcul    = Column(String(64))
    calcule_par      = Column(String(50), server_default="SYSTEME")

    __table_args__ = (
        UniqueConstraint("type_indicateur", "periode", "region_id", "commune_id",
                         name="uq_in_type_periode_geo"),
    )

    def __repr__(self):
        return f"<IndicateurNational {self.type_indicateur} {self.periode}={self.valeur}>"


# ── GreffeJudiciaire ─────────────────────────────────────────────
class GreffeJudiciaire(Base):
    """Greffes judiciaires (9 TGI + Cour d'appel — migration 010)."""
    __tablename__ = "greffe_judiciaire"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nom_greffe       = Column(String(200), nullable=False)
    tribunal_nom     = Column(String(200), nullable=False)
    tribunal_code    = Column(String(20), unique=True, nullable=False)
    region_id        = Column(UUID(as_uuid=True), ForeignKey("regions.id"))
    juridiction      = Column(String(100))
    statut           = Column(String(20), server_default="actif")
    cle_verification = Column(String(500))
    contact          = Column(String(200))
    adresse          = Column(Text)
    chef_greffe_id   = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at       = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<GreffeJudiciaire {self.tribunal_code}>"


# ── SauvegardeSysteme ────────────────────────────────────────────
class SauvegardeSysteme(Base):
    """Sauvegardes système (AES-256 — migration 013)."""
    __tablename__ = "sauvegarde_systeme"

    id                      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type_sauvegarde         = Column(String(30), nullable=False)
    statut                  = Column(String(20), server_default="en_cours")
    emplacement             = Column(String(500), nullable=False)
    emplacement_secondaire  = Column(String(500))
    taille_octets           = Column(Numeric(18, 0))
    nb_tables               = Column(Integer)
    nb_enregistrements      = Column(Numeric(18, 0))
    sha256_archive          = Column(String(64))
    chiffrement             = Column(String(30), server_default="AES-256")
    duree_secondes          = Column(Integer)
    date_sauvegarde         = Column(DateTime(timezone=True), server_default=func.now())
    retention_jours         = Column(Integer, server_default="90")

    def __repr__(self):
        return f"<SauvegardeSysteme {self.type_sauvegarde} {self.statut}>"


# ── FilDiscussion ─────────────────────────────────────────────────
class FilDiscussion(Base):
    """Fils de discussion messagerie inter-notaires (migration 026)."""
    __tablename__ = "fil_discussion"

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sujet             = Column(String(300), nullable=False)
    type_fil          = Column(String(50), nullable=False)
    chambre_notariale = Column(String(200))
    parcelle_id       = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"))
    dossier_ref       = Column(String(100))
    cree_par          = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    est_archive       = Column(Boolean, server_default="false")
    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    updated_at        = Column(DateTime(timezone=True))

    def __repr__(self):
        return f"<FilDiscussion {self.sujet[:50]}>"


# ── MessageNotarial ───────────────────────────────────────────────
class MessageNotarial(Base):
    """Messages sécurisés inter-notaires (migration 026)."""
    __tablename__ = "message_notarial"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fil_id           = Column(UUID(as_uuid=True), ForeignKey("fil_discussion.id"))
    expediteur_id    = Column(UUID(as_uuid=True), ForeignKey("notary_registry.id"), nullable=False)
    destinataire_id  = Column(UUID(as_uuid=True), ForeignKey("notary_registry.id"))
    sujet            = Column(String(300))
    corps            = Column(Text, nullable=False)
    priorite         = Column(String(20), server_default="normale")
    corps_chiffre    = Column(Text)
    statut           = Column(String(30), server_default="envoye")
    sha256_message   = Column(String(64), nullable=False)
    sha256_prev      = Column(String(64))
    acte_ref_id      = Column(UUID(as_uuid=True))
    envoye_at        = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<MessageNotarial statut={self.statut}>"


# ── BordereauEnvoi ────────────────────────────────────────────────
class BordereauEnvoi(Base):
    """Bordereaux d'envoi inter-services BOR-YYYY-NNNNNNN (migration 021)."""
    __tablename__ = "bordereau_envoi"

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    numero_bordereau     = Column(String(30), nullable=False, unique=True)
    date_envoi           = Column(DateTime(timezone=True), server_default=func.now())
    service_expediteur   = Column(String(50), nullable=False)
    service_destinataire = Column(String(50), nullable=False)
    dossiers_transmis    = Column(ARRAY(String), server_default="ARRAY[]::varchar[]")
    signataire_id        = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    signature_pki        = Column(Text)
    recu_par_id          = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    recu_le              = Column(DateTime(timezone=True))
    pdf_url              = Column(Text)

    def __repr__(self):
        return f"<BordereauEnvoi {self.numero_bordereau}>"


# ── MigrationHistorique ───────────────────────────────────────────
class MigrationHistorique(Base):
    """Orchestrateur pipeline WF30 (migration 013)."""
    __tablename__ = "migration_historique"

    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code_migration      = Column(String(50), nullable=False, unique=True)
    source              = Column(String(200), nullable=False)
    description         = Column(Text)
    periode_couverte    = Column(String(100))
    commune_id          = Column(UUID(as_uuid=True), ForeignKey("communes.id"))
    statut              = Column(String(30), server_default="initie")
    nb_lignes_source    = Column(Integer, server_default="0")
    nb_importees        = Column(Integer, server_default="0")
    nb_valides          = Column(Integer, server_default="0")
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True))

    def __repr__(self):
        return f"<MigrationHistorique {self.code_migration} {self.statut}>"


# ── PublicationsJO ────────────────────────────────────────────────
class PublicationJO(Base):
    """Publications au Journal Officiel (migration 001)."""
    __tablename__ = "publications_jo"

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    numero_jo         = Column(String(50), nullable=False)
    date_parution     = Column(DateTime(timezone=True), nullable=False)
    type_contenu      = Column(String(50), nullable=False)
    entite_ref_id     = Column(UUID(as_uuid=True))
    entite_ref_type   = Column(String(100))
    rnaf_id           = Column(UUID(as_uuid=True), ForeignKey("rnaf.id"))
    titre             = Column(String(500))
    contenu_extrait   = Column(Text)
    sha256_contenu    = Column(String(64))
    publie_par        = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at        = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<PublicationJO {self.numero_jo}>"


# ── HuissierAgree ─────────────────────────────────────────────────
class HuissierAgree(Base):
    """Huissiers titulaires de charge (migration 020)."""
    __tablename__ = "huissier_agree"

    id                        = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id                   = Column(UUID(as_uuid=True), ForeignKey("users.id"), unique=True)
    numero_charge             = Column(String(50), nullable=False, unique=True)
    nom_huissier              = Column(String(200), nullable=False)
    tgi_rattachement          = Column(UUID(as_uuid=True), ForeignKey("greffe_judiciaire.id"), nullable=False)
    date_prestation_serment   = Column(Date, nullable=False)
    adresse_etude             = Column(Text)
    statut                    = Column(String(20), server_default="actif")
    date_suspension           = Column(Date)
    motif_suspension          = Column(Text)
    created_at                = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<HuissierAgree {self.numero_charge}>"


# ── LeveeHypotheque ───────────────────────────────────────────────
class LeveeHypotheque(Base):
    """Mainlevées officielles tracées SHA-256 (migration 011)."""
    __tablename__ = "levee_hypotheque"

    id                    = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hypotheque_dossier_id = Column(UUID(as_uuid=True), ForeignKey("hypotheque_dossier.id"), unique=True)
    hypotheque_id         = Column(UUID(as_uuid=True), ForeignKey("hypotheques.id"), nullable=False)
    mortgage_registry_id  = Column(UUID(as_uuid=True), ForeignKey("mortgage_registry.id"))
    motif                 = Column(String(50), nullable=False)
    montant_solde         = Column(Numeric(18, 2))
    acte_mainlevee_ref    = Column(String(200), nullable=False)
    notaire_id            = Column(UUID(as_uuid=True), ForeignKey("notary_registry.id"))
    sha256_mainlevee      = Column(String(64), nullable=False)
    date_levee            = Column(DateTime(timezone=True), server_default=func.now())
    enregistre_par        = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    def __repr__(self):
        return f"<LeveeHypotheque {self.motif}>"
