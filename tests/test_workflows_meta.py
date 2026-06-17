from alembic import context
"""
FONCIER+ v3.4.8 — Tests méta sur la complétude des workflows
=============================================================

Ces tests auraient immédiatement détecté l'écart 8/33 avant la livraison.
À ajouter dans backend/tests/test_workflows_meta.py — scope session, ils
ne nécessitent pas de backend lancé, juste un accès DB en lecture.
"""
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Liste des 33 codes de workflow attendus (source de vérité = rbac_matrix.py)
WORKFLOWS_ATTENDUS = [f"WF{i}" for i in range(1, 34)]


@pytest_asyncio.fixture
async def db_session():
    from app.core.database import async_session_factory
    async with async_session_factory() as s:
        yield s


# ═══════════════════════════════════════════════════════════════════
# TEST 1 — Chaque workflow déclaré doit avoir une workflow_definition
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
@pytest.mark.parametrize("wf_code", WORKFLOWS_ATTENDUS)
async def test_workflow_definition_exists(db_session: AsyncSession, wf_code):
    """Chaque WF doit être présent dans workflow_definition.

    Avant migration 017 : 25/33 échouent.
    Après migration 017 : 33/33 passent.
    """
    r = await db_session.execute(
        text("SELECT COUNT(*) FROM workflow_definition WHERE code_wf = :c AND is_active"),
        {"c": wf_code},
    )
    count = r.scalar()
    assert count >= 1, (
        f"{wf_code} n'a AUCUNE définition active dans workflow_definition — "
        f"migration 017 requise"
    )


# ═══════════════════════════════════════════════════════════════════
# TEST 2 — Chaque workflow doit avoir au moins 2 étapes définies
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
@pytest.mark.parametrize("wf_code", WORKFLOWS_ATTENDUS)
async def test_workflow_has_steps(db_session: AsyncSession, wf_code):
    """Un workflow sans étapes n'est pas pilotable."""
    r = await db_session.execute(
        text("""
            SELECT COUNT(*) FROM workflow_step_def s
            JOIN workflow_definition d ON d.id = s.definition_id
            WHERE d.code_wf = :c AND d.is_active
        """),
        {"c": wf_code},
    )
    count = r.scalar()
    assert count >= 2, (
        f"{wf_code} n'a que {count} étape(s) définies — minimum 2 attendues"
    )


# ═══════════════════════════════════════════════════════════════════
# TEST 3 — Chaque workflow doit avoir une étape finale (is_final=TRUE)
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
@pytest.mark.parametrize("wf_code", WORKFLOWS_ATTENDUS)
async def test_workflow_has_final_step(db_session: AsyncSession, wf_code):
    r = await db_session.execute(
        text("""
            SELECT COUNT(*) FROM workflow_step_def s
            JOIN workflow_definition d ON d.id = s.definition_id
            WHERE d.code_wf = :c AND s.is_final = TRUE
        """),
        {"c": wf_code},
    )
    assert r.scalar() >= 1, (
        f"{wf_code} n'a aucune étape marquée is_final=TRUE — "
        f"le workflow ne pourra jamais se terminer"
    )


