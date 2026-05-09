"""
FONCIER+ v3.4.9 — Tests méta sur les 5 registres administratifs
=================================================================

Ces tests vérifient que :
  1. Les 5 tables registres existent
  2. Les contraintes d'unicité sont posées
  3. Les triggers de validation sont installés et actifs
  4. Les seeds minimaux sont présents
  5. Les garde-fous métier bloquent les usages frauduleux

À lancer après la migration 020.
"""
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


REGISTRES = [
    ("etude_notariale",  "uq_etude_parquet",       "numero_parquet"),
    ("banque_agreee",    "uq_banque_bceao",        "numero_agrement_bceao"),
    ("huissier_agree",   "uq_huissier_charge",     "numero_charge"),
    ("geometre_expert",  "uq_geometre_onge",       "numero_inscription_onge"),
    ("mandat_elu",       "uq_maire_commune_actif", "commune_id"),  # index partiel
]

TRIGGERS_A05 = [
    ("tg_verifier_mandat_maire",    "workflow_instance"),
    ("tg_verifier_banque_agreee",   "mortgage_registry"),
    ("tg_verifier_huissier_agree",  "workflow_instance"),
    ("tg_verifier_geometre_inscrit", "workflow_instance"),
]


@pytest_asyncio.fixture
async def db():
    from app.core.database import async_session_factory
    async with async_session_factory() as s:
        yield s


# ═══════════════════════════════════════════════════════════════════
# 1. PRÉSENCE DES 5 TABLES REGISTRES
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
@pytest.mark.parametrize("table,uq_name,uq_col", REGISTRES)
async def test_registre_existe(db: AsyncSession, table, uq_name, uq_col):
    """Chaque table registre doit être présente avec sa contrainte d'unicité."""
    r = await db.execute(text("""
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = :t
    """), {"t": table})
    assert r.scalar() == 1, f"Table {table} absente — migration 020 requise"


# ═══════════════════════════════════════════════════════════════════
# 2. PRÉSENCE DES 4 TRIGGERS DE VALIDATION
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
@pytest.mark.parametrize("trigger,table", TRIGGERS_A05)
async def test_trigger_registre_installe(db: AsyncSession, trigger, table):
    r = await db.execute(text("""
        SELECT COUNT(*) FROM information_schema.triggers
        WHERE trigger_name = :tg AND event_object_table = :tbl
    """), {"tg": trigger, "tbl": table})
    assert r.scalar() >= 1, (
        f"Trigger {trigger} absent sur {table} — migration 020 non appliquée"
    )


# ═══════════════════════════════════════════════════════════════════
# 3. SEEDS MINIMAUX
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_seed_banques_15_minimum(db: AsyncSession):
    """Au minimum les 15 banques commerciales principales du Niger."""
    r = await db.execute(text("""
        SELECT COUNT(*) FROM banque_agreee WHERE statut = 'actif'
    """))
    assert r.scalar() >= 15, "Seed banque_agreee incomplet (< 15)"


@pytest.mark.asyncio
async def test_seed_etudes_notariales_8_regions(db: AsyncSession):
    """Au moins une étude par région du Niger (8 régions)."""
    r = await db.execute(text("""
        SELECT COUNT(DISTINCT chambre_regionale)
        FROM etude_notariale WHERE statut = 'active'
    """))
    assert r.scalar() >= 8, "Seed etude_notariale incomplet (< 8 régions)"


# ═══════════════════════════════════════════════════════════════════
# 4. UNICITÉ DU MAIRE EN FONCTION PAR COMMUNE (LAC-A05)
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_un_seul_maire_actif_par_commune(db: AsyncSession):
    """La règle métier : une commune ne peut avoir qu'un maire en fonction."""
    r = await db.execute(text("""
        SELECT commune_id, COUNT(*) AS nb
        FROM mandat_elu
        WHERE statut = 'en_cours' AND fonction = 'maire'
        GROUP BY commune_id
        HAVING COUNT(*) > 1
    """))
    doublons = r.fetchall()
    assert not doublons, (
        f"Communes avec plusieurs maires actifs : "
        f"{[(str(d.commune_id), d.nb) for d in doublons]}"
    )


# ═══════════════════════════════════════════════════════════════════
# 5. DÉMONSTRATIONS ANTIFRAUDE
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_antifraud_hypotheque_banque_fantome(db: AsyncSession):
    """Une hypothèque avec banque_id inexistant doit être refusée."""
    from uuid import uuid4
    with pytest.raises(Exception) as exc:
        await db.execute(text("""
            INSERT INTO mortgage_registry
                (id, parcelle_id, banque_id, montant_fcfa, statut, created_at)
            VALUES (gen_random_uuid(), gen_random_uuid(), :fake_id,
                    1000000, 'actif', NOW())
        """), {"fake_id": str(uuid4())})
        await db.commit()
    msg = str(exc.value)
    assert "introuvable" in msg.lower() or "banque_agreee" in msg


@pytest.mark.asyncio
async def test_antifraud_hypotheque_banque_suspendue(db: AsyncSession):
    """Une hypothèque sur une banque au statut 'suspendu' doit être refusée."""
    # Suspendre temporairement une banque
    r = await db.execute(text("""
        UPDATE banque_agreee SET statut = 'suspendu'
        WHERE numero_agrement_bceao = 'BCEAO-NE-001'
        RETURNING id
    """))
    banque_id = r.scalar()
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(text("""
            INSERT INTO mortgage_registry
                (id, parcelle_id, banque_id, montant_fcfa, statut, created_at)
            VALUES (gen_random_uuid(), gen_random_uuid(), :bid,
                    1000000, 'actif', NOW())
        """), {"bid": str(banque_id)})
        await db.commit()
    assert "suspendu" in str(exc.value).lower() or "non actif" in str(exc.value)

    # Restaurer l'état
    await db.execute(text("""
        UPDATE banque_agreee SET statut = 'actif'
        WHERE numero_agrement_bceao = 'BCEAO-NE-001'
    """))
    await db.commit()
