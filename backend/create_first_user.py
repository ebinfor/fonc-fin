import sqlalchemy
import uuid
from app.core.security import hash_password

print("🔐 Génération de l'UUID et du hash sécurisé...")
mot_de_passe = "MonMotDePasseSecurise123"
hash_calcule = hash_password(mot_de_passe)
unique_id = str(uuid.uuid4())

print("🔌 Connexion à la base de données publique...")
engine = sqlalchemy.create_engine('postgresql://postgres:eD4D1aFEE566e6E263aaCFGCc2a4Aa25@thomas.proxy.rlwy.net:19015/railway')

delete_query = sqlalchemy.text("DELETE FROM users WHERE email = :email;")

insert_query = sqlalchemy.text("""
    INSERT INTO users (id, name, email, hashed_password, password_hash, role, is_active, actif, region) 
    VALUES (:id, :name, :email, :hashed_password, :password_hash, :role, :is_active, :actif, :region);
""")

try:
    with engine.begin() as conn:
        # 1. On nettoie l'ancien enregistrement raté
        conn.execute(delete_query, {'email': 'kader@exemple.com'})
        
        # 2. On insère le profil tout neuf
        conn.execute(insert_query, {
            'id': unique_id,
            'name': 'Kader',
            'email': 'kader@exemple.com',
            'hashed_password': hash_calcule,
            'password_hash': hash_calcule,
            'role': 'ADMIN',
            'is_active': True,
            'actif': True,
            'region': 'NATIONAL'
        })
    print("✅ Réussite : L'utilisateur 'kader@exemple.com' a été ré-injecté proprement !")
except Exception as e:
    print(f"❌ Erreur lors de l'insertion : {e}")
