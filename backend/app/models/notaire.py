"""
FONCIER+ -- Modèles SQLAlchemy pour le module Notaire
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Numeric, Boolean, Text, func
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base

class EtudeNotariale(Base):
    __tablename__ = "etude_notariale"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    numero_parquet = Column(String(50), nullable=False, unique=True)
    nom_etude = Column(String(200), nullable=False)
    adresse = Column(Text, nullable=False)
    commune_id = Column(UUID(as_uuid=True), nullable=True)
    chambre_regionale = Column(String(200), nullable=True)
    statut = Column(String(20), default="active")
    date_agrement = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class MutationDossier(Base):
    __tablename__ = "mutation_dossier"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parcelle_id = Column(UUID(as_uuid=True), nullable=False)
    rnp_parcelle_id = Column(UUID(as_uuid=True), nullable=False)
    type_mutation = Column(String(50), nullable=False)
    cedant_id = Column(UUID(as_uuid=True), nullable=False)
    acquereur_id = Column(UUID(as_uuid=True), nullable=False)
    pourcentage_cede = Column(Numeric(5, 2), nullable=False)
    prix_fcfa = Column(Numeric(18, 2), nullable=True)
    statut = Column(String(30), default="initie")
    notaire_id = Column(UUID(as_uuid=True), nullable=False)
    ccfm_validation_id = Column(String(50), nullable=True)
    sha256_dossier = Column(String(64), nullable=False)
    cree_par = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class Succession(Base):
    __tablename__ = "succession"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    de_cujus_id = Column(UUID(as_uuid=True), nullable=False)
    type_succession = Column(String(50), nullable=False)
    statut = Column(String(30), default="ouverte")
    date_ouverture = Column(DateTime(timezone=True), nullable=False)
    reference_acte = Column(String(200), nullable=True)
    sha256_succession = Column(String(64), nullable=False)
    cree_par = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class SuccessionParcelle(Base):
    __tablename__ = "succession_parcelle"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    succession_id = Column(UUID(as_uuid=True), nullable=False)
    parcelle_id = Column(UUID(as_uuid=True), nullable=False)
    en_indivision = Column(Boolean, default=True)
    est_traite = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class SuccessionHeritier(Base):
    __tablename__ = "succession_heritier"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    succession_id = Column(UUID(as_uuid=True), nullable=False)
    heritier_id = Column(UUID(as_uuid=True), nullable=False)
    lien_parente = Column(String(50), nullable=False)
    part_pct = Column(Numeric(5, 2), nullable=False)
    a_accepte = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
