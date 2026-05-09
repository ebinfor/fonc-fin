#!/usr/bin/env bash
# FONCIER+ v3.5.2 — deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f .env.production ]] || { echo "✗ .env.production manquant"; exit 1; }
[[ -f ssl/foncier.gov.ne.crt ]] || { echo "✗ Certificat SSL manquant"; exit 1; }

echo "▶ Pré-vérifications..."
docker compose -f docker-compose.prod.yml --env-file .env.production config > /dev/null

echo "▶ Backup base avant déploiement..."
./scripts/backup.sh

echo "▶ Build des images..."
docker compose -f docker-compose.prod.yml --env-file .env.production build

echo "▶ Migrations Alembic..."
docker compose -f docker-compose.prod.yml --env-file .env.production run --rm backend \
    alembic upgrade 021_v35_fondation_administrative

echo "▶ Démarrage des services..."
docker compose -f docker-compose.prod.yml --env-file .env.production up -d

echo "▶ Attente healthchecks..."
sleep 10
for i in {1..30}; do
    if curl -fsS https://foncier.gov.ne/v1/health > /dev/null 2>&1; then
        echo "✓ Backend UP"
        break
    fi
    [[ $i -eq 30 ]] && { echo "✗ Backend KO"; exit 1; }
    sleep 2
done

echo "✓ Déploiement v3.5.2 terminé"
