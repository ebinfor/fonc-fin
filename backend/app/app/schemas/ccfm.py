from pydantic import BaseModel, Field, EmailStr
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from app.models.ccfm import ModePaiementCCFM

# Données requises pour créer une demande
class CCFMDemandeCreate(BaseModel):
    nom_prenom: str = Field(..., max_length=200, description="Nom et prénom du demandeur")
    nip_passeport: str = Field(..., max_length=50, description="NIP ou Numéro de passeport")
    date_naissance: date
    lieu_naissance: str = Field(..., max_length=200, description="Lieu de naissance")
    nationalite: str = Field(default="NE", max_length=2)
    adresse: str = Field(..., description="Adresse de résidence")
    telephone: str = Field(..., max_length=20, description="Numéro de téléphone valide")
    email: EmailStr | None = Field(default=None)
    localite: str = Field(..., max_length=200, description="Ville ou Commune (ex: Niamey Commune 1)")
    lot: str = Field(..., max_length=50, description="Numéro du lot")
    parcelle: str = Field(..., max_length=50, description="Numéro de la parcelle")
    superficie_m2: Decimal = Field(..., max_digits=12, decimal_places=2, description="Superficie mesurée")
    mode_paiement: ModePaiementCCFM = Field(..., description="Opérateur ou méthode de paiement")
    reference_paiement: str = Field(..., max_length=200, description="Référence unique de la transaction")

# Structure de la réponse renvoyée par l'API (Output)
class CCFMDemandeResponse(BaseModel):
    ccfm_id: UUID
    nus: str
    reference_ccfm: str | None
    nom_prenom: str
    localite: str
    lot: str
    parcelle: str
    etat: str
    date_enregistrement: datetime

    class Config:
        from_attributes = True  # Permet à Pydantic de lire directement les objets SQLAlchemy