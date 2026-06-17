import asyncio
import os
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

# Récupération de l'URL publique
DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL")

if not DATABASE_URL:
    raise ValueError("❌ Erreur : La variable DATABASE_PUBLIC_URL n'est pas définie localement.")

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

print(f"🔗 Connexion au serveur PostgreSQL Railway...")
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def seed_data():
    async with AsyncSessionLocal() as session:
        print("🚀 Début du seeding de la base de données FONCIER+...")
        
        try:
            # ─── 1. CRÉATION SÉCURISÉE DES TABLES EN RAW SQL ─────────────────
            print("🏗️ Création des tables 'users' et 'workflow_step_def' si manquantes...")
            
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS users (
                    id VARCHAR(36) PRIMARY KEY,
                    name VARCHAR(255),
                    email VARCHAR(255) UNIQUE,
                    password_hash VARCHAR(255),
                    role VARCHAR(50),
                    actif BOOLEAN DEFAULT TRUE,
                    region VARCHAR(10)
                );
            """))

            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS workflow_step_def (
                    id VARCHAR(36) PRIMARY KEY,
                    definition_id VARCHAR(50),
                    code_etape VARCHAR(50),
                    ordre INTEGER,
                    role_requis VARCHAR(50),
                    role_backup VARCHAR(50),
                    description TEXT
                );
            """))
            await session.commit()
            print("✅ Tables prêtes ou déjà existantes.")

            # ─── 2. INJECTION DE L'ADMINISTRAREUR ───────────────────────────
            res_user = await session.execute(
                text("SELECT id FROM users WHERE email = :email"),
                {"email": "admin@test.foncier.ne"}
            )
            user = res_user.fetchone()
            
            if not user:
                print("👤 Création du compte administrateur initial...")
                await session.execute(
                    text("""
                        INSERT INTO users (id, name, email, password_hash, role, actif, region)
                        VALUES (:id, :name, :email, :password, :role, :actif, :region)
                    """),
                    {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "name": "Kader Hamidou Zakou",
                        "email": "admin@test.foncier.ne",
                        "password": "supersecretpasswordhash",
                        "role": "ADMIN",
                        "actif": True,
                        "region": "NIA"
                    }
                )
                print("✅ Utilisateur ADMIN inséré.")
            else:
                print(f"ℹ️ Utilisateur ADMIN déjà présent (ID: {user[0]}).")

            # ─── 3. INJECTION DES ÉTAPES DU WORKFLOW RNAF ───────────────────
            
            # ─── 2.5 INJECTION DE LA DÉFINITION DU WORKFLOW (PARENT) ───────
            print("📑 Création du workflow parent RNAF...")
            rnaf_uuid = "de305d54-75b4-431b-adb2-eb6b9e546013"
            res_wf = await session.execute(
                text("SELECT id FROM workflow_definition WHERE type_workflow = :type"),
                {"type": "RNAF"}
            )
            if not res_wf.fetchone():
                await session.execute(
                    text("INSERT INTO workflow_definition (id, type_workflow, nom, description, is_active, version) VALUES (:id, 'RNAF', 'Régularisation Nationale des Actes Fonciers', 'Moteur de règles RNAF', true, 1)"),
                    {"id": rnaf_uuid}
                )
                await session.commit()
                print("✅ Workflow parent RNAF créé !")
            else:
                print("✅ Workflow parent RNAF existe déjà.")

            print("📑 Injection des étapes du moteur de workflow (RNAF)...")
            
            steps = [
                {
                    "id": str(uuid.uuid4()),
                    "definition_id": "de305d54-75b4-431b-adb2-eb6b9e546013",
                    "code_etape": "EN_INSTRUCTION",
                    "ordre": 1,
                    "role_requis": "AGENT_CCFM",
                    "role_backup": "CHEF_CCFM",
                    "description": "Vérification des pièces de l'arrêté foncier transmis."
                },
                {
                    "id": str(uuid.uuid4()),
                    "definition_id": "de305d54-75b4-431b-adb2-eb6b9e546013",
                    "code_etape": "PUBLIE",
                    "ordre": 2,
                    "role_requis": "EDITEUR_JO",
                    "role_backup": "ADMIN",
                    "description": "Insertion et publication officielle au Journal Officiel."
                }
            ]
            
            for step in steps:
                step['nom_etape'] = step.get('nom_etape', step['code_etape'].replace('_', ' ').title())
                step['type_validation'] = step.get('type_validation', 'APPROBATION')
                step['est_obligatoire'] = step.get('est_obligatoire', True)
                res_step = await session.execute(
                    text("SELECT id FROM workflow_step_def WHERE definition_id = :w_code AND code_etape = :c_etape"),
                    {"w_code": step["definition_id"], "c_etape": step["code_etape"]}
                )
                if not res_step.fetchone():
                    await session.execute(
                        text("""
                            INSERT INTO workflow_step_def (id, definition_id, code_etape, nom_etape, ordre, role_requis, role_backup, description, type_validation, est_obligatoire)
                            VALUES (:id, :definition_id, :code_etape, :nom_etape, :ordre, :role_requis, :role_backup, :description, :type_validation, :est_obligatoire)
                        """),
                        step
                    )
            
            await session.commit()
            print("🎉 Seeding terminé avec succès ! Prêt pour la simulation.")
            
        except Exception as e:
            print(f"❌ Erreur lors de l'exécution SQL : {e}")
            await session.rollback()

if __name__ == "__main__":
    asyncio.run(seed_data())