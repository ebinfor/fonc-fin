"""
FONCIER+ v3.4.7 — Modèles SQLAlchemy : Workflows Inter-modules
Migration 004 — Aligné sur les 35 modèles existants (foncier.py / transaction.py / archives.py)

Tables nouvelles (complémentaires, jamais dupliquées) :
  rnaf_registre_workflow  → lien RNAF → arretes_fonciers + statut RNAF complet
  rnp_parcelle_link       → lien RNP → rnaf (rnaf_id obligatoire)
  bgu_parcel_status       → statut public BGU visible portail
  ccfm_suspension         → suspension CCFM → bloque transactions
  parcel_freeze           → gel/dégel (enrichit litiges existants)
  transaction_block       → blocage automatique actes/hypothèques
  justice_rnaf_decision   → décision Justice sur RNAF suspendu
  annf_archive_link       → lien ANNF → toutes entités archivables
  parcel_public_history   → historique public des statuts

Relations inter-tables existantes renforcées :
  rnaf.rnaf_id ← rnp_demandes.rnaf_id         (déjà FK, on enrichit)
  litiges.parcelle_id ← parcel_freeze          (extension de litiges)
  demandes_ccfm ← ccfm_suspension              (extension de demandes_ccfm)
  decisions_judiciaires ← justice_rnaf_decision (extension)
  rnaf ← annf_archive_link ← archivage ANNF
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Enum,
    ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


# ─────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────

class StatutRNAF(str, enum.Enum):
    """
    Cycle de vie RNAF.
    RÈGLE : RNP ne peut exister sans RNAF PUBLIE.
    RÈGLE : CCFM vérifie après PUBLIE.
    """
    BROUILLON       = "brouillon"
    EN_VERIFICATION = "en_verification"
    EN_SCELLEMENT   = "en_scellement"
    SCELLE          = "scelle"
    PUBLIE          = "publie"          # ← RNP autorisé à partir d'ici
    SUSPENDU        = "suspendu"        # ← CCFM ou Justice → blocage transactionnel
    ANNULE          = "annule"          # ← Justice ou Autorité centrale
    ARCHIVE         = "archive"

class StatutRNP(str, enum.Enum):
    """Statut RNP — lié au statut RNAF parent."""
    PROVISOIRE  = "provisoire"   # RNAF SCELLE mais pas encore PUBLIE
    ACTIF       = "actif"        # RNAF PUBLIE → RNP opérationnel
    SUSPENDU    = "suspendu"     # RNAF SUSPENDU → transactions bloquées
    ANNULE      = "annule"       # RNAF ANNULE
    ARCHIVE     = "archive"

class TypeBlockage(str, enum.Enum):
    CCFM_SUSPENSION   = "ccfm_suspension"
    JUSTICE_LITIGE    = "justice_litige"
    FRAUDE_DETECTEE   = "fraude_detectee"
    RNAF_SUSPENDU     = "rnaf_suspendu"
    ADMIN_MANUEL      = "admin_manuel"

class StatutBlockage(str, enum.Enum):
    ACTIF  = "actif"
    LEVE   = "leve"
    EXPIRE = "expire"

class StatutDecisionJusticeRNAF(str, enum.Enum):
    EN_INSTRUCTION  = "en_instruction"
    VALIDEE         = "validee"     # RNAF rétabli → dégel
    ANNULATION      = "annulation"  # RNAF annulé définitivement
    CLASSEE         = "classee"

class TypeArchiveANNF(str, enum.Enum):
    RNAF            = "rnaf"
    RNP             = "rnp"
    CCFM            = "ccfm"
    ACTE_NOTARIE    = "acte_notarie"
    DECISION_JUSTICE = "decision_justice"
    BGU_SCELLE      = "bgu_scelle"
    PLAN_CADASTRAL  = "plan_cadastral"


# ─────────────────────────────────────────────
# 1. RNAF_REGISTRE_WORKFLOW
#    Enrichit la table `rnaf` existante.
#    Porte le statut complet + liens JO + chaîne Urbanisme.
# ─────────────────────────────────────────────

class RNAFWorkflow(Base):
    """
    Extension du workflow RNAF.
    FK vers `rnaf` existant (table foncier.py → RNAF).
    Porte les métadonnées de publication JO et la chaîne
    Urbanisme → RNAF → JO → RNP.
    """
    __tablename__ = "rnaf_workflow"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Lien vers rnaf existant (FK)
    rnaf_id = Column(UUID(as_uuid=True), ForeignKey("rnaf.id"),
                     nullable=False, unique=True, index=True)

    # Lien vers l'arrêté foncier source (table arretes_fonciers existante)
    arrete_foncier_id = Column(UUID(as_uuid=True),
                                ForeignKey("arretes_fonciers.id"),
                                nullable=False, index=True)

    # Lien vers publication Journal Officiel (table publications_jo existante)
    publication_jo_id = Column(UUID(as_uuid=True),
                                ForeignKey("publications_jo.id"),
                                nullable=True,
                                comment="Obligatoire pour passer à PUBLIE")

    statut = Column(Enum(StatutRNAF), default=StatutRNAF.BROUILLON,
                    nullable=False, index=True)

    # Dates clés du cycle de vie
    date_soumission    = Column(DateTime(timezone=True))
    date_verification  = Column(DateTime(timezone=True))
    date_scellement    = Column(DateTime(timezone=True))
    date_publication   = Column(DateTime(timezone=True))
    date_suspension    = Column(DateTime(timezone=True))
    date_annulation    = Column(DateTime(timezone=True))

    motif_suspension   = Column(Text)
    motif_annulation   = Column(Text)

    # SHA-256 de l'état publié (inviolable)
    sha256_publie      = Column(String(64))

    # Acteurs
    soumis_par         = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    verifie_par        = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    scelle_par         = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    publie_par         = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    suspendu_par       = Column(UUID(as_uuid=True), ForeignKey("users.id"))

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # BUG-MOD-03 fix : relationships essentiels
    rnp_links   = relationship("RNPParcelleLink",
                               primaryjoin="RNAFWorkflow.rnaf_id == foreign(RNPParcelleLink.rnaf_id)",
                               lazy="noload")
    justice_decisions = relationship("JusticeRNAFDecision",
                                     back_populates="rnaf_workflow",
                                     lazy="noload")

    def __repr__(self):
        return f"<RNAFWorkflow rnaf={self.rnaf_id} statut={self.statut}>"


# ─────────────────────────────────────────────
# 2. RNP_PARCELLE_LINK
#    Lien RNP ↔ RNAF obligatoire.
#    RÈGLE : RNP ne peut exister sans RNAF PUBLIE.
# ─────────────────────────────────────────────

class RNPParcelleLink(Base):
    """
    Lien RNP → RNAF (obligatoire).
    Enrichit rnp_demandes (existant) avec le rnaf_id et le statut synchronisé.
    RÈGLE : si le RNAF est SUSPENDU → statut RNP = SUSPENDU automatiquement.
    RÈGLE : si le RNAF est ANNULE → statut RNP = ANNULE.
    """
    __tablename__ = "rnp_parcelle_link"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # FK vers rnp_demandes existant
    rnp_demande_id = Column(UUID(as_uuid=True), ForeignKey("rnp_demandes.id"),
                             nullable=False, unique=True, index=True)

    # FK vers rnaf — OBLIGATOIRE (RNP ne peut exister sans RNAF publié)
    rnaf_id = Column(UUID(as_uuid=True), ForeignKey("rnaf.id"),
                     nullable=False, index=True)

    # FK vers parcelles (migration 003)
    parcelle_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                          nullable=True, index=True)

    statut_rnp = Column(Enum(StatutRNP), default=StatutRNP.PROVISOIRE,
                         nullable=False, index=True)

    # Synchro automatique avec statut RNAF
    statut_rnaf_au_moment = Column(Enum(StatutRNAF),
                                    comment="Snapshot du statut RNAF lors de la dernière synchro")
    derniere_sync_at = Column(DateTime(timezone=True), server_default=func.now())

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "statut_rnp != 'actif' OR statut_rnaf_au_moment = 'publie'",
            name="ck_rnp_actif_necessite_rnaf_publie",
        ),
    )

    def __repr__(self):
        return f"<RNPParcelleLink rnp={self.rnp_demande_id} statut={self.statut_rnp}>"


# ─────────────────────────────────────────────
# 3. BGU_PARCEL_STATUS
#    Statut public BGU — portail citoyen.
# ─────────────────────────────────────────────

class BGUParcelStatus(Base):
    """
    Statut public des parcelles dans BGU.
    Visible sur le portail public (sans authentification).
    Synchronisé avec : statut RNAF, statut RNP, gel judiciaire.
    """
    __tablename__ = "bgu_parcel_status"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # FK vers assiettes_bgu existant
    assiette_bgu_id = Column(UUID(as_uuid=True), ForeignKey("assiettes_bgu.id"),
                              nullable=False, unique=True, index=True)

    # FK vers parcelles (migration 003) — nullable car assiette peut précéder la parcelle
    parcelle_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                          nullable=True, index=True)

    # Statut public affiché
    statut_public = Column(String(30), nullable=False, default="actif",
                            comment="actif|suspendu|gele|annule|en_verification")
    statut_detail  = Column(Text, comment="Message public optionnel")

    # Sources du statut (quelles tables l'ont déclenché)
    source_rnaf     = Column(Boolean, default=False)
    source_ccfm     = Column(Boolean, default=False)
    source_justice  = Column(Boolean, default=False)
    source_fraude   = Column(Boolean, default=False)

    # Dernière mise à jour publique
    derniere_maj_at = Column(DateTime(timezone=True), server_default=func.now())
    maj_par         = Column(UUID(as_uuid=True), ForeignKey("users.id"))

    # QR code public (déjà présent dans l'architecture CCFM)
    qr_code_url     = Column(String(500))

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<BGUParcelStatus statut={self.statut_public}>"


# ─────────────────────────────────────────────
# 4. CCFM_SUSPENSION
#    Suspension CCFM → déclenche blocage transactionnel.
#    Enrichit demandes_ccfm existant.
# ─────────────────────────────────────────────

class CCFMSuspension(Base):
    """
    Suspension émise par le module CCFM.
    RÈGLE : toute suspension active bloque les transactions
            (actes notariaux + hypothèques) sur la parcelle concernée.
    RÈGLE : la suspension peut déclencher une contestation Justice.
    Lié à demandes_ccfm (existant) et rnaf_workflow.
    """
    __tablename__ = "ccfm_suspension"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # FK vers demandes_ccfm existant
    demande_ccfm_id = Column(UUID(as_uuid=True), ForeignKey("demandes_ccfm.id"),
                              nullable=False, index=True)

    # FK vers rnaf_workflow — la suspension peut viser un RNAF
    rnaf_workflow_id = Column(UUID(as_uuid=True), ForeignKey("rnaf_workflow.id"),
                               nullable=True, index=True)

    # FK vers parcelles (migration 003)
    parcelle_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                          nullable=True, index=True)

    motif_suspension = Column(Text, nullable=False)
    base_juridique   = Column(String(200))
    sha256_preuve    = Column(String(64), nullable=False)

    # Acteurs
    emise_par    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    validee_par  = Column(UUID(as_uuid=True), ForeignKey("users.id"))

    # Cycle de vie
    est_active   = Column(Boolean, default=True, nullable=False)
    levee_par    = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    levee_at     = Column(DateTime(timezone=True))
    motif_levee  = Column(Text)

    # Lien vers décision judiciaire éventuelle
    justice_decision_id = Column(UUID(as_uuid=True),
                                  ForeignKey("decisions_judiciaires.id"),
                                  nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # BUG-MOD-03 fix
    parcelle   = relationship("Parcelle",
                              foreign_keys=[parcelle_id], lazy="noload")
    emetteur   = relationship("User",
                              foreign_keys=[emise_par], lazy="noload")

    def __repr__(self):
        return f"<CCFMSuspension active={self.est_active}>"


# ─────────────────────────────────────────────
# 5. PARCEL_FREEZE
#    Gel/dégel enrichi — s'appuie sur litiges existant.
#    Séparé de parcel_freeze migration 003 (is_gele booléen)
#    pour tracer l'historique complet des gels.
# ─────────────────────────────────────────────

class ParcelFreezeEvent(Base):
    """
    Événement de gel ou dégel d'une parcelle.
    Chaque gel/dégel est tracé ici (historique complet).
    Sources : Justice (litige), CCFM (suspension), Fraude.
    Lié à litiges existant (transaction.py → Litige).
    """
    __tablename__ = "parcel_freeze_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # FK vers parcelles (migration 003)
    parcelle_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                          nullable=False, index=True)

    # Source du gel
    type_gel = Column(String(30), nullable=False,
                      comment="justice|ccfm|fraude|admin")

    # FK vers litiges existant (transaction.py)
    litige_id = Column(UUID(as_uuid=True), ForeignKey("litiges.id"),
                        nullable=True)

    # FK vers ccfm_suspension
    suspension_ccfm_id = Column(UUID(as_uuid=True),
                                 ForeignKey("ccfm_suspension.id"),
                                 nullable=True)

    # Gel
    gele    = Column(Boolean, nullable=False,
                     comment="True = événement de gel, False = événement de dégel")
    motif   = Column(Text, nullable=False)
    gele_par = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    gele_at  = Column(DateTime(timezone=True), server_default=func.now())

    # Dégel (rempli si gele=False)
    degele_par = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    degele_at  = Column(DateTime(timezone=True))

    def __repr__(self):
        return f"<ParcelFreezeEvent {'GEL' if self.gele else 'DEGEL'} parcelle={self.parcelle_id}>"


# ─────────────────────────────────────────────
# 6. TRANSACTION_BLOCK
#    Blocage automatique des transactions.
#    Couvre : actes_notaries + hypotheques (existants).
# ─────────────────────────────────────────────

class TransactionBlock(Base):
    """
    Blocage automatique des transactions sur parcelle.
    Déclenché par : CCFM suspension, gel judiciaire, fraude détectée, RNAF suspendu.
    Vérification obligatoire avant toute écriture dans :
      - actes_notaries
      - hypotheques
      - dossiers_domaine
    """
    __tablename__ = "transaction_blocks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # FK vers parcelles
    parcelle_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                          nullable=False, index=True)

    type_blockage = Column(Enum(TypeBlockage), nullable=False)
    statut        = Column(Enum(StatutBlockage), default=StatutBlockage.ACTIF,
                            nullable=False, index=True)

    motif         = Column(Text, nullable=False)
    sha256_motif  = Column(String(64), comment="Hash du motif pour audit inviolable")

    # Sources (clés étrangères polymorphiques — une seule active)
    source_litige_id     = Column(UUID(as_uuid=True), ForeignKey("litiges.id"),
                                   nullable=True)
    source_ccfm_id       = Column(UUID(as_uuid=True), ForeignKey("ccfm_suspension.id"),
                                   nullable=True)
    source_conflit_id    = Column(UUID(as_uuid=True),
                                   ForeignKey("conflits_parcellaires.id"),
                                   nullable=True)
    source_rnaf_id       = Column(UUID(as_uuid=True), ForeignKey("rnaf_workflow.id"),
                                   nullable=True)

    # Cycle de vie
    pose_par    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    pose_at     = Column(DateTime(timezone=True), server_default=func.now())
    leve_par    = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    leve_at     = Column(DateTime(timezone=True))
    motif_levee = Column(Text)

    # Expiration automatique optionnelle
    expire_at   = Column(DateTime(timezone=True))

    def __repr__(self):
        return f"<TransactionBlock {self.type_blockage} — {self.statut}>"


# ─────────────────────────────────────────────
# 7. JUSTICE_RNAF_DECISION
#    Décision Justice sur RNAF suspendu.
#    Enrichit decisions_judiciaires existant.
# ─────────────────────────────────────────────

class JusticeRNAFDecision(Base):
    """
    Décision judiciaire spécifique au RNAF suspendu.
    Enrichit decisions_judiciaires (existant, transaction.py).
    RÈGLE :
      - VALIDEE → rétablissement RNAF + dégel parcelles concernées
      - ANNULATION → RNAF annulé + RNP annulé + blocage permanent
    """
    __tablename__ = "justice_rnaf_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # FK vers decisions_judiciaires existant (transaction.py)
    decision_judiciaire_id = Column(UUID(as_uuid=True),
                                     ForeignKey("decisions_judiciaires.id"),
                                     nullable=False, unique=True, index=True)

    # FK vers rnaf_workflow
    rnaf_workflow_id = Column(UUID(as_uuid=True), ForeignKey("rnaf_workflow.id"),
                               nullable=False, index=True)

    # FK vers ccfm_suspension à l'origine de la contestation
    ccfm_suspension_id = Column(UUID(as_uuid=True), ForeignKey("ccfm_suspension.id"),
                                 nullable=True)

    statut_decision = Column(Enum(StatutDecisionJusticeRNAF),
                              default=StatutDecisionJusticeRNAF.EN_INSTRUCTION,
                              nullable=False)

    # Contenu de la décision
    numero_decision  = Column(String(100), unique=True)
    date_decision    = Column(DateTime(timezone=True))
    motif_decision   = Column(Text, nullable=False)
    sha256_decision  = Column(String(64), nullable=False)

    # Effets sur RNAF
    retablit_rnaf    = Column(Boolean, default=False,
                               comment="True → RNAF repasse à PUBLIE")
    annule_rnaf      = Column(Boolean, default=False,
                               comment="True → RNAF passe à ANNULE définitivement")
    degele_parcelles = Column(Boolean, default=False,
                               comment="True → dégel automatique des parcelles liées")

    # Acteurs
    president_id    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    greffier_id     = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    notifie_at      = Column(DateTime(timezone=True))

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # BUG-MOD-03 fix
    rnaf_workflow = relationship("RNAFWorkflow",
                                 foreign_keys=[rnaf_workflow_id],
                                 back_populates="justice_decisions",
                                 lazy="noload")

    def __repr__(self):
        return f"<JusticeRNAFDecision {self.statut_decision} rnaf={self.rnaf_workflow_id}>"


# ─────────────────────────────────────────────
# 8. ANNF_ARCHIVE_LINK
#    Archivage ANNF — lien vers toutes entités archivables.
#    WORM + SHA-256. Enrichit la table archives existante.
# ─────────────────────────────────────────────

class ANNFArchiveLink(Base):
    """
    Lien d'archivage ANNF.
    Principe WORM (Write Once Read Many) : jamais de modification après scellement.
    Couvre toutes les entités archivables : RNAF, RNP, CCFM, actes, décisions, BGU.
    """
    __tablename__ = "annf_archive_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Type d'entité archivée
    type_archive = Column(Enum(TypeArchiveANNF), nullable=False, index=True)

    # Identifiant de l'entité source (UUID polymorphique)
    entite_id    = Column(UUID(as_uuid=True), nullable=False, index=True)
    entite_table = Column(String(100), nullable=False,
                           comment="Nom de la table source (ex: rnaf, actes_notaries)")

    # Contenu archivé (snapshot JSONB compressé)
    snapshot_json = Column(JSONB, nullable=False,
                            comment="État complet de l'entité au moment de l'archivage")
    sha256_snapshot = Column(String(64), nullable=False,
                              comment="SHA-256 du snapshot — inviolable WORM")

    # Numéro IDA (Identifiant d'Archive) — unique dans ANNF
    ida = Column(String(50), nullable=False, unique=True, index=True,
                  comment="Identifiant d'Archive unique ex: ANNF-2026-0001234")

    # Scellement WORM — une fois scellé, aucune modification possible
    scelle     = Column(Boolean, default=False, nullable=False)
    scelle_at  = Column(DateTime(timezone=True))
    scelle_par = Column(UUID(as_uuid=True), ForeignKey("users.id"))

    # Version de l'archive (ANNF est versionné)
    version    = Column(Integer, default=1, nullable=False)
    version_precedente_id = Column(UUID(as_uuid=True),
                                    ForeignKey("annf_archive_links.id"),
                                    nullable=True)

    archive_par = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("entite_id", "entite_table", "version",
                          name="uq_annf_entite_version"),
    )

    def __repr__(self):
        return f"<ANNFArchiveLink {self.type_archive} IDA={self.ida}>"


# ─────────────────────────────────────────────
# 9. PARCEL_PUBLIC_HISTORY
#    Historique public des statuts — portail citoyen.
# ─────────────────────────────────────────────

class ParcelPublicHistory(Base):
    """
    Historique public des changements de statut d'une parcelle.
    Visible sans authentification via le portail public et BGU.
    Alimenté automatiquement par chaque transition de statut.
    """
    __tablename__ = "parcel_public_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # FK vers parcelles (migration 003)
    parcelle_id   = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                            nullable=False, index=True)
    nicad         = Column(String(30), nullable=False, index=True,
                            comment="Dénormalisé pour requêtes publiques sans JOIN")

    statut_avant  = Column(String(50))
    statut_apres  = Column(String(50), nullable=False)

    # Module source du changement
    module_source = Column(String(30), nullable=False,
                            comment="rnaf|rnp|ccfm|justice|bgu|cadastre|admin")
    entite_id     = Column(UUID(as_uuid=True),
                            comment="ID de l'entité source (RNAF, litige, etc.)")

    motif_public  = Column(Text, comment="Message visible publiquement (sans données sensibles)")

    # Qui a déclenché (anonymisé pour le public — rôle seulement)
    role_acteur   = Column(String(50))

    created_at    = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    def __repr__(self):
        return f"<ParcelPublicHistory NICAD={self.nicad} {self.statut_avant}→{self.statut_apres}>"
