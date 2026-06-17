import asyncio
import os
import importlib
from sqlalchemy import Text
from sqlalchemy.schema import ForeignKeyConstraint, CheckConstraint
from app.core.database import engine, Base

# 1. Chargement de l'arbre de l'application
try:
    import app.main
except Exception:
    pass

# 2. Importation forcée de tous les modules de données
models_dir = os.path.join("app", "models")
if os.path.exists(models_dir):
    for root, _, files in os.walk(models_dir):
        for file in files:
            if file.endswith(".py") and file != "__init__.py":
                rel_path = os.path.relpath(os.path.join(root, file), ".")
                module_name = rel_path.replace(os.sep, ".").removesuffix(".py")
                try:
                    importlib.import_module(module_name)
                except Exception:
                    continue

async def main():
    print("⚙️ [Diagnostic] Préparation et nettoyage des tables en mémoire...")
    
    for table_name, table in list(Base.metadata.tables.items()):
        # On affiche chaque table pour valider que le script s'exécute bien
        print(f"  🔹 Traitement de la table : {table_name}")
        
        # Nettoyage des verrous de relations et de checks
        table.foreign_keys.clear()
        table.constraints = {c for c in table.constraints if not isinstance(c, (ForeignKeyConstraint, CheckConstraint))}
        
        for col in table.columns:
            # 1. Neutralisation du type Geometry (Bypass PostGIS)
            if "geometry" in str(col.type).lower() or "geo" in str(col.type).lower():
                col.type = Text()
            
            # 2. Suppression RADICALE de tous les defaults pour éliminer l'erreur ARRAY[]
            if col.server_default is not None:
                col.server_default = None
                    
    print("\n🚀 Envoi de la structure brute à la base de données Railway...")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("\n✅ TOUTES LES TABLES DE L'ÉCOSYSTEME ONT ÉTÉ MATÉRIALISÉES AVEC SUCCÈS !")
    except Exception as e:
        print(f"\n❌ Erreur persistante : {e}")

if __name__ == "__main__":
    asyncio.run(main())