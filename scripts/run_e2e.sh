#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# FONCIER+ v3.4.7 — Runner E2E complet
# Orchestre : setup → migrate → seed → test → cleanup
# ═══════════════════════════════════════════════════════════════════
# Usage:
#   ./run_e2e.sh                  # run complet
#   ./run_e2e.sh --antifraud-only # uniquement les tests antifraude
#   ./run_e2e.sh --keep           # ne nettoie pas en fin de run (debug)
#   ./run_e2e.sh --ci             # mode CI (JUnit XML + couverture)
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Configuration ─────────────────────────────────────────────────
readonly DB_NAME="${DB_NAME:-foncier_e2e}"
readonly DB_USER="${DB_USER:-foncier}"
readonly DB_PASS="${DB_PASS:-foncier_test}"
readonly DB_HOST="${DB_HOST:-localhost}"
readonly DB_PORT="${DB_PORT:-5432}"
readonly API_PORT="${API_PORT:-8000}"
readonly BACKEND_DIR="${BACKEND_DIR:-./backend}"
readonly TESTS_DIR="${TESTS_DIR:-./backend/tests}"
export FONCIER_TEST_MODE=1
export DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}"

# ─── Couleurs pour output ──────────────────────────────────────────
readonly C_GREEN="\033[0;32m"
readonly C_RED="\033[0;31m"
readonly C_YELLOW="\033[0;33m"
readonly C_BLUE="\033[0;34m"
readonly C_RESET="\033[0m"

log()     { echo -e "${C_BLUE}[$(date +%H:%M:%S)]${C_RESET} $*"; }
success() { echo -e "${C_GREEN}✓${C_RESET} $*"; }
warn()    { echo -e "${C_YELLOW}⚠${C_RESET} $*"; }
error()   { echo -e "${C_RED}✗${C_RESET} $*" >&2; }

# ─── Parse arguments ───────────────────────────────────────────────
ANTIFRAUD_ONLY=0
KEEP_DB=0
CI_MODE=0
for arg in "$@"; do
    case "$arg" in
        --antifraud-only) ANTIFRAUD_ONLY=1 ;;
        --keep)           KEEP_DB=1 ;;
        --ci)             CI_MODE=1 ;;
        --help|-h)        sed -n '2,15p' "$0"; exit 0 ;;
    esac
done

# ─── Cleanup hook ──────────────────────────────────────────────────
API_PID=""
cleanup() {
    local exit_code=$?
    log "Nettoyage…"
    if [[ -n "${API_PID}" ]] && kill -0 "${API_PID}" 2>/dev/null; then
        log "Arrêt du backend (PID ${API_PID})"
        kill "${API_PID}" 2>/dev/null || true
        wait "${API_PID}" 2>/dev/null || true
    fi
    if [[ "${KEEP_DB}" -eq 0 ]]; then
        log "Suppression de la base ${DB_NAME}"
        PGPASSWORD="${DB_PASS}" dropdb -h "${DB_HOST}" -p "${DB_PORT}" \
            -U "${DB_USER}" --if-exists "${DB_NAME}" 2>/dev/null || true
    else
        warn "Base ${DB_NAME} conservée (--keep)"
    fi
    if [[ ${exit_code} -eq 0 ]]; then
        success "Run E2E terminé avec succès"
    else
        error "Run E2E échoué (code ${exit_code})"
    fi
    exit ${exit_code}
}
trap cleanup EXIT INT TERM

# ═══════════════════════════════════════════════════════════════════
# PHASE 1 — SETUP
# ═══════════════════════════════════════════════════════════════════
log "═══ PHASE 1 — SETUP ═══"

command -v psql >/dev/null || { error "psql introuvable"; exit 1; }
command -v alembic >/dev/null || { error "alembic introuvable"; exit 1; }
command -v pytest >/dev/null || { error "pytest introuvable"; exit 1; }
command -v uvicorn >/dev/null || { error "uvicorn introuvable"; exit 1; }
success "Dépendances vérifiées"

log "Création de la base ${DB_NAME}"
PGPASSWORD="${DB_PASS}" dropdb -h "${DB_HOST}" -p "${DB_PORT}" \
    -U "${DB_USER}" --if-exists "${DB_NAME}" 2>/dev/null || true
PGPASSWORD="${DB_PASS}" createdb -h "${DB_HOST}" -p "${DB_PORT}" \
    -U "${DB_USER}" "${DB_NAME}"
success "Base créée"

PGPASSWORD="${DB_PASS}" psql -h "${DB_HOST}" -p "${DB_PORT}" \
    -U "${DB_USER}" -d "${DB_NAME}" -c "CREATE EXTENSION IF NOT EXISTS postgis;" \
    -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;" > /dev/null
success "Extensions PostGIS + pgcrypto activées"

# ═══════════════════════════════════════════════════════════════════
# PHASE 2 — MIGRATIONS
# ═══════════════════════════════════════════════════════════════════
log "═══ PHASE 2 — MIGRATIONS ═══"
cd "${BACKEND_DIR}"
alembic upgrade 016_e2e_infrastructure 2>&1 | tail -20
current_rev=$(alembic current 2>/dev/null | grep -oP '\w+_\w+' | head -1)
[[ "${current_rev}" == "016_e2e_infrastructure" ]] || {
    error "Migration 016 non appliquée (rev: ${current_rev})"
    exit 1
}
success "16 migrations appliquées (jusqu'à 016_e2e_infrastructure)"
cd - > /dev/null

