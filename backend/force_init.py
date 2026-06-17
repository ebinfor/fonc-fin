import asyncio
import sys
sys.path.append(".")
from app.core.database import engine, Base

# Import de sécurité pour s'assurer que TOUS les modèles sont enregistrés dans Base.metadata
import app.models

async def main():
    print("🔄 Connexion globale à Railway...")
    try:
        async with engine.begin() as conn:
            print("🚀 Création de toutes les tables de l'écosystème...")
            await conn.run_sync(Base.metadata.create_all)
        print("✅ Tables créées avec succès sur Railway !")
    except Exception as e:
        print(f"❌ Erreur : {e}")
if __name__ == "__main__":
    asyncio.run(main())
