"""
FONCIER+ v3.4.7 — Modèles SQLAlchemy : Hiérarchie parcellaire & Antifraude
Tables : regions, communes, arretes_urbanisme, lotissements, ilots,
         parcelles, parcel_versions, parcel_lineage, nicad_registry,
         conflits_parcellaires, annulations_parcelles, refontes_lotissements,
         bgu_geojson_master
"""

import enum
import uuid
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Enum, Float,
    ForeignKey, Integer, Numeric, String, Text, UniqueConstraint,
    event, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


# ─────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────

class StatutLotissement(str, enum.Enum):
    ACTIF = "actif"
    REFONTE = "refonte"
    ARCHIVE = "archive"

class StatutParcelle(str, enum.Enum):
    ACTIVE = "active"
    SUBDIVISEE = "subdivisee"
    ANCIENNE = "ancienne"
    ANNULE = "annule"
    ARCHIVE = "archive"
    EN_ATTENTE_VALIDATION = "en_attente_validation"

class StatutVersion(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVE = "archive"

class TypeConflit(str, enum.Enum):
    SUPERPOSITION = "superposition"
    DEPLACEMENT = "deplacement"
    SURFACE = "surface"
    INCOHERENCE = "incoherence"

class GraviteConflit(str, enum.Enum):
    CRITIQUE = "critique"       # blocage immédiat
    A_VERIFIER = "a_verifier"   # alerte uniquement

class StatutConflit(str, enum.Enum):
    OUVERT = "ouvert"
    EN_TRAITEMENT = "en_traitement"
    RESOLU = "resolu"
    BLOQUE = "bloque"

class TypeCommune(str, enum.Enum):
    URBAINE = "urbaine"
    RURALE = "rurale"


# ─────────────────────────────────────────────
# 1. RÉGIONS
# ─────────────────────────────────────────────

class Region(Base):
    __tablename__ = "regions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code_region = Column(String(2), nullable=False, unique=True, index=True,
                         comment="RR — ex: 03 (Dosso)")
    nom_region = Column(String(100), nullable=False)
    chef_lieu = Column(String(100))
    geom = Column(Geometry("POLYGON", srid=4326))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relations
    communes = relationship("Commune", back_populates="region", lazy="dynamic")
    # users = relationship("User", back_populates="region", lazy="dynamic")  # Neutralisé pour compilation ORM

    def __repr__(self):
        return f"<Region {self.code_region} — {self.nom_region}>"


# ─────────────────────────────────────────────
# 2. COMMUNES
# ─────────────────────────────────────────────

class Commune(Base):
    __tablename__ = "communes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    region_id = Column(UUID(as_uuid=True), ForeignKey("regions.id"), nullable=False, index=True)
    code_commune = Column(String(2), nullable=False, index=True,
                          comment="CC — unique dans la région")
    nom_commune = Column(String(150), nullable=False)
    geom = Column(Geometry("POLYGON", srid=4326))
    type_commune = Column(Enum(TypeCommune), default=TypeCommune.URBAINE)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("region_id", "code_commune", name="uq_commune_region"),
    )

    # Relations
    region = relationship("Region", back_populates="communes")
    arretes = relationship("ArreteUrbanisme", back_populates="commune", lazy="dynamic")

    def __repr__(self):
        return f"<Commune {self.code_commune} — {self.nom_commune}>"


# ─────────────────────────────────────────────
# 3. ARRÊTÉS D'URBANISME
# ─────────────────────────────────────────────

class ArreteUrbanisme(Base):
    __tablename__ = "arretes_urbanisme"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    commune_id = Column(UUID(as_uuid=True), ForeignKey("communes.id"), nullable=False, index=True)
    numero_arrete = Column(String(100), nullable=False, unique=True)
    code_arrete = Column(String(7), nullable=False, unique=True, index=True,
                         comment="ARR — ex: AR0045")
    date_signature = Column(DateTime(timezone=True), nullable=False)
    objet = Column(Text)
    statut = Column(String(20), default="actif")
    sha256_document = Column(String(64), comment="Hash SHA-256 du document PDF signé")
    signe_par = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Règle métier : 1 arrêté → 1 lotissement (UNIQUE sur arretes_urbanisme.id côté lotissement)
    # Relations
    commune = relationship("Commune", back_populates="arretes")
    lotissement = relationship("Lotissement", back_populates="arrete", uselist=False)
    signataire = relationship("User", foreign_keys=[signe_par])

    def __repr__(self):
        return f"<ArreteUrbanisme {self.code_arrete}>"


