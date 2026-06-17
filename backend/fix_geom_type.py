import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

RAW_URL = "postgres://postgres:eD4D1aFEE566e6E263aaCFGCc2a4Aa25@thomas.proxy.rlwy.net:19015/railway"
DATABASE_URL = RAW_URL.replace("postgres://", "postgresql+asyncpg://")

async def clean_slate():
    print("🧼 Connexion à Railway pour détruire l'ancienne table incomplète...")
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        # Le CASCADE supprime proprement la table et ses anciennes liaisons bancales
        await conn.execute(text("DROP TABLE IF EXISTS parcelles CASCADE;"))
        print("✅ Nettoyage réussi ! La table parcelles a été balayée.")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(clean_slate())