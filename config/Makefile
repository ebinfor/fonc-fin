# FONCIER+ v3.5.2 — Makefile
.PHONY: deploy rollback logs status backup migrate

COMPOSE = docker compose -f docker-compose.prod.yml --env-file .env.production

deploy:
	./scripts/deploy.sh

rollback:
	./scripts/rollback.sh

logs:
	$(COMPOSE) logs -f --tail=100

status:
	$(COMPOSE) ps

backup:
	./scripts/backup.sh

migrate:
	$(COMPOSE) run --rm backend alembic upgrade head

shell-db:
	$(COMPOSE) exec postgres psql -U foncier -d foncier_prod

stop:
	$(COMPOSE) stop

restart:
	$(COMPOSE) restart

clean:
	$(COMPOSE) down
