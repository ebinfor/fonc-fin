import asyncio
import sys
from app.core.database import engine, Base

# 🚀 Force le chargement des modèles pour que SQLAlchemy les connaisse
import app.models.workflow_engine

async def init_db():
    async with engine.begin() as conn:
        print("🚀 Connexion à Railway établie. Création des tables de workflow...")
        await conn.run_sync(Base.metadata.create_all)
        print("🎉 Tables créées avec succès sur la base distante !")

if __name__ == "__main__":
    asyncio.run(init_db())