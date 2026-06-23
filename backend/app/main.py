from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.sql import func
from .core.database import Base

class Parcelle(Base):
    __tablename__ = "m_parcelles_rnp"

    id = Column(Integer, primary_key=True, index=True)
    nicad = Column(String, unique=True, index=True)
    region_code = Column(String)
    commune_code = Column(String)
    ilot = Column(Integer)
    lettre_parcelle = Column(String)
    nom_titulaire = Column(String)
    
    # Coordonnées pour PostGIS
    latitude = Column(Float)
    longitude = Column(Float)
    
    # Métadonnées de conformité
    arrete = Column(String)
    est_publie_jo = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# Note : Vous pouvez ajouter d'autres classes ici (Regions, Communes, etc.)