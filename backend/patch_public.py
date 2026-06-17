import os
from sqlalchemy import create_engine, text

# 1. Récupération des identifiants et de l'hôte PUBLIC de Railway
user = os.getenv("PGUSER")
password = os.getenv("PGPASSWORD")
database = os.getenv("PGDATABASE")
host_public = os.getenv("PGHOST_PUBLIC")
port_public = os.getenv("PGPORT_PUBLIC")

if not host_public:
    print("❌ Impossible de trouver les variables de connexion publique de Railway.")
    exit(1)

# 2. Construction de l'URL de connexion classique (synchrone)
public_url = f"postgresql://{user}:{password}@{host_public}:{port_public}/{database}"

print("🚀 Connexion à la passerelle publique PostgreSQL de Railway...")
try:
    engine = create_engine(public_url)
    with engine.begin() as conn:
        print("🔧 Injection de la colonne 'password_hash'...")
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);"))
    print("✅ Opération réussie ! La colonne 'password_hash' est désormais en place.")
except Exception as e:
    print(f"❌ Erreur lors de l'exécution du patch : {e}")
