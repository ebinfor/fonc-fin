"""
FONCIER+ -- Modèles SQLAlchemy pour le module Banque
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Numeric, Boolean, Text, func
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base

class HypothequeDossier(Base):
    __tablename__ = "hypotheque_dossier"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parcelle_id = Column(UUID(as_uuid=True), nullable=False)
    rnp_parcelle_id = Column(UUID(as_uuid=True), nullable=False)
    debiteur_id = Column(UUID(as_uuid=True), nullable=False)
    banque_id = Column(UUID(as_uuid=True), nullable=False)
    montant_fcfa = Column(Numeric(18, 2), nullable=False)
    taux_interet = Column(Numeric(5, 2), nullable=True)
    duree_mois = Column(Numeric(5, 2), nullable=True)
    ccfm_validation_id = Column(String(50), nullable=True)
    statut = Column(String(30), default="initie")
    mortgage_registry_id = Column(UUID(as_uuid=True), nullable=True)
    sha256_dossier = Column(String(64), nullable=False)
    cree_par = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class HypothequeLegacy(Base):
    __tablename__ = "hypotheques"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parcelle_id = Column(UUID(as_uuid=True), nullable=False)
    montant = Column(Numeric(18, 2), nullable=False)
    statut = Column(String(30), default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class AutorisationBancaire(Base):
    __tablename__ = "autorisation_bancaire"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hypotheque_dossier_id = Column(UUID(as_uuid=True), nullable=False)
    banque_holder_id = Column(UUID(as_uuid=True), nullable=False)
    type_operation = Column(String(50), nullable=False)
    statut = Column(String(30), default="accordee")
    conditions = Column(Text, nullable=True)
    montant_garanti_fcfa = Column(Numeric(18, 2), nullable=True)
    valide_jusqu_au = Column(DateTime(timezone=True), nullable=True)
    sha256_autorisation = Column(String(64), nullable=False)
    accordee_par = Column(UUID(as_uuid=True), nullable=False)
    accordee_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class LeveeHypotheque(Base):
    __tablename__ = "levee_hypotheque"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hypotheque_dossier_id = Column(UUID(as_uuid=True), nullable=True)
    hypotheque_id = Column(UUID(as_uuid=True), nullable=False)
    mortgage_registry_id = Column(UUID(as_uuid=True), nullable=False)
    motif = Column(String(50), nullable=False)
    montant_solde = Column(Numeric(18, 2), nullable=True)
    acte_mainlevee_ref = Column(String(200), nullable=True)
    notaire_id = Column(UUID(as_uuid=True), nullable=True)
    sha256_mainlevee = Column(String(64), nullable=False)
    date_levee = Column(DateTime(timezone=True), server_default=func.now())
    enregistre_par = Column(UUID(as_uuid=True), nullable=False)
