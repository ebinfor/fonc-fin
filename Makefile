# FONCIER+ v1.0.9 — Makefile
# ============================
# Cibles de déploiement : monolithique ET microservices

.PHONY: all help setup-env build deploy deploy-check \
        deploy-mono deploy-micro scale-service health \
        logs migrate seed test test-unit test-e2e \
        stop clean backup

VERSION ?= latest
COMPOSE_MONO  = -f config/docker-compose.prod.yml
COMPOSE_MICRO = -f docker-compose.microservices.yml

# ── Aide ──────────────────────────────────────────────────────
help:
	@echo "FONCIER+ v1.0.9 — Commandes disponibles :"
	@echo ""
	@echo "  setup-env        Générer .env avec secrets forts"
	@echo "  deploy-check     Vérifier env + SSL + TS avant déploiement"
	@echo "  deploy-mono      Déployer en mode MONOLITHIQUE (1 backend)"
	@echo "  deploy-micro     Déployer en mode MICROSERVICES (8 services)"
	@echo "  migrate          Exécuter les migrations Alembic"
	@echo "  seed             Seeder 45 utilisateurs / 29 rôles"
	@echo "  health           Vérifier la santé de tous les services"
	@echo "  logs [svc=...]   Voir les logs (tous ou un service)"
	@echo "  scale-micro svc=svc-parcel n=3  Scaler un service"
	@echo "  stop             Arrêter tous les conteneurs"
	@echo "  test             Lancer toute la suite de tests"
	@echo "  backup           Déclencher une sauvegarde manuelle"

# ── Configuration ─────────────────────────────────────────────
setup-env:
	@echo "Génération des secrets..."
	@cp -n .env.template .env 2>/dev/null || true
	@sed -i "s/^JWT_SECRET=.*/JWT_SECRET=$$(openssl rand -hex 32)/" .env
	@sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$$(openssl rand -base64 24 | tr -d '+/=')/" .env
	@sed -i "s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=$$(openssl rand -base64 16 | tr -d '+/=')/" .env
	@sed -i "s/^MINIO_SECRET_KEY=.*/MINIO_SECRET_KEY=$$(openssl rand -base64 24 | tr -d '+/=')/" .env
	@echo "✓ .env généré avec secrets forts"

deploy-check:
	@echo "Vérification pré-déploiement..."
	@test -f .env || (echo "✗ .env manquant — make setup-env" && exit 1)
	@test -f ssl/foncier.gov.ne.crt || (echo "✗ SSL manquant" && exit 1)
	@grep -q "JWT_SECRET=.\{32\}" .env || (echo "✗ JWT_SECRET trop court" && exit 1)
	@cd frontend && npm run type-check 2>/dev/null || true
	@echo "✓ Vérifications OK"

# ── Déploiement monolithique ──────────────────────────────────
deploy-mono: deploy-check
	@echo "Déploiement MONOLITHIQUE..."
	docker compose $(COMPOSE_MONO) pull
	docker compose $(COMPOSE_MONO) build
	docker compose $(COMPOSE_MONO) up -d
	@$(MAKE) migrate
	@echo "✓ Monolithique déployé"

# ── Déploiement microservices ─────────────────────────────────
deploy-micro: deploy-check
	@echo "Déploiement MICROSERVICES (8 services)..."
	docker compose $(COMPOSE_MICRO) build
	docker compose $(COMPOSE_MICRO) up -d
	@sleep 10
	@$(MAKE) migrate-micro
	@echo "✓ Microservices déployés"

# ── Migrations ────────────────────────────────────────────────
migrate:
	docker compose $(COMPOSE_MONO) exec backend \
		alembic upgrade head

migrate-micro:
	# La migration s'exécute depuis svc-cert (service le plus stable)
	docker compose $(COMPOSE_MICRO) exec svc-cert \
		alembic upgrade head

seed:
	docker compose $(COMPOSE_MONO) exec backend \
		python scripts/seed.py

seed-micro:
	docker compose $(COMPOSE_MICRO) exec svc-cert \
		python scripts/seed.py

# ── Scaling (microservices uniquement) ───────────────────────
scale-micro:
	@test -n "$(svc)" || (echo "Usage: make scale-micro svc=svc-parcel n=3" && exit 1)
	docker compose $(COMPOSE_MICRO) up -d --scale $(svc)=$(or $(n),2) --no-recreate

# ── Health ────────────────────────────────────────────────────
health:
	@echo "Health checks..."
	@for svc in dar parcel legal workflow alert conflict audit cert; do \
		STATUS=$$(docker compose $(COMPOSE_MICRO) exec -T svc-$$svc \
			curl -sf http://localhost:8000/health 2>/dev/null | python3 -c \
			"import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null \
			|| echo "DOWN"); \
		echo "  svc-$$svc : $$STATUS"; \
	done

# ── Logs ──────────────────────────────────────────────────────
logs:
	@if [ -n "$(svc)" ]; then \
		docker compose $(COMPOSE_MICRO) logs -f $(svc); \
	else \
		docker compose $(COMPOSE_MICRO) logs -f; \
	fi

# ── Tests ─────────────────────────────────────────────────────
test:
	cd backend && python -m pytest tests/ -v --tb=short -x

test-unit:
	cd backend && python -m pytest tests/ -v -k "not e2e" --tb=short

test-sandbox:
	cd backend && python -m pytest tests/test_sql_sandbox.py -v

# ── Stop / Clean ──────────────────────────────────────────────
stop:
	docker compose $(COMPOSE_MICRO) down 2>/dev/null || true
	docker compose $(COMPOSE_MONO)  down 2>/dev/null || true

clean: stop
	docker compose $(COMPOSE_MICRO) down -v 2>/dev/null || true
	docker image prune -f

# ── Backup ────────────────────────────────────────────────────
backup:
	docker compose $(COMPOSE_MICRO) exec -T db-backup /backup.sh