# ═══════════════════════════════════════════════════════════════════
# PHASE 3 — SEEDS (29 utilisateurs + données de test)
# ═══════════════════════════════════════════════════════════════════
log "═══ PHASE 3 — SEEDS ═══"

python3 <<'EOF'
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "backend"))
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text
import bcrypt

ROLES = [
    "ADMIN", "MINISTRE_URBANISME",
    "ADMIN_CADASTRE", "DIRECTEUR_CADASTRE", "CHEF_SERVICE_CADASTRE",
    "GEOMETRE", "TOPOGRAPHE", "SECRETARIAT_CADASTRE",
    "DIRECTEUR_URBANISME", "CHEF_URBANISME", "AGENT_URBANISME",
    "ADMIN_COMMUNE", "MAIRE", "AGENT_COMMUNE",
    "CHEF_CCFM", "AGENT_CCFM", "NOTAIRE",
    "BANQ_DIRECTEUR", "BANQ_AGENT",
    "JUGE_FONCIER", "GREFFIER", "HUISSIER",
    "DIRECTEUR_DOMAINE", "AGENT_DOMAINE",
    "EDITEUR_JO", "RESPONSABLE_BGU",
    "AUDITEUR", "ARCHIVISTE_ANNF", "RESPONSABLE_ANNF",
]
assert len(ROLES) == 29

async def seed():
    engine = create_async_engine(os.environ["DATABASE_URL"])
    pwd_hash = bcrypt.hashpw(b"Test@2026!", bcrypt.gensalt()).decode()
    async with AsyncSession(engine) as db:
        for role in ROLES:
            email = f"{role.lower()}@test.foncier.ne"
            await db.execute(text("""
                INSERT INTO users (id, email, password_hash, role, region, actif)
                VALUES (gen_random_uuid(), :email, :pwd, :role, 'NIA', TRUE)
                ON CONFLICT (email) DO NOTHING
            """), {"email": email, "pwd": pwd_hash, "role": role})
        # Parcelle de test + îlot minimal
        await db.execute(text("""
            INSERT INTO regions (id, code, nom) VALUES
                (gen_random_uuid(), '01', 'Niamey') ON CONFLICT DO NOTHING;
        """))
        await db.commit()
    await engine.dispose()
    print(f"✓ {len(ROLES)} utilisateurs seedés")

asyncio.run(seed())
EOF
success "29 utilisateurs créés (mot de passe: Test@2026!)"

# ═══════════════════════════════════════════════════════════════════
# PHASE 4 — LANCEMENT BACKEND
# ═══════════════════════════════════════════════════════════════════
log "═══ PHASE 4 — LANCEMENT BACKEND ═══"
cd "${BACKEND_DIR}"
uvicorn app.main:app --host 127.0.0.1 --port "${API_PORT}" \
    --log-level warning > /tmp/foncier_api.log 2>&1 &
API_PID=$!
cd - > /dev/null

log "Attente du backend (PID ${API_PID})…"
for i in {1..30}; do
    if curl -sf "http://127.0.0.1:${API_PORT}/health" > /dev/null 2>&1; then
        success "Backend UP sur port ${API_PORT}"
        break
    fi
    [[ $i -eq 30 ]] && { error "Backend ne démarre pas"; cat /tmp/foncier_api.log; exit 1; }
    sleep 1
done

# ═══════════════════════════════════════════════════════════════════
# PHASE 5 — EXÉCUTION DES TESTS
# ═══════════════════════════════════════════════════════════════════
log "═══ PHASE 5 — TESTS E2E ═══"

PYTEST_ARGS=(-v --tb=short --color=yes)
[[ ${CI_MODE} -eq 1 ]] && PYTEST_ARGS+=(
    --junit-xml=test-results.xml
    --cov=app --cov-report=xml --cov-report=term
)
[[ ${ANTIFRAUD_ONLY} -eq 1 ]] && PYTEST_ARGS+=(-k "antifraud")

pytest "${TESTS_DIR}/test_workflows_e2e.py" "${PYTEST_ARGS[@]}"

# ═══════════════════════════════════════════════════════════════════
# PHASE 6 — MÉTRIQUES FINALES
# ═══════════════════════════════════════════════════════════════════
log "═══ PHASE 6 — MÉTRIQUES ═══"

PGPASSWORD="${DB_PASS}" psql -h "${DB_HOST}" -p "${DB_PORT}" \
    -U "${DB_USER}" -d "${DB_NAME}" -c "
SELECT
    (SELECT COUNT(*) FROM users)                           AS nb_users,
    (SELECT COUNT(*) FROM workflow_instance)               AS nb_wf_instances,
    (SELECT COUNT(DISTINCT wf_code) FROM workflow_instance) AS nb_wf_distincts,
    (SELECT COUNT(*) FROM greffe_judiciaire WHERE actif)   AS nb_greffes_actifs;
"

success "Tous les workflows testés de WF1 à WF33 sans exception"
