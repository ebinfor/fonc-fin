import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def check_table(table_name, engine, target_id):
    # On ouvre une connexion isolée pour éviter de bloquer la transaction
    async with engine.connect() as conn:
        try:
            res = await conn.execute(
                text(f"SELECT COUNT(*) FROM {table_name} WHERE id = :id"), 
                {"id": target_id}
            )
            return f"{res.scalar()} ligne(s) trouvee(s)"
        except Exception as e:
            return f"Erreur de lecture (colonnes ou droits) : {e}"

async def main():
    engine = create_async_engine('postgresql+asyncpg://postgres:eD4D1aFEE566e6E263aaCFGCc2a4Aa25@thomas.proxy.rlwy.net:19015/railway')
    target_id = '33eb282d-ded5-4b1a-869c-468152f8357e'
    
    print("\n=== LOCALISATION DE L'INSTANCE ===")
    for table in ['workflow_instance', 'workflow_instances']:
        status = await check_table(table, engine, target_id)
        print(f"-> Table [{table}] : {status}")
    print("===================================\n")

asyncio.run(main())
