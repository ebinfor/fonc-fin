"""
FONCIER+ — scripts/seed.py
Crée les 29 utilisateurs de test + données minimales pour le démarrage.
Usage : python scripts/seed.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

ROLES = [
    ("admin@foncier.gov.ne", "Admin@2026!", "ADMIN"),
    ("ministre@foncier.gov.ne", "Ministre@2026!", "MINISTRE_URBANISME"),
    ("admin.cadastre@foncier.gov.ne", "Cadastre@2026!", "ADMIN_CADASTRE"),
    ("dir.cadastre@foncier.gov.ne", "DirCad@2026!", "DIRECTEUR_CADASTRE"),
    ("chef.cadastre@foncier.gov.ne", "ChefCad@2026!", "CHEF_SERVICE_CADASTRE"),
    ("geometre@foncier.gov.ne", "Geo@2026!", "GEOMETRE"),
    ("topographe@foncier.gov.ne", "Topo@2026!", "TOPOGRAPHE"),
    ("secretariat@foncier.gov.ne", "Sec@2026!", "SECRETARIAT_CADASTRE"),
    ("dir.urb@foncier.gov.ne", "DirUrb@2026!", "DIRECTEUR_URBANISME"),
    ("chef.urb@foncier.gov.ne", "ChefUrb@2026!", "CHEF_URBANISME"),
    ("agent.urb@foncier.gov.ne", "AgtUrb@2026!", "AGENT_URBANISME"),
    ("admin.commune@foncier.gov.ne", "AComm@2026!", "ADMIN_COMMUNE"),
    ("maire@foncier.gov.ne", "Maire@2026!", "MAIRE"),
    ("agent.commune@foncier.gov.ne", "AgtComm@2026!", "AGENT_COMMUNE"),
    ("chef.ccfm@foncier.gov.ne", "CCFM@2026!", "CHEF_CCFM"),
    ("agent.ccfm@foncier.gov.ne", "AgtCCFM@2026!", "AGENT_CCFM"),
    ("notaire@foncier.gov.ne", "Notaire@2026!", "NOTAIRE"),
    ("dir.banque@foncier.gov.ne", "BanqDir@2026!", "BANQ_DIRECTEUR"),
    ("agent.banque@foncier.gov.ne", "BanqAgt@2026!", "BANQ_AGENT"),
    ("juge@justice.foncier.ne", "Juge@2026!", "JUGE_FONCIER"),
    ("greffier@justice.foncier.ne", "Greff@2026!", "GREFFIER"),
    ("huissier@justice.foncier.ne", "Huis@2026!", "HUISSIER"),
    ("dir.domaine@foncier.gov.ne", "Dom@2026!", "DIRECTEUR_DOMAINE"),
    ("agent.domaine@foncier.gov.ne", "AgtDom@2026!", "AGENT_DOMAINE"),
    ("editeur.jo@foncier.gov.ne", "JO@2026!", "EDITEUR_JO"),
    ("bgu@foncier.gov.ne", "BGU@2026!", "RESPONSABLE_BGU"),
    ("auditeur@foncier.gov.ne", "Audit@2026!", "AUDITEUR"),
    ("archiviste@foncier.gov.ne", "Archive@2026!", "ARCHIVISTE_ANNF"),
    ("resp.annf@foncier.gov.ne", "ANNF@2026!", "RESPONSABLE_ANNF"),
]


async def run():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from sqlalchemy import text
    import bcrypt

    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://foncier:foncier@localhost:5432/foncier_dev"
    )
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        created = 0
        for email, password, role in ROLES:
            pwd = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            r = await db.execute(text("""
                INSERT INTO users (id, email, password_hash, role, region, actif)
                VALUES (gen_random_uuid(), :email, :pwd, :role, 'NAT', TRUE)
                ON CONFLICT (email) DO NOTHING
            """), {"email": email, "pwd": pwd, "role": role})
            if r.rowcount > 0:
                created += 1
        await db.commit()
        print(f"✓ {created} utilisateurs créés ({len(ROLES)} total dans la liste)")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