# ─────────────────────────────────────────────
# 4. LOTISSEMENTS
# ─────────────────────────────────────────────

class Lotissement(Base):
    __tablename__ = "lotissements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # RÈGLE : 1 arrêté → 1 lotissement (UNIQUE)
    arrete_id = Column(UUID(as_uuid=True), ForeignKey("arretes_urbanisme.id"),
                       nullable=False, unique=True, index=True)
    nom_lotissement = Column(String(200), nullable=False)
    surface_totale_m2 = Column(Numeric(15, 4), nullable=False)
    geom = Column(Geometry("POLYGON", srid=4326))
    statut = Column(Enum(StatutLotissement), default=StatutLotissement.ACTIF)
    # Contrainte métier : 50 à 200 îlots
    nb_ilots_attendus = Column(Integer,
                               CheckConstraint("nb_ilots_attendus BETWEEN 50 AND 200",
                                               name="ck_ilots_range"),
                               default=50)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relations
    arrete = relationship("ArreteUrbanisme", back_populates="lotissement")
    ilots = relationship("Ilot", back_populates="lotissement", lazy="dynamic")
    refontes_source = relationship("RefonteLotissement",
                                   foreign_keys="RefonteLotissement.lotissement_ancien_id",
                                   back_populates="lotissement_ancien")
    refontes_cible = relationship("RefonteLotissement",
                                  foreign_keys="RefonteLotissement.lotissement_nouveau_id",
                                  back_populates="lotissement_nouveau")

    def __repr__(self):
        return f"<Lotissement {self.nom_lotissement}>"


# ─────────────────────────────────────────────
# 5. ÎLOTS
# ─────────────────────────────────────────────

class Ilot(Base):
    __tablename__ = "ilots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lotissement_id = Column(UUID(as_uuid=True), ForeignKey("lotissements.id"),
                             nullable=False, index=True)
    code_ilot = Column(String(3), nullable=False, comment="III — ex: 089")
    geom = Column(Geometry("POLYGON", srid=4326))
    surface_m2 = Column(Numeric(12, 4))
    # Contrainte métier : 1 à 30 parcelles par îlot
    nb_parcelles_max = Column(Integer,
                               CheckConstraint("nb_parcelles_max BETWEEN 1 AND 30",
                                               name="ck_parcelles_range"),
                               default=30)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("lotissement_id", "code_ilot", name="uq_ilot_lotissement"),
    )

    # Relations
    lotissement = relationship("Lotissement", back_populates="ilots")
    parcelles = relationship("Parcelle", back_populates="ilot", lazy="dynamic")

    def __repr__(self):
        return f"<Ilot {self.code_ilot}>"


# ─────────────────────────────────────────────
# 6. PARCELLES
# ─────────────────────────────────────────────

