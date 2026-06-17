import asyncio
import os
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# 1. Extraction et nettoyage de l'URL Railway
railway_db_url = os.getenv("DATABASE_URL")
if not railway_db_url:
    print("❌ Erreur : DATABASE_URL introuvable dans l'environnement Railway.")
    exit(1)

# Sécurité : Forcer le driver asyncpg si Railway renvoie du postgresql:// au lieu de postgresql+asyncpg://
if railway_db_url.startswith("postgresql://"):
    railway_db_url = railway_db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# 2. Création d'un moteur SQL indépendant connecté à Railway
engine = create_async_engine(railway_db_url, echo=False)
AsyncSessionLocalIndependent = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 3. Import des modèles uniquement (sans toucher à leur session)
import backend.app.models.users as users_mod
UserClass = getattr(users_mod, 'User', getattr(users_mod, 'Utilisateur', None))

async def main():
    if not UserClass:
        print("❌ Classe User ou Utilisateur introuvable.")
        return

    async with AsyncSessionLocalIndependent() as session:
        hashed_password = pwd_context.hash("MonMotDePasseSecurise123")
        
        admin = UserClass(
            email="kader@exemple.com",
            actif=True
        )
        
        # Test dynamique des colonnes de mot de passe
        if hasattr(UserClass, 'password'): admin.password = hashed_password
        elif hasattr(UserClass, 'password_hash'): admin.password_hash = hashed_password
        elif hasattr(UserClass, 'mot_de_passe'): admin.mot_de_passe = hashed_password
        else: admin.password = hashed_password

        # Test dynamique des colonnes de nom
        if hasattr(UserClass, 'full_name'): admin.full_name = "Kader Hamidou Zakou"
        elif hasattr(UserClass, 'nom_complet'): admin.nom_complet = "Kader Hamidou Zakou"
            
        # Test dynamique du rôle
        if hasattr(UserClass, 'role'): admin.role = "ADMIN"

        session.add(admin)
        await session.commit()
        print("🚀 SUCCÈS : Compte ADMINISTRATEUR inséré directement dans la base de données Railway !")

if __name__ == "__main__":
    asyncio.run(main())
