"""
FONCIER+ v3.4.7 — Modèles SQLAlchemy : Droits Fonciers Versionnés
Migration 005 — Aligné sur migrations 001-004

Principe fondamental :
  Un propriétaire n'écrase JAMAIS un autre.
  Tout changement de droit = nouvelle version.
  UPDATE proprietaire_id → INTERDIT.

Tables nouvelles :
  right_holder          → titulaire foncier (personne physique/morale)
  parcel_right_version  → version de droit foncier (avec date_debut/date_fin)
  property_transfer     → historique des transferts de propriété
  notary_registry       → registre des notaires agréés
  mortgage_registry     → hypothèques (enrichit hypotheques existant)
  parcel_servitude      → servitudes foncières
  parcel_dispute        → litiges fonciers (enrichit litiges existant)
  annf_right_archive    → archivage ANNF des droits

Contrainte critique :
  La somme des pourcentages de droits ACTIFS sur une parcelle = 100%.
  Vérifiée par trigger PostgreSQL.

Requête temporelle critique (§XIII du spec) :
  "Qui possédait cette parcelle le 4 juillet 2008 ?"
  → SELECT * FROM parcel_right_version
    WHERE rnp_parcelle_id = X
      AND date_debut_validite <= '2008-07-04'
      AND (date_fin_validite IS NULL OR date_fin_validite >= '2008-07-04')
      AND statut IN ('actif', 'historique')
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Enum,
    ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


# ─────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────

class TypePersonne(str, enum.Enum):
    PHYSIQUE     = "physique"
    MORALE       = "morale"
    ETAT         = "etat"
    COLLECTIVITE = "collectivite"
    BANQUE       = "banque"

class TypeDroit(str, enum.Enum):
    PROPRIETE   = "propriete"
    USUFRUIT    = "usufruit"
    BAIL        = "bail"
    HYPOTHEQUE  = "hypotheque"
    SERVITUDE   = "servitude"
    OCCUPATION  = "occupation"

class StatutDroit(str, enum.Enum):
    ACTIF      = "actif"
    HISTORIQUE = "historique"   # clos par une nouvelle version
    CONTESTE   = "conteste"     # litige ouvert
    ANNULE     = "annule"       # annulé par Justice

class OrigineOperation(str, enum.Enum):
    CESSION           = "cession"
    VENTE             = "vente"
    HERITAGE          = "heritage"
    DONATION          = "donation"
    DECISION_JUDICIAIRE = "decision_judiciaire"
    CREATION_INITIALE = "creation_initiale"
    SUBDIVISION       = "subdivision"
    REFONTE           = "refonte"

class TypeTransfert(str, enum.Enum):
    VENTE               = "vente"
    DONATION            = "donation"
    HERITAGE            = "heritage"
    CESSION             = "cession"
    DECISION_JUDICIAIRE = "decision_judiciaire"
    EXPROPRIATION       = "expropriation"

class StatutHypotheque(str, enum.Enum):
    ACTIVE = "active"
    LEVEE  = "levee"
    EXPIREE = "expiree"
    CONTENTIEUX = "contentieux"

class TypeServitude(str, enum.Enum):
    PASSAGE      = "passage"
    CANALISATION = "canalisation"
    ELECTRICITE  = "electricite"
    EAU          = "eau"
    AUTRE        = "autre"

class TypeLitigeDroit(str, enum.Enum):
    LIMITE    = "limite"
    PROPRIETE = "propriete"
    HERITAGE  = "heritage"
    FRAUDE    = "fraude"
    HYPOTHEQUE = "hypotheque"
    SERVITUDE = "servitude"

class StatutLitigeDroit(str, enum.Enum):
    OUVERT         = "ouvert"
    EN_INSTRUCTION = "en_instruction"
    CLOS           = "clos"
    ABANDONNE      = "abandonne"

class AutoriteSaisie(str, enum.Enum):
    JUSTICE = "justice"
    CCFM    = "ccfm"
    DOMAINE = "domaine"

class SourceArchiveDroit(str, enum.Enum):
    NOTAIRE = "notaire"
    JUSTICE = "justice"
    DOMAINE = "domaine"
    CCFM    = "ccfm"
    ADMIN   = "admin"


# ─────────────────────────────────────────────
# 1. RIGHT_HOLDER — Titulaire foncier
#    Personne physique ou morale pouvant posséder des droits.
#    Stable dans le temps : une parcelle change de propriétaire,
#    mais le titulaire reste traçable.
# ─────────────────────────────────────────────

class RightHolder(Base):
    __tablename__ = "right_holders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    type_personne = Column(Enum(TypePersonne), nullable=False, index=True)

    # Personne physique
    nom     = Column(String(150))
    prenom  = Column(String(150))

    # Personne morale / Entité
    raison_sociale = Column(String(300))

    # Identifiants officiels
    numero_identite  = Column(String(50), index=True,
                               comment="CNI, passeport, acte de naissance...")
    numero_registre  = Column(String(50), index=True,
                               comment="RCCM, NIF, numéro organisme...")

    # Coordonnées
    adresse = Column(Text)
    contact = Column(String(200))

    # Lien optionnel vers RNP (si le titulaire est lui-même une entité immatriculée)
    rnp_personne_id = Column(UUID(as_uuid=True),
                              ForeignKey("rnp_demandes.id"),
                              nullable=True)

    # Statut actif / inactif (décès, dissolution...)
    est_actif = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relations
    droits_actifs = relationship(
        "ParcelRightVersion",
        primaryjoin="and_(RightHolder.id==ParcelRightVersion.titulaire_id,"
                    "ParcelRightVersion.statut=='actif')",
        foreign_keys="ParcelRightVersion.titulaire_id",
        lazy="dynamic",
        viewonly=True,
    )
    transferts_sortants = relationship(
        "PropertyTransfer",
        foreign_keys="PropertyTransfer.ancien_titulaire_id",
        back_populates="ancien_titulaire",
        lazy="dynamic",
    )
    transferts_entrants = relationship(
        "PropertyTransfer",
        foreign_keys="PropertyTransfer.nouveau_titulaire_id",
        back_populates="nouveau_titulaire",
        lazy="dynamic",
    )

    def __repr__(self):
        label = self.raison_sociale or f"{self.nom} {self.prenom}"
        return f"<RightHolder {self.type_personne} — {label}>"


# ─────────────────────────────────────────────
# 2. NOTARY_REGISTRY — Registre des notaires
#    Un acte sans notaire traçable est juridiquement fragile.
# ─────────────────────────────────────────────

class NotaryRegistry(Base):
    __tablename__ = "notary_registry"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Lien vers User existant (rôle NOTAIRE)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"),
                     nullable=True, unique=True,
                     comment="Si le notaire a un compte FONCIER+")

    nom_notaire      = Column(String(200), nullable=False)
    numero_agrement  = Column(String(50), nullable=False, unique=True,
                               comment="Numéro d'agrément officiel Niger")
    chambre_notariale = Column(String(200))
    region_id        = Column(UUID(as_uuid=True), ForeignKey("regions.id"))
    adresse          = Column(Text)
    contact          = Column(String(200))

    # Statut agréé / suspendu / radié
    statut_agrement  = Column(String(20), default="agree",
                               comment="agree|suspendu|radie")

    date_agrement    = Column(DateTime(timezone=True))
    date_fin_agrement = Column(DateTime(timezone=True))

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relations
    transferts = relationship("PropertyTransfer", back_populates="notaire",
                               lazy="dynamic")
    droits_certifies = relationship("ParcelRightVersion",
                                     foreign_keys="ParcelRightVersion.certifie_par_notaire",
                                     lazy="dynamic")

    def __repr__(self):
        return f"<NotaryRegistry {self.nom_notaire} — {self.numero_agrement}>"


# ─────────────────────────────────────────────
# 3. PARCEL_RIGHT_VERSION — Version de droit foncier
#    Table centrale : registre réel des droits.
#    RÈGLE : jamais d'UPDATE sur une version active.
#    RÈGLE : toute modification = nouvelle version.
#    RÈGLE : somme des pourcentages ACTIFS = 100% (vérifiée par trigger).
#    REQUÊTE TEMPORELLE : date_debut_validite / date_fin_validite
# ─────────────────────────────────────────────

class ParcelRightVersion(Base):
    """
    Version de droit foncier sur une parcelle.

    Requête "Qui possédait X le 4 juillet 2008 ?" :
      SELECT * FROM parcel_right_version
      WHERE rnp_parcelle_id = :id
        AND date_debut_validite <= '2008-07-04'
        AND (date_fin_validite IS NULL OR date_fin_validite >= '2008-07-04')
      ORDER BY date_debut_validite;
    """
    __tablename__ = "parcel_right_version"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # FK vers rnp_demandes existant (migration 001)
    rnp_parcelle_id = Column(UUID(as_uuid=True), ForeignKey("rnp_demandes.id"),
                              nullable=False, index=True)

    # FK vers parcelles (migration 003) — dénormalisé pour requêtes rapides
    parcelle_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                          nullable=True, index=True)

    # FK vers rnaf — droit DOIT être lié à un arrêté foncier (§X du spec)
    rnaf_id = Column(UUID(as_uuid=True), ForeignKey("rnaf.id"),
                     nullable=False, index=True,
                     comment="Obligatoire — droit sans RNAF = juridiquement faible")

    # Titulaire
    titulaire_id = Column(UUID(as_uuid=True), ForeignKey("right_holders.id"),
                           nullable=False, index=True)

    type_droit = Column(Enum(TypeDroit), nullable=False)

    # Pourcentage de propriété (0 < x <= 100)
    # Somme de tous les droits ACTIFS sur une parcelle = 100% (trigger)
    pourcentage_droit = Column(Numeric(5, 2), nullable=False,
                                comment="Entre 0.01 et 100.00 — somme ACTIFS = 100")

    # Validité temporelle — clé de la requête historique
    date_debut_validite = Column(DateTime(timezone=True), nullable=False, index=True)
    date_fin_validite   = Column(DateTime(timezone=True), nullable=True, index=True,
                                  comment="NULL = droit encore actif")

    statut = Column(Enum(StatutDroit), default=StatutDroit.ACTIF,
                    nullable=False, index=True)

    # Origine du droit
    origine_operation = Column(Enum(OrigineOperation), nullable=False)
    acte_reference    = Column(String(200),
                                comment="Référence de l'acte source (numéro acte notarié, etc.)")

    # Lien vers actes_notaries existant (transaction.py)
    acte_notarie_id = Column(UUID(as_uuid=True), ForeignKey("actes_notaries.id"),
                              nullable=True)

    # Lien vers CCFM (validation post-publication)
    ccfm_validation_id = Column(UUID(as_uuid=True), ForeignKey("demandes_ccfm.id"),
                                 nullable=True)

    # Notaire ayant certifié
    certifie_par_notaire = Column(UUID(as_uuid=True), ForeignKey("notary_registry.id"),
                                   nullable=True)

    # Numéro de version (incrémenté à chaque nouvelle version pour la même parcelle/titulaire)
    numero_version = Column(Integer, nullable=False, default=1)

    # Lien vers la version précédente (traçabilité de la chaîne)
    version_precedente_id = Column(UUID(as_uuid=True),
                                    ForeignKey("parcel_right_version.id"),
                                    nullable=True)

    # SHA-256 de la version (inviolable)
    sha256_version = Column(String(64), nullable=False)

    # Qui a saisi / validé
    cree_par   = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    valide_par = Column(UUID(as_uuid=True), ForeignKey("users.id"))

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "pourcentage_droit > 0 AND pourcentage_droit <= 100",
            name="ck_pourcentage_droit_range",
        ),
    )

    # Relations
    titulaire          = relationship("RightHolder", foreign_keys=[titulaire_id])
    acte_notarie       = relationship("ActeNotarie", foreign_keys=[acte_notarie_id])
    version_precedente = relationship("ParcelRightVersion",
                                       foreign_keys=[version_precedente_id],
                                       remote_side=[id])
    transferts         = relationship("PropertyTransfer",
                                       foreign_keys="PropertyTransfer.right_version_id",
                                       back_populates="right_version")
    archive_annf       = relationship("ANNFRightArchive", back_populates="right_version",
                                       uselist=False)
    disputes           = relationship("ParcelDispute", back_populates="right_version_contestee")

    def __repr__(self):
        return (f"<ParcelRightVersion RNP={self.rnp_parcelle_id} "
                f"type={self.type_droit} {self.pourcentage_droit}% "
                f"statut={self.statut}>")


# ─────────────────────────────────────────────
# 4. PROPERTY_TRANSFER — Transfert de propriété
#    Chaque transfert est tracé.
#    Lie : ancien titulaire → nouveau titulaire.
#    Sans ça : litiges insolubles.
# ─────────────────────────────────────────────

class PropertyTransfer(Base):
    __tablename__ = "property_transfers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Parcelle concernée
    rnp_parcelle_id = Column(UUID(as_uuid=True), ForeignKey("rnp_demandes.id"),
                              nullable=False, index=True)
    parcelle_id     = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                              nullable=True, index=True)

    # Titulaires
    ancien_titulaire_id  = Column(UUID(as_uuid=True), ForeignKey("right_holders.id"),
                                   nullable=False, index=True)
    nouveau_titulaire_id = Column(UUID(as_uuid=True), ForeignKey("right_holders.id"),
                                   nullable=False, index=True)

    # Pourcentage transféré
    pourcentage_transfere = Column(Numeric(5, 2), nullable=False)

    type_transfert  = Column(Enum(TypeTransfert), nullable=False)
    date_transfert  = Column(DateTime(timezone=True), nullable=False, index=True)
    acte_reference  = Column(String(200))

    # Lien vers notaire — obligatoire pour certains types
    notaire_id = Column(UUID(as_uuid=True), ForeignKey("notary_registry.id"),
                         nullable=True,
                         comment="Obligatoire pour vente, donation, cession")

    # Lien vers actes_notaries existant (transaction.py)
    acte_notarie_id = Column(UUID(as_uuid=True), ForeignKey("actes_notaries.id"),
                              nullable=True)

    # Lien vers CCFM
    ccfm_validation_id = Column(UUID(as_uuid=True), ForeignKey("demandes_ccfm.id"),
                                 nullable=True)

    # Lien vers la version de droit créée par ce transfert
    right_version_id = Column(UUID(as_uuid=True), ForeignKey("parcel_right_version.id"),
                               nullable=True)

    # Lien vers décision judiciaire (si transfert forcé)
    decision_judiciaire_id = Column(UUID(as_uuid=True),
                                     ForeignKey("decisions_judiciaires.id"),
                                     nullable=True)

    sha256_transfert = Column(String(64), nullable=False)

    enregistre_par = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "pourcentage_transfere > 0 AND pourcentage_transfere <= 100",
            name="ck_pct_transfere_range",
        ),
    )

    # Relations
    ancien_titulaire  = relationship("RightHolder", foreign_keys=[ancien_titulaire_id],
                                      back_populates="transferts_sortants")
    nouveau_titulaire = relationship("RightHolder", foreign_keys=[nouveau_titulaire_id],
                                      back_populates="transferts_entrants")
    notaire           = relationship("NotaryRegistry", back_populates="transferts")
    right_version     = relationship("ParcelRightVersion",
                                      foreign_keys=[right_version_id],
                                      back_populates="transferts")

    def __repr__(self):
        return f"<PropertyTransfer {self.type_transfert} {self.pourcentage_transfere}%>"


# ─────────────────────────────────────────────
# 5. MORTGAGE_REGISTRY — Hypothèques (enrichit hypotheques existant)
#    Les banques exigent la sécurité juridique.
#    Table complémentaire à hypotheques (transaction.py).
# ─────────────────────────────────────────────

class MortgageRegistry(Base):
    """
    Registre des hypothèques enrichi.
    Enrichit la table hypotheques existante (transaction.py)
    avec le lien RNAF, le versioning et la validation CCFM.
    """
    __tablename__ = "mortgage_registry"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Lien vers hypotheques existant (transaction.py)
    hypotheque_id = Column(UUID(as_uuid=True), ForeignKey("hypotheques.id"),
                            nullable=False, unique=True)

    # Lien vers rnp_demandes
    rnp_parcelle_id = Column(UUID(as_uuid=True), ForeignKey("rnp_demandes.id"),
                              nullable=False, index=True)

    # Lien vers parcelles (migration 003)
    parcelle_id = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                          nullable=True, index=True)

    # Titulaire bénéficiaire (banque)
    banque_holder_id = Column(UUID(as_uuid=True), ForeignKey("right_holders.id"),
                               nullable=False,
                               comment="FK vers right_holders type_personne=banque")

    montant        = Column(Numeric(18, 2), nullable=False)
    devise         = Column(String(3), default="XOF", nullable=False)
    date_debut     = Column(DateTime(timezone=True), nullable=False)
    date_fin       = Column(DateTime(timezone=True))

    statut = Column(Enum(StatutHypotheque), default=StatutHypotheque.ACTIVE,
                    nullable=False, index=True)

    reference_contrat = Column(String(200))

    # RNAF requis (droit lié à un arrêté foncier)
    rnaf_id = Column(UUID(as_uuid=True), ForeignKey("rnaf.id"), nullable=False)

    # Validation CCFM obligatoire
    ccfm_validation_id = Column(UUID(as_uuid=True), ForeignKey("demandes_ccfm.id"),
                                 nullable=True)

    # Mainlevée
    levee_at     = Column(DateTime(timezone=True))
    levee_par    = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    motif_levee  = Column(Text)
    acte_mainlevee = Column(String(200))

    sha256_contrat = Column(String(64))

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<MortgageRegistry {self.montant} {self.devise} statut={self.statut}>"


# ─────────────────────────────────────────────
# 6. PARCEL_SERVITUDE — Servitudes foncières
#    Souvent oubliée, mais une servitude peut bloquer une vente.
# ─────────────────────────────────────────────

class ParcelServitude(Base):
    __tablename__ = "parcel_servitude"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    rnp_parcelle_id = Column(UUID(as_uuid=True), ForeignKey("rnp_demandes.id"),
                              nullable=False, index=True)
    parcelle_id     = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                              nullable=True, index=True)

    type_servitude = Column(Enum(TypeServitude), nullable=False)

    # Bénéficiaire de la servitude
    beneficiaire_id = Column(UUID(as_uuid=True), ForeignKey("right_holders.id"),
                              nullable=True)
    beneficiaire_libre = Column(String(200),
                                 comment="Si le bénéficiaire n'est pas dans right_holders")

    date_debut    = Column(DateTime(timezone=True), nullable=False)
    date_fin      = Column(DateTime(timezone=True),
                            comment="NULL = servitude perpétuelle")
    acte_reference = Column(String(200))

    # Lien vers actes_notaries
    acte_notarie_id = Column(UUID(as_uuid=True), ForeignKey("actes_notaries.id"),
                              nullable=True)

    # RNAF requis
    rnaf_id = Column(UUID(as_uuid=True), ForeignKey("rnaf.id"), nullable=True)

    # Géométrie de la servitude (PostGIS — optionnelle)
    geom_servitude = Column(String(500),
                             comment="WKT de la zone de servitude (optionnel)")

    # Bloquante ou non pour les transactions
    bloque_transaction = Column(Boolean, default=False, nullable=False,
                                 comment="Si True → transaction_block créé automatiquement")

    est_active   = Column(Boolean, default=True, nullable=False)
    cree_par     = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    # Relation
    beneficiaire = relationship("RightHolder", foreign_keys=[beneficiaire_id])

    def __repr__(self):
        return f"<ParcelServitude {self.type_servitude} active={self.est_active}>"


# ─────────────────────────────────────────────
# 7. PARCEL_DISPUTE — Litige foncier sur droits
#    Historique des contestations.
#    Enrichit litiges existant (transaction.py) pour les droits.
# ─────────────────────────────────────────────

class ParcelDispute(Base):
    """
    Litige portant spécifiquement sur les droits de propriété.
    Enrichit litiges existant (qui gère le gel parcelle).
    Ici : le CONTENU juridique de la contestation.
    """
    __tablename__ = "parcel_disputes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    rnp_parcelle_id = Column(UUID(as_uuid=True), ForeignKey("rnp_demandes.id"),
                              nullable=False, index=True)
    parcelle_id     = Column(UUID(as_uuid=True), ForeignKey("parcelles.id"),
                              nullable=True, index=True)

    # Lien vers litiges existant (transaction.py)
    litige_id = Column(UUID(as_uuid=True), ForeignKey("litiges.id"),
                        nullable=True, unique=True)

    # Droit contesté
    right_version_contestee_id = Column(UUID(as_uuid=True),
                                         ForeignKey("parcel_right_version.id"),
                                         nullable=True)

    type_litige    = Column(Enum(TypeLitigeDroit), nullable=False)
    date_ouverture = Column(DateTime(timezone=True), nullable=False,
                             server_default=func.now())

    autorite_saisie = Column(Enum(AutoriteSaisie), nullable=False)

    statut = Column(Enum(StatutLitigeDroit), default=StatutLitigeDroit.OUVERT,
                    nullable=False, index=True)

    # Parties
    plaignant_id  = Column(UUID(as_uuid=True), ForeignKey("right_holders.id"),
                            nullable=True)
    defendeur_id  = Column(UUID(as_uuid=True), ForeignKey("right_holders.id"),
                            nullable=True)

    description       = Column(Text, nullable=False)
    decision_reference = Column(String(200))

    # Lien vers décision judiciaire finale
    decision_judiciaire_id = Column(UUID(as_uuid=True),
                                     ForeignKey("decisions_judiciaires.id"),
                                     nullable=True)

    date_cloture  = Column(DateTime(timezone=True))
    cree_par      = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())

    # Relations
    right_version_contestee = relationship("ParcelRightVersion",
                                            foreign_keys=[right_version_contestee_id],
                                            back_populates="disputes")
    plaignant  = relationship("RightHolder", foreign_keys=[plaignant_id])
    defendeur  = relationship("RightHolder", foreign_keys=[defendeur_id])

    def __repr__(self):
        return f"<ParcelDispute {self.type_litige} statut={self.statut}>"


# ─────────────────────────────────────────────
# 8. ANNF_RIGHT_ARCHIVE — Archivage ANNF des droits
#    ANNF = mémoire nationale.
#    Chaque version de droit doit être archivée.
#    WORM — immuable après scellement.
# ─────────────────────────────────────────────

class ANNFRightArchive(Base):
    """
    Archive ANNF d'une version de droit foncier.
    Lié à annf_archive_links (migration 004) via ida.
    Chaque droit archivé = 1 entrée ici + 1 entrée dans annf_archive_links.
    """
    __tablename__ = "annf_right_archive"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Droit archivé
    right_version_id = Column(UUID(as_uuid=True),
                               ForeignKey("parcel_right_version.id"),
                               nullable=False, unique=True)

    # Lien vers annf_archive_links (migration 004)
    annf_link_id = Column(UUID(as_uuid=True), ForeignKey("annf_archive_links.id"),
                           nullable=True)

    date_archivage = Column(DateTime(timezone=True), server_default=func.now(),
                             nullable=False)
    hash_integrite = Column(String(64), nullable=False,
                             comment="SHA-256 de la version archivée")
    version        = Column(Integer, default=1, nullable=False)

    source = Column(Enum(SourceArchiveDroit), nullable=False)

    # Snapshot du droit au moment de l'archivage
    snapshot_json = Column(JSONB, nullable=False)

    # WORM
    scelle     = Column(Boolean, default=False, nullable=False)
    scelle_at  = Column(DateTime(timezone=True))
    scelle_par = Column(UUID(as_uuid=True), ForeignKey("users.id"))

    archive_par = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # Relations
    right_version = relationship("ParcelRightVersion",
                                  back_populates="archive_annf")

    def __repr__(self):
        return f"<ANNFRightArchive v{self.version} scelle={self.scelle}>"