class Parcelle(Base):
    __tablename__ = "parcelles"

    # ID GeoJSON stable — ne change jamais, même après subdivision
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ilot_id = Column(UUID(as_uuid=True), ForeignKey("ilots.id"), nullable=False, index=True)

    # NICAD unique — format RR-CC-ARR-III-PPP[S]
    nicad = Column(String(30), nullable=False, unique=True, index=True)
    geojson_id = Column(String(100), nullable=False, unique=True,
                         comment="Identifiant GeoJSON stable — ne jamais modifier")

    statut = Column(Enum(StatutParcelle), default=StatutParcelle.EN_ATTENTE_VALIDATION)
    surface_m2 = Column(Numeric(12, 4), nullable=False)
    geom = Column(Geometry("POLYGON", srid=4326))

    # Lien vers la version active (circulaire — géré au niveau applicatif)
    version_active_id = Column(UUID(as_uuid=True), ForeignKey("parcel_versions.id"),
                                nullable=True)

    # Gel judiciaire ❄️ (Justice module)
    is_gele = Column(Boolean, default=False, nullable=False)
    gele_par_litige_id = Column(UUID(as_uuid=True), nullable=True)

    cree_par = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relations
    ilot = relationship("Ilot", back_populates="parcelles")
    versions = relationship("ParcelVersion",
                             foreign_keys="ParcelVersion.parcelle_id",
                             back_populates="parcelle",
                             order_by="ParcelVersion.numero_version")
    version_active = relationship("ParcelVersion",
                                   foreign_keys=[version_active_id])
    createur = relationship("User", foreign_keys=[cree_par])
    nicad_entry = relationship("NicadRegistry", back_populates="parcelle", uselist=False)
    enfants = relationship("ParcelLineage",
                           foreign_keys="ParcelLineage.parent_id",
                           back_populates="parent")
    parents = relationship("ParcelLineage",
                           foreign_keys="ParcelLineage.enfant_id",
                           back_populates="enfant")
    conflits_a = relationship("ConflitParcellaire",
                              foreign_keys="ConflitParcellaire.parcelle_a_id")
    conflits_b = relationship("ConflitParcellaire",
                              foreign_keys="ConflitParcellaire.parcelle_b_id")
    annulation = relationship("AnnulationParcelle", back_populates="parcelle", uselist=False)
    bgu_master = relationship("BGUGeoJSONMaster", back_populates="parcelle", uselist=False)

    def __repr__(self):
        return f"<Parcelle NICAD={self.nicad} statut={self.statut}>"


# ─────────────────────────────────────────────
# 7. PARCEL_VERSIONS — historique complet
# ─────────────────────────────────────────────

class ParcelVersion(Base):
    """
    RÈGLE : modification directe interdite.
    Toute modification crée une nouvelle version.
    1 seule version ACTIVE par NICAD.
    """
    __tablename__ = "parcel_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parcelle_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                          nullable=False, index=True)
    numero_version = Column(Integer, nullable=False, default=1)
    statut_version = Column(Enum(StatutVersion), default=StatutVersion.ACTIVE)

    # Géométrie et surface originales conservées pour audit
    surface_originale_m2 = Column(Numeric(12, 4), nullable=False)
    geom_originale = Column(Geometry("POLYGON", srid=4326), nullable=False)

    motif_modification = Column(Text, nullable=False)
    sha256_version = Column(String(64), nullable=False,
                             comment="SHA-256 : hash(nicad|surface|geom|motif|timestamp)")

    modifie_par = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    valide_par = Column(UUID(as_uuid=True), ForeignKey("users.id"))

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("parcelle_id", "numero_version", name="uq_version_parcelle"),
    )

    # Relations
    parcelle = relationship("Parcelle", foreign_keys=[parcelle_id],
                             back_populates="versions")
    auteur = relationship("User", foreign_keys=[modifie_par])
    valideur = relationship("User", foreign_keys=[valide_par])

    def __repr__(self):
        return f"<ParcelVersion v{self.numero_version} — {self.statut_version}>"


# ─────────────────────────────────────────────
# 8. PARCEL_LINEAGE — filiation des subdivisions
# ─────────────────────────────────────────────

class ParcelLineage(Base):
    """
    RÈGLES SUBDIVISIONS :
    - 1 niveau maximum de subdivision
    - 2 filles maximum (suffixes a et b uniquement)
    - Parcelle parent → statut SUBDIVISEE
    """
    __tablename__ = "parcel_lineage"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                        nullable=False, index=True)
    enfant_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                        nullable=False, unique=True)

    # Suffixe : 'a' ou 'b' uniquement
    suffixe = Column(String(1), nullable=False)

    # Niveau : toujours 1 (subdivision limitée à 1 niveau)
    niveau_subdivision = Column(Integer, nullable=False, default=1)

    autorise_par = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("parent_id", "suffixe", name="uq_lineage_parent_suffixe"),
        CheckConstraint("suffixe IN ('a', 'b')", name="ck_suffixe_ab"),
        CheckConstraint("niveau_subdivision = 1", name="ck_niveau_1"),
    )

    # Relations
    parent = relationship("Parcelle", foreign_keys=[parent_id], back_populates="enfants")
    enfant = relationship("Parcelle", foreign_keys=[enfant_id], back_populates="parents")
    autoriseur = relationship("User", foreign_keys=[autorise_par])

    def __repr__(self):
        return f"<ParcelLineage parent→enfant({self.suffixe})>"


