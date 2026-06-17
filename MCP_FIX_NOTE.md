# MCP / Uvicorn Fix Note

## Résumé des corrections

- Correction du fichier de configuration Continue (`.continue/config.json`)
  - JSON validé.
  - `apiKey` remplacés par des valeurs vides pour éviter de stocker des secrets.
  - ajout de `cwd` et `enabled` dans la section `mcpServers`.

- Correction du démarrage du moteur de monitoring dans `backend/app/main.py`
  - remplacement de l'appel à `MonitoringEngine.instance().demarrer(get_db_session)` par `database_session_scope`.
  - le moteur attend une factory d'async context manager, pas un générateur FastAPI.

- Adaptation de `backend/app/services/monitoring_engine.py`
  - support des deux styles de factory :
    - async context managers (`async with factory() as db`)
    - async generators FastAPI (`await agen.__anext__()` + `await agen.aclose()`).

## Commande de lancement testée

```bash
PYTHONPATH=/workspaces/fonc-fin/backend /workspaces/fonc-fin/.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --log-level info
```

## Etat actuel

- Le serveur FastAPI démarre correctement sur `http://127.0.0.1:8000`.
- La boucle de monitoring ne génère plus l'erreur `async_generator object does not support the asynchronous context manager protocol`.

## Notes complémentaires

- Le paquet `mcp` a été installé dans le venv, mais il n'est pas encore utilisé pour lancer un agent MCP séparé.
- Pour un usage MCP propre, il est recommandé de créer un environnement Python isolé dédié à `mcp` afin d'éviter les conflits de dépendances avec FastAPI/Starlette.
