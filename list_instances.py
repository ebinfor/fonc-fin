import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def get_latest(table_name, engine):
    async with engine.connect() as conn:
        try:
            # On tente de récupérer les 5 derniers IDs créés
            res = await conn.execute(text(f"SELECT id FROM {table_name} LIMIT 5;"))
            rows = res.fetchall()
            print(f"\n🔹 Table [{table_name}] :")
            if not rows:
                print("  (Table vide)")
            for r in rows:
                print(f"  - {r[0]}")
        except Exception as e:
            print(f"❌ Impossible de lire {table_name} : {e}")

async def main():
    engine = create_async_engine('postgresql+asyncpg://postgres:eD4D1aFEE566e6E263aaCFGCc2a4Aa25@thomas.proxy.rlwy.net:19015/railway')
    print("\n=== RECHERCHE DES ENREGISTREMENTS RECENTOS ===")
    for table in ['workflow_instance', 'workflow_instances']:
        await get_latest(table, engine)
    print("==============================================\n")

asyncio.run(main())