# ─────────────────────────────────────────────
# 9. NICAD_REGISTRY
# ─────────────────────────────────────────────

class NicadRegistry(Base):
    """
    Registre central des NICAD.
    NICAD invariable même après subdivision ou correction mineure.
    Nouveau NICAD uniquement si refonte totale.
    """
    __tablename__ = "nicad_registry"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nicad = Column(String(30), nullable=False, unique=True, index=True)
    parcelle_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                          nullable=False, unique=True)

    # Décomposition du NICAD pour recherche rapide
    code_region = Column(String(2), nullable=False, index=True)
    code_commune = Column(String(2), nullable=False, index=True)
    code_arrete = Column(String(7), nullable=False, index=True)
    code_ilot = Column(String(3), nullable=False)
    code_parcelle = Column(String(3), nullable=False)
    suffixe = Column(String(1), nullable=True,
                     comment="a ou b pour subdivisions — null pour parcelles originales")

    genere_par = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relations
    parcelle = relationship("Parcelle", back_populates="nicad_entry")
    generateur = relationship("User", foreign_keys=[genere_par])

    def __repr__(self):
        return f"<NicadRegistry {self.nicad}>"


# ─────────────────────────────────────────────
# 10. CONFLITS_PARCELLAIRES — antifraude
# ─────────────────────────────────────────────

class ConflitParcellaire(Base):
    """
    RÈGLES ANTIFRAUDE :
    - Superposition critique → blocage immédiat
    - Déplacement → blocage + fraude potentielle
    - Intersection mineure → A_VERIFIER
    Aucun acte notarial/hypothèque sur parcelle CRITIQUE.
    """
    __tablename__ = "conflits_parcellaires"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parcelle_a_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                            nullable=False, index=True)
    parcelle_b_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                            nullable=True, index=True,
                            comment="Null si déplacement détecté sans voisin identifié")

    type_conflit = Column(Enum(TypeConflit), nullable=False)
    gravite = Column(Enum(GraviteConflit), nullable=False)
    surface_intersection_m2 = Column(Numeric(12, 4), default=0)

    # Détails techniques PostGIS
    st_relation = Column(String(50), comment="ST_Intersects / ST_Contains / ST_Within")
    seuil_applique_m2 = Column(Numeric(10, 4),
                                comment="Seuil utilisé pour qualification critique/a_verifier")

    statut = Column(Enum(StatutConflit), default=StatutConflit.OUVERT)
    sha256_geom = Column(String(64), comment="Hash de la géométrie d'intersection")

    description = Column(Text)
    detecte_at = Column(DateTime(timezone=True), server_default=func.now())
    traite_par = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    traite_at = Column(DateTime(timezone=True))

    # Relations
   # Relations
    parcelle_a = relationship("Parcelle", foreign_keys=[parcelle_a_id])
    parcelle_b = relationship("Parcelle", foreign_keys=[parcelle_b_id], overlaps="conflits_b")
    traiteur = relationship("User", foreign_keys=[traite_par])
    def __repr__(self):
        return f"<ConflitParcellaire {self.type_conflit} — {self.gravite}>"


# ─────────────────────────────────────────────
# 11. ANNULATIONS_PARCELLES
# ─────────────────────────────────────────────

class AnnulationParcelle(Base):
    """
    RÈGLE : DELETE physique INTERDIT.
    Annulation = statut ANNULE + enregistrement ici.
    Validation multi-niveaux obligatoire.
    """
    __tablename__ = "annulations_parcelles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parcelle_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                          nullable=False, unique=True, index=True)

    motif = Column(Text, nullable=False)
    base_juridique = Column(String(200), comment="Texte de loi ou décision de référence")
    preuve_document = Column(String(500), comment="Chemin S3 ou URL du document de preuve")
    sha256_preuve = Column(String(64), nullable=False,
                            comment="Hash SHA-256 du document de preuve")

    # Validation multi-niveaux
    valide_niveau1 = Column(UUID(as_uuid=True), ForeignKey("users.id"),
                             comment="Superviseur")
    valide_niveau1_at = Column(DateTime(timezone=True))
    valide_niveau2 = Column(UUID(as_uuid=True), ForeignKey("users.id"),
                             comment="Autorité centrale / Directeur Urbanisme")
    valide_niveau2_at = Column(DateTime(timezone=True))

    annule_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relations
    parcelle = relationship("Parcelle", back_populates="annulation")
    valideur1 = relationship("User", foreign_keys=[valide_niveau1])
    valideur2 = relationship("User", foreign_keys=[valide_niveau2])

    def __repr__(self):
        return f"<AnnulationParcelle motif={self.motif[:30]}>"


