"""
FONCIER+ v3.4.7 — Modèles SQLAlchemy : Moteur Workflow Formel
Migration 007

Contient uniquement les structures de tables de l'infrastructure
de validation multi-institutionnelle.
"""

import enum
import uuid
from datetime import datetime
from sqlalchemy import (
    Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text, func
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from app.core.database import Base


# ─────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────

class StatutInstance(str, enum.Enum):
    EN_ATTENTE = "EN_ATTENTE"
    EN_COURS = "EN_COURS"
    COMPLETE = "COMPLETE"
    SUSPENDU = "SUSPENDU"
    ANNULE = "ANNULE"


class StatutEtape(str, enum.Enum):
    ATTENTE = "ATTENTE"
    EN_COURS = "EN_COURS"
    VALIDEE = "VALIDEE"
    REJETEE = "REJETEE"


class TypeValidation(str, enum.Enum):
    AUTOMATIQUE = "AUTOMATIQUE"
    MANUELLE = "MANUELLE"
    SYSTEME = "SYSTEME"


# ─────────────────────────────────────────────────────────────
# TABLES / MODÈLES SQLALCHEMY
# ─────────────────────────────────────────────────────────────

class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, nullable=False)
    nom = Column(String(100), nullable=False)
    type_workflow = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    etapes = relationship("WorkflowStepDef", back_populates="definition", order_by="WorkflowStepDef.ordre")


class WorkflowStepDef(Base):
    __tablename__ = "workflow_step_defs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    definition_id = Column(UUID(as_uuid=True), ForeignKey("workflow_definitions.id", ondelete="CASCADE"), nullable=False)
    ordre = Column(Integer, nullable=False)
    code_etape = Column(String(50), nullable=False)
    nom_etape = Column(String(100), nullable=False)
    role_requis = Column(String(100), nullable=False)
    role_backup = Column(String(100), nullable=True)

    definition = relationship("WorkflowDefinition", back_populates="etapes")


class WorkflowInstance(Base):
    __tablename__ = "workflow_instances"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    definition_id = Column(UUID(as_uuid=True), ForeignKey("workflow_definitions.id"), nullable=False)
    entite_type = Column(String(50), nullable=False)
    entite_id = Column(String(255), nullable=False)
    demarre_par = Column(String(255), nullable=False)
    statut = Column(Enum(StatutInstance), default=StatutInstance.EN_COURS, nullable=False)
    etape_courante_ordre = Column(Integer, default=1, nullable=False)
    etape_courante_code = Column(String(50), nullable=True)
    attendu_de_role = Column(String(100), nullable=True)
    contexte_json = Column(JSONB, default=dict, nullable=False)
    sha256_completion = Column(String(64), nullable=True)
    date_completion = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    definition = relationship("WorkflowDefinition")


class WorkflowStepLog(Base):
    __tablename__ = "workflow_step_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id = Column(UUID(as_uuid=True), ForeignKey("workflow_instances.id", ondelete="CASCADE"), nullable=False)
    step_ordre = Column(Integer, nullable=False)
    step_code = Column(String(50), nullable=False)
    step_nom = Column(String(100), nullable=False)
    statut_etape = Column(Enum(StatutEtape), nullable=False)
    acteur_id = Column(String(255), nullable=False)
    role_acteur = Column(String(100), nullable=False)
    commentaire = Column(Text, nullable=True)
    decision = Column(String(50), nullable=False)
    donnees_avant = Column(JSONB, nullable=True)
    donnees_apres = Column(JSONB, nullable=True)
    sha256_step = Column(String(64), nullable=False)
    sha256_prev = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WorkflowSignature(Base):
    __tablename__ = "workflow_signatures"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id = Column(UUID(as_uuid=True), ForeignKey("workflow_instances.id"), nullable=False)
    signature = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WorkflowEscalade(Base):
    __tablename__ = "workflow_escalades"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id = Column(UUID(as_uuid=True), ForeignKey("workflow_instances.id"), nullable=False)
    raison = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())