# ═══════════════════════════════════════════════════════════════════
# TEST 4 — Règles antifraude : les triggers bloquants sont présents
# ═══════════════════════════════════════════════════════════════════
TRIGGERS_CRITIQUES = [
    # (nom_trigger, table, motif)
    ("tg_block_update_proprietaire_id", "parcelles",
     "BUG-002 fix — bloque UPDATE direct"),
    ("tg_block_transfert_si_charges", "workflow_instance",
     "BUG-WF02 fix — gate CCFM sur TRANSFERT"),
    ("tg_wf30_check_rms", "migration_etape_log",
     "BUG-WF03 fix — RMS ≤ 0.5m bloquant"),
    ("tg_chainer_annf", "annf_right_archive",
     "BUG-WF07 fix — chaîne SHA-256 ANNF"),
    ("tg_conflit_degel_automatique", "litiges",
     "BUG-WF08 fix — dégel auto après résolution"),
    ("tg_conflit_gel_automatique", "litiges",
     "Gel automatique à l'ouverture d'un litige"),
    ("tg_antifraude_overlap", "parcelles",
     "Détection superposition géométrique"),
    ("tg_prevent_hard_delete", "parcelles",
     "Règle n°1 — pas de DELETE physique"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger_name,table_name,motif", TRIGGERS_CRITIQUES)
async def test_antifraud_trigger_installed(
    db_session: AsyncSession, trigger_name, table_name, motif
):
    """Vérifie que les 8 triggers antifraude critiques sont en place."""
    r = await db_session.execute(
        text("""
            SELECT COUNT(*) FROM information_schema.triggers
            WHERE trigger_name = :tg AND event_object_table = :tbl
        """),
        {"tg": trigger_name, "tbl": table_name},
    )
    assert r.scalar() >= 1, (
        f"Trigger {trigger_name} absent sur {table_name} — {motif}"
    )


# ═══════════════════════════════════════════════════════════════════
# TEST 5 — Fonctions PL/pgSQL attendues
# ═══════════════════════════════════════════════════════════════════
FONCTIONS_ATTENDUES = [
    "ccfm_f1", "ccfm_f2", "ccfm_f3", "ccfm_f4",
    "ccfm_f5", "ccfm_f6", "ccfm_f7",
    "ccfm_f8_orchestrer",              # BUG-WF01 fix
    "ccfm_signer_safe",                 # BUG-WF06 fix
    "appliquer_annulation_objet_safe",  # BUG-WF04 fix
    "verifier_authenticite_jugement",   # migration 016
    "verifier_somme_propriete",         # BUG-006 fix (E2E audit)
]


@pytest.mark.asyncio
@pytest.mark.parametrize("fn_name", FONCTIONS_ATTENDUES)
async def test_plpgsql_function_exists(db_session: AsyncSession, fn_name):
    r = await db_session.execute(
        text("""
            SELECT COUNT(*) FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proname = :fn
        """),
        {"fn": fn_name},
    )
    assert r.scalar() >= 1, f"Fonction PL/pgSQL {fn_name} absente"


# ═══════════════════════════════════════════════════════════════════
# TEST 6 — Synthèse globale de couverture
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_couverture_33_sur_33(db_session: AsyncSession):
    """Synthèse : les 33 workflows sont tous présents et pilotables.

    Ce test est le verrou final avant release. S'il passe, on peut
    signer la v3.4.8.
    """
    r = await db_session.execute(
        text("""
            SELECT d.code_wf,
                   COUNT(s.id) AS nb_etapes,
                   BOOL_OR(s.is_final) AS has_final
            FROM workflow_definition d
            LEFT JOIN workflow_step_def s ON s.definition_id = d.id
            WHERE d.is_active = TRUE
            GROUP BY d.code_wf
            ORDER BY d.code_wf
        """)
    )
    rows = r.fetchall()
    codes_presents = {row.code_wf for row in rows}
    codes_manquants = set(WORKFLOWS_ATTENDUS) - codes_presents

    assert not codes_manquants, (
        f"{len(codes_manquants)} workflows manquants : "
        f"{sorted(codes_manquants)}"
    )

    incomplets = [
        row.code_wf for row in rows
        if row.nb_etapes < 2 or not row.has_final
    ]
    assert not incomplets, (
        f"Workflows présents mais incomplets (< 2 étapes ou pas de finale) : "
        f"{incomplets}"
    )

    assert len(codes_presents) == 33, (
        f"Couverture : {len(codes_presents)}/33 workflows déclarés"
    )


# ═══════════════════════════════════════════════════════════════════
# TEST 7 — Vérification de la cohérence matrice RBAC ↔ workflow_step_def
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_coherence_matrice_rbac_vs_base(db_session: AsyncSession):
    """Pour chaque workflow, chaque rôle marqué L/V/S dans la matrice
    doit exister comme role_attendu dans au moins une étape."""
    from rbac_matrix import WORKFLOWS

    incoherences = []
    for wf_code, wf_def in WORKFLOWS.items():
        roles_matrice = {
            r for r, p in wf_def["permissions"].items()
            if p in ("L", "V", "S")
        }
        r = await db_session.execute(
            text("""
                SELECT DISTINCT s.role_attendu FROM workflow_step_def s
                JOIN workflow_definition d ON d.id = s.definition_id
                WHERE d.code_wf = :c
            """),
            {"c": wf_code},
        )
        roles_base = {row[0] for row in r.fetchall()}
        manquants = roles_matrice - roles_base - {"ADMIN"}  # ADMIN ignoré
        if manquants:
            incoherences.append(f"{wf_code}: rôles {manquants} en matrice mais absents en base")

    assert not incoherences, "\n".join(incoherences)