# ─────────────────────────────────────────────
# 12. REFONTES_LOTISSEMENTS
# ─────────────────────────────────────────────

class RefonteLotissement(Base):
    """
    RÈGLE : autorisée uniquement par Autorité centrale.
    Nouvel arrêté obligatoire.
    Surface totale doit être cohérente avec ancien plan.
    """
    __tablename__ = "refontes_lotissements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lotissement_ancien_id = Column(UUID(as_uuid=True), ForeignKey("lotissements.id"),
                                    nullable=False, index=True)
    lotissement_nouveau_id = Column(UUID(as_uuid=True), ForeignKey("lotissements.id"),
                                     nullable=False, unique=True)
    nouvel_arrete_id = Column(UUID(as_uuid=True), ForeignKey("arretes_urbanisme.id"),
                               nullable=False)

    motif_refonte = Column(Text, nullable=False)
    surface_ancien_m2 = Column(Numeric(15, 4), nullable=False)
    surface_nouveau_m2 = Column(Numeric(15, 4), nullable=False)
    # Vérification de cohérence surface (tolérance ± 1%)
    surface_coherence_ok = Column(Boolean, nullable=False,
                                   comment="True si écart surface < 1%")
    ecart_surface_pct = Column(Float)

    autorisee_par = Column(UUID(as_uuid=True), ForeignKey("users.id"),
                            nullable=False,
                            comment="Doit être DIRECTEUR_URBANISME ou MINISTRE")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relations
    lotissement_ancien = relationship("Lotissement",
                                       foreign_keys=[lotissement_ancien_id],
                                       back_populates="refontes_source")
    lotissement_nouveau = relationship("Lotissement",
                                        foreign_keys=[lotissement_nouveau_id],
                                        back_populates="refontes_cible")
    nouvel_arrete = relationship("ArreteUrbanisme", foreign_keys=[nouvel_arrete_id])
    autoriseur = relationship("User", foreign_keys=[autorisee_par])

    def __repr__(self):
        return f"<RefonteLotissement {self.motif_refonte[:30]}>"


# ─────────────────────────────────────────────
# 13. BGU_GEOJSON_MASTER — plan scellé
# ─────────────────────────────────────────────

class BGUGeoJSONMaster(Base):
    """
    Source de vérité géospatiale scellée (PostGIS + blockchain).
    ID GeoJSON stable — ne jamais modifier après scellement.
    """
    __tablename__ = "bgu_geojson_master"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parcelle_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                          nullable=False, unique=True, index=True)

    # ID stable lié à la parcelle
    geojson_id = Column(String(100), nullable=False, unique=True,
                         comment="Identifiant GeoJSON stable — invariable")

    geom = Column(Geometry("POLYGON", srid=4326), nullable=False)
    sha256_geom = Column(String(64), nullable=False,
                          comment="SHA-256 de la géométrie WKT normalisée")

    # Scellement
    scelle = Column(Boolean, default=False, nullable=False)
    scelle_at = Column(DateTime(timezone=True))
    scelle_par = Column(UUID(as_uuid=True), ForeignKey("users.id"))

    # Ancrage blockchain
    blockchain_hash = Column(String(128),
                              comment="Hash d'ancrage blockchain (ex: txid Ethereum)")
    blockchain_network = Column(String(50), default="internal",
                                 comment="Réseau blockchain utilisé")
    blockchain_timestamp = Column(DateTime(timezone=True))

    version = Column(Integer, default=1, nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relations
    parcelle = relationship("Parcelle", back_populates="bgu_master")
    scelleur = relationship("User", foreign_keys=[scelle_par])

    def __repr__(self):
        return f"<BGUGeoJSONMaster {self.geojson_id} scelle={self.scelle}>"
