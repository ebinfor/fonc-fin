import re
from pathlib import Path

file_path = Path("backend/app/api/v1/endpoints/workflows.py")
code = file_path.read_text(encoding="utf-8")

# On prépare le bloc de code qui va forcer la création des tables dès l'appel
auto_create_code = """
    # Assurer la création des tables à la volée pour l'environnement SQLite en mémoire des tests
    try:
        from app.models.workflows import Base as WFBase
        import asyncio
        # On extrait le moteur synchrone sous-jacent pour exécuter la création des tables si nécessaire
        engine = db.bind
        if "sqlite" in str(engine.url):
            from sqlalchemy import create_engine
            # Recréation rapide des tables manquantes sur la connexion active
            sync_engine = create_engine("sqlite://", creator=lambda: db.sync_session.bind.engine.pool.connect())
    except Exception:
        pass
"""

# Approche encore plus simple et robuste pour Pytest : intercepter l'erreur no such table directement dans le service ou conftest.
# Modifions plutôt le point d'entrée du test dans conftest.py pour exécuter un create_all global.
