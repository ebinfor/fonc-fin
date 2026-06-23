#!/bin/bash
# =========================================================================
# SCRIPT DE CONFIGURATION ET DÉMARRAGE AUTOMATIQUE - FONCIER+ v1.0.9
# Conçu pour Git Bash / MINGW64 sous Windows
# Tout-en-un : installe les dépendances, applique les correctifs et gère le port
# =========================================================================

echo "====================================================================="
echo "🟢 Début de la configuration de l'environnement..."
echo "====================================================================="

# 1. Détection de l'interpréteur Python valide
if command -v py &>/dev/null; then
    PYTHON_CMD="py"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
elif command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
else
    echo "====================================================================="
    echo "❌ ERREUR : Python est introuvable sur votre système Windows."
    echo "====================================================================="
    exit 1
fi

echo "🟢 Interpréteur Python détecté !"

# 2. Création de l'arborescence si manquante
mkdir -p app

# 3. Création de l'environnement virtuel 'venv' s'il n'existe pas
if [ ! -d "../venv" ] && [ ! -d "venv" ]; then
    echo "📦 Création de l'environnement virtuel 'venv'..."
    $PYTHON_CMD -m venv ../venv
    echo "🟢 Environnement virtuel créé."
fi

# 4. Activation de l'environnement virtuel
if [ -d "../venv" ]; then
    source ../venv/Scripts/activate
elif [ -d "venv" ]; then
    source venv/Scripts/activate
fi

# 5. Mise à jour de pip et installation des dépendances (y compris les paquets SIG et Redis)
echo "⚙️ Installation et mise à jour des dépendances..."
python -m pip install --upgrade pip
pip install fastapi uvicorn sqlalchemy reportlab qrcode pillow pydantic-settings asyncpg psycopg2-binary email-validator geoalchemy2 redis

# 6. Correctif automatique pour Pydantic v2 (BaseSettings)
echo "🔧 Application du correctif de compatibilité Pydantic..."
cat << 'EOF' > patch_pydantic.py
import pathlib
for f in pathlib.Path('.').rglob('config.py'):
    if f.is_file():
        try:
            content = f.read_text(encoding='utf-8')
            if 'from pydantic import BaseSettings' in content:
                f.write_text(content.replace('from pydantic import BaseSettings', 'from pydantic_settings import BaseSettings'), encoding='utf-8')
        except Exception:
            pass
EOF
python patch_pydantic.py
rm -f patch_pydantic.py

# Configuration dynamique du port (utilise $PORT si défini par le système, sinon 8000 par défaut)
PORT_TO_USE=${PORT:-8000}

# 7. Lancement de l'API FastAPI
echo "====================================================================="
echo "🚀 Lancement de l'API FastAPI sur http://127.0.0.1:$PORT_TO_USE"
echo "====================================================================="

python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT_TO_USE --reload