import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

RAW_URL = "postgres://postgres:eD4D1aFEE566e6E263aaCFGCc2a4Aa25@thomas.proxy.rlwy.net:19015/railway"
DATABASE_URL = RAW_URL.replace("postgres://", "postgresql+asyncpg://")

async def create_perfect_table():
    print("🚀 Connexion directe à la base PostGIS 17...")
    engine = create_async_engine(DATABASE_URL)
    
    async with engine.begin() as conn:
        print("🧼 Nettoyage des anciennes définitions...")
        await conn.execute(text("DROP TABLE IF EXISTS parcelles CASCADE;"))
        
        print("📐 Création de la table parcelles avec l'intégralité des colonnes exigées par l'API...")
        await conn.execute(text("""
            CREATE TABLE parcelles (
                id VARCHAR(255) PRIMARY KEY,
                ilot_id VARCHAR(255),
                nicad VARCHAR(255),
                geojson_id VARCHAR(255),
                statut VARCHAR(50) DEFAULT 'VALIDE',
                surface_m2 DOUBLE PRECISION DEFAULT 0.0,
                version_active_id VARCHAR(255),
                is_gele BOOLEAN DEFAULT FALSE,
                gele_par_litige_id VARCHAR(255),
                cree_par VARCHAR(255),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                geom geometry(Geometry, 4326)
            );
        """))
        print("✅ Réussite : La table parcelles est désormais structurellement parfaite !")
        
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(create_perfect_table())
