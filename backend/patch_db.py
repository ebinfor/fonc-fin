import asyncio
from sqlalchemy import text
from app.core.database import engine

async def main():
    print("🚀 Connexion sécurisée à la base PostgreSQL de FONCIER+...")
    async with engine.begin() as conn:
        print("🔧 Injection de la colonne 'password_hash'...")
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);"))
    print("✅ Base de données mise à jour avec succès ! La colonne est en place.")

if __name__ == "__main__":
    asyncio.run(main())
