import asyncio
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

# Importer la configuration et les modèles
from app.core.config import get_settings
from app.core.database import Base
import app.models  # Forcer le chargement des modèles

settings = get_settings()
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
target_metadata = Base.metadata

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection):
    # Filtrer les tables géographiques PostGIS inutiles sur SQLite
    def include_object(object, name, type_, reflected, compare_to):
        if type_ == "table" and ("postgis" in name or "spatial_ref_sys" in name):
            return False
        return True

    context.configure(
        connection=connection, 
        target_metadata=target_metadata,
        include_object=include_object
    )

    # Intercepter et neutraliser le SQL brut incompatible avec SQLite
    orig_execute = connection.execute
    def sqlite_safe_execute(statement, *args, **kwargs):
        # 🔥 PROTECTION : On n'applique le filtre de chaîne que sur un dialecte SQLite
        if connection.dialect.name == "sqlite":
            stmt_str = str(statement).lower()
            if any(x in stmt_str for x in ["extension", "postgis", "do $$", "create type"]):
                from sqlalchemy import text
                return orig_execute(text("SELECT 1;"), *args, **kwargs)
        
        # Exécution directe et native pour PostgreSQL (asyncpg)
        return orig_execute(statement, *args, **kwargs)
    
    connection.execute = sqlite_safe_execute

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Mode Online standard (utilisé hors des tests : Dev / Production)."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


# ─── Point d'entrée de dispatch d'Alembic ────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    # Si conftest injecte une connexion de test, on évite asyncio.run()
    test_connection = config.attributes.get("connection", None)
    if test_connection is not None:
        do_run_migrations(test_connection)
    else:
        asyncio.run(run_migrations_online())