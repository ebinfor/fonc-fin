from sqlalchemy import create_engine, text
import os

# Remplacez par votre URL Railway complète (format: postgresql://user:pass@host:port/dbname)
DATABASE_URL = "postgresql://postgres:eD4D1aFEE566e6E263aaCFGCc2a4Aa25@thomas.proxy.rlwy.net:19015/railway"

def test_connection():
    print(f"Tentative de connexion à la base de données...")
    try:
        engine = create_engine(DATABASE_URL, connect_args={"connect_timeout": 10})
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version();"))
            print("✅ SUCCÈS : Connexion établie !")
            print(f"📌 Version : {result.scalar()}")
    except Exception as e:
        print(f"❌ ÉCHEC : {e}")

if __name__ == "__main__":
    test_connection()