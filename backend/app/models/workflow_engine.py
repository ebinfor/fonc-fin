"""
FONCIER+ v3.4.7 — Modèles SQLAlchemy : Moteur Workflow Formel
Migration 007

Le moteur workflow rend le système juridiquement exécutoire.
Sans lui, les validations restent manuelles et contournables.

Trois composants :
  workflow_definition  → Définition des types de workflows (modèles réutilisables)
  workflow_step_def    → Étapes obligatoires de chaque type
  workflow_instance    → Instance en cours d'un workflow pour une entité réelle
  workflow_step_log    → Historique de chaque étape franchie
  workflow_signature   → Signatures numériques par étape
  workflow_escalade    → Escalade si délai dépassé

Workflows couverts (alignés sur les 13 modules v3.4.7) :
  WF-RNAF    : BROUILLON → EN_VERIFICATION → EN_SCELLEMENT → SCELLE → PUBLIE
  WF-RNP     : INIT → INSTRUIRE → SAISIE → VALIDER → GENERER_NICAD
  WF-CCFM    : DEMANDE → VERIFICATION → CONSTAT → CERTIFICAT → ARCHIVE
  WF-NOTAIRE : REDACTION → VERIFICATION → SIGNATURE → ENREGISTREMENT → ARCHIVE
  WF-JUSTICE : SAISINE → INSTRUCTION → AUDIENCE → DECISION → NOTIFICATION
  WF-TRANSFERT : DEMANDE → CCFM_CHECK → NOTAIRE → ENREGISTREMENT → ANNF
  WF-BGU     : IMPORT → CONTROLE → VALIDATION → SCELLEMENT → PUBLICATION
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Enum,
    ForeignKey, Integer, Interval, Numeric, String,
    Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


# ─────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────

class TypeWorkflow(str, enum.Enum):
    RNAF        = "RNAF"
    RNP         = "RNP"
    CCFM        = "CCFM"
    NOTAIRE     = "NOTAIRE"
    JUSTICE     = "JUSTICE"
    TRANSFERT   = "TRANSFERT"
    BGU         = "BGU"
    URBANISME   = "URBANISME"
    ANNF        = "ANNF"
    DAR         = "DAR"

class StatutInstance(str, enum.Enum):
    EN_COURS    = "en_cours"
    EN_ATTENTE  = "en_attente"    # bloqué sur une étape
    SUSPENDU    = "suspendu"      # intervention externe requise
    COMPLETE    = "complete"
    REJETE      = "rejete"
    ANNULE      = "annule"
    EXPIRE      = "expire"        # délai dépassé sans action

class StatutEtape(str, enum.Enum):
    EN_ATTENTE  = "en_attente"
    EN_COURS    = "en_cours"
    VALIDEE     = "validee"
    REJETEE     = "rejetee"
    IGNOREE     = "ignoree"       # étape optionnelle non franchie
    EXPIREE     = "expiree"

class TypeValidation(str, enum.Enum):
    APPROBATION    = "approbation"
    SIGNATURE      = "signature"
    SCELLEMENT     = "scellement"
    HORODATAGE     = "horodatage"
    CONSTAT        = "constat"
    PUBLICATION    = "publication"

class TypeEscalade(str, enum.Enum):
    RELANCE        = "relance"
    ESCALADE_HAUT  = "escalade_haut"
    AUTO_REJET     = "auto_rejet"


# ─────────────────────────────────────────────
# 1. WORKFLOW_DEFINITION — Modèles réutilisables
# ─────────────────────────────────────────────

class WorkflowDefinition(Base):
    """
    Définit un type de workflow : son nom, ses règles globales,
    et la liste ordonnée de ses étapes.
    Immuable après activation (is_locked = True).
    """
    __tablename__ = "workflow_definition"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    type_workflow = Column(Enum(TypeWorkflow), nullable=False, unique=True)
    nom           = Column(String(200), nullable=False)
    description   = Column(Text)
    version       = Column(Integer, default=1, nullable=False)

    # Délai global maximum (en jours)
    delai_max_jours = Column(Integer, nullable=True,
                              comment="NULL = pas de délai")

    # Peut-on rejeter à n'importe quelle étape ?
    rejet_possible_partout = Column(Boolean, default=True)

    # Signature numérique requise sur l'étape finale ?
    signature_finale_requise = Column(Boolean, default=True)

    # Archivage ANNF automatique à la complétion ?
    annf_auto = Column(Boolean, default=True)

    # Verrouillé = plus modifiable (instances en cours)
    is_locked    = Column(Boolean, default=False)
    is_active    = Column(Boolean, default=True)

    cree_par   = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relations
    etapes    = relationship("WorkflowStepDef", back_populates="definition",
                              order_by="WorkflowStepDef.ordre",
                              cascade="all, delete-orphan")
    instances = relationship("WorkflowInstance", back_populates="definition",
                              lazy="dynamic")

    def __repr__(self):
        return f"<WorkflowDefinition {self.type_workflow} v{self.version}>"


# ─────────────────────────────────────────────
# 2. WORKFLOW_STEP_DEF — Étapes d'un workflow
# ─────────────────────────────────────────────

class WorkflowStepDef(Base):
    """
    Définit une étape d'un workflow.
    Chaque étape a : un rôle requis, un type de validation,
    un délai, et des conditions d'entrée/sortie.
    """
    __tablename__ = "workflow_step_def"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    definition_id = Column(UUID(as_uuid=True),
                            ForeignKey("workflow_definition.id"),
                            nullable=False, index=True)

    # Position dans le workflow (1, 2, 3...)
    ordre        = Column(Integer, nullable=False)
    code_etape   = Column(String(50), nullable=False,
                           comment="ex: SOUMETTRE, VERIFIER, SCELLER, PUBLIER")
    nom_etape    = Column(String(200), nullable=False)
    description  = Column(Text)

    # Rôle requis pour valider cette étape
    role_requis  = Column(String(100), nullable=False,
                           comment="ex: DIRECTEUR_URBANISME, CHEF_CCFM")
    # Rôle alternatif (si le premier est absent)
    role_backup  = Column(String(100))

    type_validation = Column(Enum(TypeValidation), nullable=False)

    # Étape optionnelle ?
    est_obligatoire = Column(Boolean, default=True)

    # Délai max pour cette étape (en heures)
    delai_max_heures = Column(Integer, nullable=True)

    # Condition d'entrée (expression évaluée côté applicatif)
    condition_entree = Column(Text,
                               comment="ex: rnaf.statut == SCELLE")
    # Condition de sortie (validation)
    condition_sortie = Column(Text)

    # Documents requis à cette étape
    documents_requis = Column(JSONB, default=list,
                               comment="Liste des types de documents obligatoires")

    # Si rejeté à cette étape, retour à l'étape N
    retour_etape_rejet = Column(Integer, nullable=True,
                                 comment="NULL = rejet définitif")

    # Action automatique déclenchée après validation
    action_post_validation = Column(String(100),
                                     comment="ex: PUBLIER_JO, CREER_TRANSACTION_BLOCK")

    __table_args__ = (
        UniqueConstraint("definition_id", "ordre",
                          name="uq_step_def_ordre"),
        UniqueConstraint("definition_id", "code_etape",
                          name="uq_step_def_code"),
    )

    # Relations
    definition = relationship("WorkflowDefinition", back_populates="etapes")

    def __repr__(self):
        return f"<WorkflowStepDef {self.code_etape} ordre={self.ordre}>"


# ─────────────────────────────────────────────
# 3. WORKFLOW_INSTANCE — Instance en cours
# ─────────────────────────────────────────────

class WorkflowInstance(Base):
    """
    Instance d'un workflow pour une entité réelle.
    1 entité = 1 instance active à la fois.
    Toutes les transitions sont tracées dans workflow_step_log.
    """
    __tablename__ = "workflow_instance"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    definition_id = Column(UUID(as_uuid=True),
                            ForeignKey("workflow_definition.id"),
                            nullable=False, index=True)

    # Entité concernée (polymorphique)
    entite_type = Column(String(50), nullable=False,
                          comment="rnaf|rnp_demande|demande_ccfm|acte_notarie|litige...")
    entite_id   = Column(UUID(as_uuid=True), nullable=False, index=True)

    # Dénormalisé pour accès rapide
    nicad       = Column(String(30), nullable=True,
                          comment="NICAD si workflow lié à une parcelle")
    parcelle_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                          nullable=True)

    statut = Column(Enum(StatutInstance), default=StatutInstance.EN_COURS,
                    nullable=False, index=True)

    # Étape courante
    etape_courante_ordre = Column(Integer, default=1, nullable=False)
    etape_courante_code  = Column(String(50))

    # Métadonnées
    contexte_json = Column(JSONB, default=dict,
                            comment="Données contextuelles de l'instance")

    # Délai
    date_debut     = Column(DateTime(timezone=True), server_default=func.now())
    date_echeance  = Column(DateTime(timezone=True),
                             comment="Calculée : date_debut + delai_max_jours")
    date_completion = Column(DateTime(timezone=True))

    # Acteur courant attendu
    attendu_de_role = Column(String(100))
    attendu_de_user = Column(UUID(as_uuid=True), ForeignKey("users.id"),
                              nullable=True)

    # Rejet
    motif_rejet = Column(Text)
    rejete_par  = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    rejete_at   = Column(DateTime(timezone=True))

    # SHA-256 de l'état final (si COMPLETE)
    sha256_completion = Column(String(64))

    demarre_par = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        # Une seule instance EN_COURS par entité et type de workflow
        UniqueConstraint("definition_id", "entite_id",
                          name="uq_instance_active",
                          # Note : partiel — géré par trigger (SQL partiel index)
                          ),
    )

    # Relations
    definition = relationship("WorkflowDefinition", back_populates="instances")
    steps      = relationship("WorkflowStepLog", back_populates="instance",
                               order_by="WorkflowStepLog.created_at",
                               cascade="all, delete-orphan")
    signatures = relationship("WorkflowSignature", back_populates="instance",
                               lazy="dynamic")
    escalades  = relationship("WorkflowEscalade", back_populates="instance",
                               lazy="dynamic")

    def __repr__(self):
        return (f"<WorkflowInstance {self.entite_type}={self.entite_id} "
                f"statut={self.statut} etape={self.etape_courante_code}>")


# ─────────────────────────────────────────────
# 4. WORKFLOW_STEP_LOG — Historique des étapes
# ─────────────────────────────────────────────

class WorkflowStepLog(Base):
    """
    Enregistrement de chaque étape franchie dans un workflow.
    Immuable après création (WORM).
    """
    __tablename__ = "workflow_step_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    instance_id = Column(UUID(as_uuid=True),
                          ForeignKey("workflow_instance.id"),
                          nullable=False, index=True)

    # Étape
    step_ordre = Column(Integer, nullable=False)
    step_code  = Column(String(50), nullable=False)
    step_nom   = Column(String(200))

    statut_etape = Column(Enum(StatutEtape), nullable=False)

    # Acteur
    acteur_id   = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    role_acteur = Column(String(100))

    # Décision
    commentaire = Column(Text)
    decision    = Column(String(20),
                          comment="VALIDE|REJETE|SUSPENDU|IGNORE")

    # Documents soumis à cette étape
    documents_soumis = Column(JSONB, default=list)

    # Données de transition
    donnees_avant = Column(JSONB,
                            comment="Snapshot de l'entité avant cette étape")
    donnees_apres = Column(JSONB,
                            comment="Snapshot de l'entité après cette étape")

    # Durée de l'étape
    duree_heures = Column(Numeric(8, 2),
                           comment="Durée effective en heures")

    # SHA-256 de l'étape (chaîne inviolable)
    sha256_step  = Column(String(64), nullable=False)
    sha256_prev  = Column(String(64),
                           comment="SHA-256 de l'étape précédente")

    # Horodatage légal
    created_at = Column(DateTime(timezone=True), server_default=func.now(),
                         nullable=False)

    # Relations
    instance  = relationship("WorkflowInstance", back_populates="steps")
    signature = relationship("WorkflowSignature",
                              primaryjoin="WorkflowSignature.step_log_id==WorkflowStepLog.id",
                              foreign_keys="WorkflowSignature.step_log_id",
                              back_populates="step_log",
                              uselist=False)

    def __repr__(self):
        return (f"<WorkflowStepLog {self.step_code} "
                f"statut={self.statut_etape} acteur={self.role_acteur}>")


# ─────────────────────────────────────────────
# 5. WORKFLOW_SIGNATURE — Signatures numériques
# ─────────────────────────────────────────────

class WorkflowSignature(Base):
    """
    Signature numérique attachée à une étape de workflow.
    Clé de la valeur juridique du système.
    """
    __tablename__ = "workflow_signature"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    instance_id  = Column(UUID(as_uuid=True),
                           ForeignKey("workflow_instance.id"),
                           nullable=False, index=True)
    step_log_id  = Column(UUID(as_uuid=True),
                           ForeignKey("workflow_step_log.id"),
                           nullable=False, unique=True)

    # Signataire
    signataire_id   = Column(UUID(as_uuid=True), ForeignKey("users.id"),
                              nullable=False)
    role_signataire = Column(String(100), nullable=False)

    # Contenu signé
    contenu_signe   = Column(Text, nullable=False,
                              comment="Représentation textuelle de ce qui est signé")
    sha256_contenu  = Column(String(64), nullable=False)

    # Signature (simulée en v3.4.7 — PKI réelle en production)
    signature_b64   = Column(Text, nullable=False,
                              comment="Signature base64 (HMAC-SHA256 ou RSA)")
    algorithme      = Column(String(50), default="HMAC-SHA256")
    certificat_ref  = Column(String(200),
                              comment="Référence au certificat PKI si applicable")

    # Horodatage légal
    horodatage_at   = Column(DateTime(timezone=True), server_default=func.now(),
                              nullable=False)
    horodatage_tsp  = Column(String(500),
                              comment="Token TSP (Time-Stamp Protocol) si disponible")

    # Validation
    est_valide      = Column(Boolean, default=True, nullable=False)
    verifie_at      = Column(DateTime(timezone=True))
    verifie_par     = Column(UUID(as_uuid=True), ForeignKey("users.id"))

    # Relations
    instance  = relationship("WorkflowInstance", back_populates="signatures",
                              foreign_keys=[instance_id])
    step_log  = relationship("WorkflowStepLog", back_populates="signature",
                              foreign_keys=[step_log_id])
    signataire = relationship("User", foreign_keys=[signataire_id])

    def __repr__(self):
        return (f"<WorkflowSignature {self.role_signataire} "
                f"valide={self.est_valide}>")


# ─────────────────────────────────────────────
# 6. WORKFLOW_ESCALADE — Escalade sur délai dépassé
# ─────────────────────────────────────────────

class WorkflowEscalade(Base):
    """
    Escalade automatique quand une étape dépasse son délai.
    Types : relance, escalade_haut, auto_rejet.
    """
    __tablename__ = "workflow_escalade"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    instance_id  = Column(UUID(as_uuid=True),
                           ForeignKey("workflow_instance.id"),
                           nullable=False, index=True)
    step_code    = Column(String(50), nullable=False)
    step_ordre   = Column(Integer, nullable=False)

    type_escalade = Column(Enum(TypeEscalade), nullable=False)

    # Délai dépassé de combien ?
    retard_heures = Column(Numeric(8, 2), nullable=False)

    # Destinataire de l'escalade
    notifie_role = Column(String(100))
    notifie_user = Column(UUID(as_uuid=True), ForeignKey("users.id"))

    message_escalade = Column(Text, nullable=False)
    est_traitee      = Column(Boolean, default=False)
    traitee_at       = Column(DateTime(timezone=True))

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relations
    instance = relationship("WorkflowInstance", back_populates="escalades")

    def __repr__(self):
        return (f"<WorkflowEscalade {self.type_escalade} "
                f"step={self.step_code} retard={self.retard_heures}h>")
