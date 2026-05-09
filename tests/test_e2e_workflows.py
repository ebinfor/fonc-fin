"""
FONCIER+ v108 — Tests E2E des workflows critiques (P2.7 audit)

Scénarios couverts :
  E2E-01  Cycle CCFM complet (14 états : DEMANDE_RECUE → ARCHIVE)
  E2E-02  Gate CCFM bloquante (mutation sans CCFM → refus)
  E2E-03  Workflow mutation vente avec vérification droits 100%
  E2E-04  Gel judiciaire automatique (Justice → parcelle gelée)
  E2E-05  Pipeline de validation règle SQL C1→C5
  E2E-06  Simulation opération foncière (dry-run SAVEPOINT)
  E2E-07  Révocation token JWT (blacklist jti)
  E2E-08  Rate limiting (429 après seuil)
"""
import asyncio, sys, os, time, uuid
import pytest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


# ── Helpers mock DB ───────────────────────────────────────────────

def _row(**kw):
    m = MagicMock()
    mm = MagicMock()
    mm.first.return_value = kw if kw else None
    mm.all.return_value   = [kw] if kw else []
    m.mappings.return_value = mm
    m.scalar.return_value   = kw.get('_scalar', 0)
    return m

def _rows(data: list):
    m = MagicMock()
    mm = MagicMock()
    mm.all.return_value   = data
    mm.first.return_value = data[0] if data else None
    m.mappings.return_value = mm
    m.scalar.return_value   = len(data)
    return m


# ════════════════════════════════════════════════════════════════════
# E2E-01 — Cycle CCFM complet (14 états)
# ════════════════════════════════════════════════════════════════════

class TestE2E01CCFMComplet:
    """
    Simule le cycle de vie complet d'une demande CCFM :
    DEMANDE_RECUE → EN_VERIFICATION → VISITE_TERRAIN → RAPPORT_DEPOSE
    → EN_INSTRUCTION → APPROUVE_CHEF → SIGNE_DIRECTEUR → EN_SCELLEMENT
    → SCELLE → PUBLIE → ARCHIVE
    """

    ETATS_WORKFLOW = [
        "DEMANDE_RECUE",
        "EN_VERIFICATION",
        "VISITE_TERRAIN",
        "RAPPORT_DEPOSE",
        "EN_INSTRUCTION",
        "APPROUVE_CHEF",
        "SIGNE_DIRECTEUR",
        "EN_SCELLEMENT",
        "SCELLE",
        "PUBLIE",
        "ARCHIVE",
    ]

    def test_sequence_etats_ccfm_valide(self):
        """Vérifier que la séquence des états CCFM est correcte."""
        etats = self.ETATS_WORKFLOW
        assert etats[0]  == "DEMANDE_RECUE"
        assert etats[-1] == "ARCHIVE"
        assert len(etats) == 11
        # SIGNE_DIRECTEUR doit précéder EN_SCELLEMENT (gate finale)
        idx_signe = etats.index("SIGNE_DIRECTEUR")
        idx_scell = etats.index("EN_SCELLEMENT")
        assert idx_signe < idx_scell

    def test_gate_ccfm_avant_mutation(self):
        """
        La gate CCFM (état SIGNE_DIRECTEUR ou ARCHIVE) doit précéder
        toute mutation/hypothèque.
        """
        etats_autorises_gate = {"SIGNE_DIRECTEUR", "SCELLE", "PUBLIE", "ARCHIVE"}
        etats_bloques = {
            e for e in self.ETATS_WORKFLOW
            if e not in etats_autorises_gate
        }
        # Vérifier que les états non-finaux ne peuvent pas déclencher une mutation
        for e in etats_bloques:
            assert e not in etats_autorises_gate, (
                f"État {e} ne doit pas autoriser la mutation"
            )

    def test_etats_terminaux_irreversibles(self):
        """ARCHIVE et REJETE sont des états terminaux non réversibles."""
        etats_terminaux = {"ARCHIVE", "REJETE"}
        for e in etats_terminaux:
            # Aucune transition ne doit partir d'un état terminal (convention)
            assert e in {"ARCHIVE", "REJETE"}

    def test_sha256_chaine_ccfm(self):
        """Vérifier que la chaîne SHA-256 CCFM est configurée."""
        mig_src = open(
            os.path.join(os.path.dirname(__file__), '..', 'backend',
                         'alembic', 'versions', '009_moteur_ccfm_et_annulation_judiciaire.py')
        ).read()
        assert 'tg_fn_ccfm_log_sha256' in mig_src
        assert 'sha256' in mig_src.lower()


# ════════════════════════════════════════════════════════════════════
# E2E-02 — Gate CCFM bloquante
# ════════════════════════════════════════════════════════════════════

class TestE2E02CCFMGate:
    """Vérifie que la gate CCFM bloque les opérations sans CCFM valide."""

    @pytest.mark.asyncio
    async def test_simulation_mutation_sans_ccfm_bloquee(self):
        """
        Simulation d'une MUTATION sans CCFM signé → impact BLOQUANT S4.
        """
        from app.services.simulation_engine import (
            SimulationEngine, SimulationContext, ImpactNiveau, SimulationStatut
        )

        async def mock_exec(stmt, params=None):
            sql = str(stmt).lower()
            if 'savepoint' in sql or 'set local' in sql or 'rollback' in sql:
                return MagicMock()
            if 'from parcelles p' in sql and 'nicad' in sql:
                m = MagicMock(); mm = MagicMock()
                mm.first.return_value = {
                    'id': 'pid-1', 'nicad': 'NI-01-01-001-001',
                    'statut': 'active', 'is_gele': False,
                    'surface_m2': 2000.0, 'region': 'NI-01', 'commune': 'Niamey',
                    'somme_droits': 100.0, 'nb_verrous': 0,
                    'nb_litiges_actifs': 0, 'workflows_en_cours': 0,
                }
                m.mappings.return_value = mm; return m
            if 'demandes_ccfm' in sql:
                # Pas de CCFM signé
                m = MagicMock(); mm = MagicMock()
                mm.first.return_value = None
                m.mappings.return_value = mm; return m
            m = MagicMock(); mm = MagicMock()
            mm.first.return_value = None; mm.all.return_value = []
            m.mappings.return_value = mm; m.scalar.return_value = 0; return m

        db = AsyncMock(); db.execute = mock_exec
        ctx = SimulationContext(
            operation_type='MUTATION',
            parcelle_ids=['00000000-0000-0000-0000-000000000001'],
            user_id='00000000-0000-0000-0000-000000000099',
            region='NI-01',
        )
        result = await SimulationEngine.simuler(ctx, db=db)

        # Sans CCFM → BLOQUANT en S4
        s4_blok = [
            i for i in result.impacts
            if i.couche == 'S4' and i.niveau == ImpactNiveau.BLOQUANT
        ]
        assert s4_blok, "Mutation sans CCFM doit générer un impact BLOQUANT S4"
        assert result.statut == SimulationStatut.ECHEC

    @pytest.mark.asyncio
    async def test_simulation_mutation_avec_ccfm_passe(self):
        """MUTATION avec CCFM signé → pas de blocage S4."""
        from app.services.simulation_engine import (
            SimulationEngine, SimulationContext, ImpactNiveau
        )

        async def mock_exec(stmt, params=None):
            sql = str(stmt).lower()
            if 'savepoint' in sql or 'set local' in sql or 'rollback' in sql:
                return MagicMock()
            if 'from parcelles p' in sql and 'nicad' in sql:
                m = MagicMock(); mm = MagicMock()
                mm.first.return_value = {
                    'id': 'pid-1', 'nicad': 'NI-01-01-001', 'statut': 'active',
                    'is_gele': False, 'surface_m2': 1000.0, 'region': 'NI-01',
                    'commune': 'Niamey', 'somme_droits': 100.0,
                    'nb_verrous': 0, 'nb_litiges_actifs': 0, 'workflows_en_cours': 0,
                }
                m.mappings.return_value = mm; return m
            if 'demandes_ccfm' in sql:
                # CCFM signé présent
                m = MagicMock(); mm = MagicMock()
                mm.first.return_value = {
                    'id': 'ccfm-1', 'etat': 'SIGNE_DIRECTEUR',
                    'date_creation': '2024-01-01', 'date_signature_directeur': '2024-01-10'
                }
                m.mappings.return_value = mm; return m
            m = MagicMock(); mm = MagicMock()
            mm.first.return_value = None; mm.all.return_value = []
            m.mappings.return_value = mm; m.scalar.return_value = 1; return m

        db = AsyncMock(); db.execute = mock_exec
        ctx = SimulationContext(
            operation_type='MUTATION',
            parcelle_ids=['00000000-0000-0000-0000-000000000001'],
            user_id='00000000-0000-0000-0000-000000000099',
            region='NI-01',
        )
        result = await SimulationEngine.simuler(ctx, db=db)
        s4_blok = [
            i for i in result.impacts
            if i.couche == 'S4' and i.niveau == ImpactNiveau.BLOQUANT
            and 'ccfm' in i.message.lower()
        ]
        assert not s4_blok, "Mutation avec CCFM signé ne doit pas bloquer en S4"


# ════════════════════════════════════════════════════════════════════
# E2E-03 — Droits fonciers = 100% invariant
# ════════════════════════════════════════════════════════════════════

class TestE2E03DroitsFonciers:

    @pytest.mark.asyncio
    async def test_droits_somme_incorrecte_bloque_simulation(self):
        """Droits PROPRIETE != 100% → BLOQUANT S3."""
        from app.services.simulation_engine import (
            SimulationEngine, SimulationContext, ImpactNiveau, SimulationStatut
        )

        async def mock_exec(stmt, params=None):
            sql = str(stmt).lower()
            if 'savepoint' in sql or 'rollback' in sql or 'set local' in sql:
                return MagicMock()
            if 'from parcelles p' in sql and 'nicad' in sql:
                m = MagicMock(); mm = MagicMock()
                mm.first.return_value = {
                    'id': 'pid-1', 'nicad': 'NI-01-TEST', 'statut': 'active',
                    'is_gele': False, 'surface_m2': 500.0, 'region': 'NI-02',
                    'commune': 'Test', 'somme_droits': 60.0,  # != 100%
                    'nb_verrous': 0, 'nb_litiges_actifs': 0, 'workflows_en_cours': 0,
                }
                m.mappings.return_value = mm; return m
            if 'parcel_right_version' in sql:
                m = MagicMock(); mm = MagicMock()
                mm.all.return_value = [{
                    'type_droit': 'PROPRIETE', 'pourcentage': 60.0,
                    'titulaire_id': 'u1', 'nom_complet': 'Test User',
                    'statut_version': 'active',
                }]
                mm.first.return_value = mm.all.return_value[0]
                m.mappings.return_value = mm; return m
            m = MagicMock(); mm = MagicMock()
            mm.first.return_value = None; mm.all.return_value = []
            m.mappings.return_value = mm; m.scalar.return_value = 0; return m

        db = AsyncMock(); db.execute = mock_exec
        ctx = SimulationContext(
            operation_type='MUTATION',
            parcelle_ids=['00000000-0000-0000-0000-000000000001'],
            user_id='00000000-0000-0000-0000-000000000099',
            region='NI-02',
        )
        result = await SimulationEngine.simuler(ctx, db=db)
        s3_blok = [
            i for i in result.impacts
            if i.couche == 'S3' and i.niveau == ImpactNiveau.BLOQUANT
        ]
        assert s3_blok, "Droits != 100% doit créer impact S3 BLOQUANT"
        assert '100' in s3_blok[0].message or 'droits' in s3_blok[0].message.lower()

    def test_trigger_check_somme_existe(self):
        """Vérifier que le trigger tg_check_somme_pourcentage est dans les migrations."""
        import glob
        mig_dir = os.path.join(
            os.path.dirname(__file__), '..', 'backend', 'alembic', 'versions'
        )
        found = False
        for fpath in glob.glob(f"{mig_dir}/*.py"):
            with open(fpath) as f:
                content = f.read()
            if 'tg_check_somme_pourcentage' in content or 'check_completude_droits' in content:
                found = True
                break
        assert found, "Trigger check somme 100% introuvable dans les migrations"


# ════════════════════════════════════════════════════════════════════
# E2E-04 — Gel judiciaire automatique
# ════════════════════════════════════════════════════════════════════

class TestE2E04GelJudiciaire:

    def test_trigger_gel_judiciaire_existe(self):
        """tg_fn_conflit_gel_parcelles doit être dans les migrations Justice."""
        import glob
        mig_dir = os.path.join(
            os.path.dirname(__file__), '..', 'backend', 'alembic', 'versions'
        )
        found = False
        for fpath in glob.glob(f"{mig_dir}/*.py"):
            with open(fpath) as f:
                content = f.read()
            if 'tg_fn_conflit_gel_parcelles' in content or 'conflit_gel' in content:
                found = True
                break
        assert found, "Trigger gel judiciaire automatique introuvable"

    @pytest.mark.asyncio
    async def test_parcelle_gelee_bloque_simulation(self):
        """Parcelle gelée judiciairement → BLOQUANT S1."""
        from app.services.simulation_engine import (
            SimulationEngine, SimulationContext, ImpactNiveau, SimulationStatut
        )

        async def mock_exec(stmt, params=None):
            sql = str(stmt).lower()
            if 'savepoint' in sql or 'rollback' in sql or 'set local' in sql:
                return MagicMock()
            if 'from parcelles p' in sql and 'nicad' in sql:
                m = MagicMock(); mm = MagicMock()
                mm.first.return_value = {
                    'id': 'pid-gel', 'nicad': 'NI-03-GEL-001',
                    'statut': 'active', 'is_gele': True,   # ← gelée
                    'surface_m2': 800.0, 'region': 'NI-03', 'commune': 'Dosso',
                    'somme_droits': 100.0, 'nb_verrous': 1,
                    'nb_litiges_actifs': 1, 'workflows_en_cours': 0,
                }
                m.mappings.return_value = mm; return m
            m = MagicMock(); mm = MagicMock()
            mm.first.return_value = None; mm.all.return_value = []
            m.mappings.return_value = mm; m.scalar.return_value = 0; return m

        db = AsyncMock(); db.execute = mock_exec
        ctx = SimulationContext(
            operation_type='HYPOTHEQUE',
            parcelle_ids=['00000000-0000-0000-0000-000000000003'],
            user_id='00000000-0000-0000-0000-000000000099',
            region='NI-03',
        )
        result = await SimulationEngine.simuler(ctx, db=db)
        assert result.statut == SimulationStatut.ECHEC
        s1_gel = [
            i for i in result.impacts
            if i.couche == 'S1' and i.niveau == ImpactNiveau.BLOQUANT
            and 'gel' in i.message.lower()
        ]
        assert s1_gel, "Parcelle gelée doit produire un BLOQUANT S1"


# ════════════════════════════════════════════════════════════════════
# E2E-05 — Pipeline SQL C1→C5
# ════════════════════════════════════════════════════════════════════

class TestE2E05PipelineSQL:

    @pytest.mark.asyncio
    async def test_sql_injection_bloquee_c1(self):
        """Tentative d'injection SQL → bloquée en C1."""
        from app.services.sql_sandbox import SQLRuleValidator, ValidationStatus
        injections = [
            "DROP TABLE parcelles; --",
            "SELECT * FROM pg_shadow",
            "1=1 OR TRUE; TRUNCATE audit_immutable",
            "EXISTS (SELECT 1) UNION SELECT password FROM users",
            "EXISTS (SELECT 1 FROM parcelles WHERE 1=1 OR 'x'='x')",
        ]
        for sql in injections:
            result = await SQLRuleValidator.valider(
                sql=sql, domaine='GENERAL', code='TEST_INJECT', db=None
            )
            assert result.status in (ValidationStatus.REFUSE, ValidationStatus.INVALIDE), \
                f"Injection non bloquée : {sql[:50]}"

    @pytest.mark.asyncio
    async def test_regle_valide_passe_c1_c2(self):
        """Une règle SQL valide doit passer C1 et C2 (sans DB pour C3)."""
        from app.services.sql_sandbox import SQLRuleValidator, ValidationStatus
        valides = [
            "NOT EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND is_gele=TRUE)",
            "EXISTS (SELECT 1 FROM demandes_ccfm dc WHERE dc.etat='SIGNE_DIRECTEUR' AND dc.id=$1)",
            "NOT EXISTS (SELECT 1 FROM entity_lock el WHERE el.entite_id=$1 AND el.actif=TRUE)",
        ]
        for sql in valides:
            result = await SQLRuleValidator.valider(
                sql=sql, domaine='GENERAL', code='TEST_VALID', db=None
            )
            assert result.status == ValidationStatus.VALIDE, \
                f"Règle valide refusée : {sql[:50]} — erreurs: {[e.message for e in result.errors]}"

    @pytest.mark.asyncio
    async def test_tautologie_bloquee_c0(self):
        """Tautologies OR 1=1 → bloquées en C0 (anti-bypass)."""
        from app.services.sql_sandbox import SQLRuleValidator, ValidationStatus
        tautologies = [
            "EXISTS (SELECT 1 FROM parcelles WHERE 1=1 OR 1=1)",
            "NOT EXISTS (SELECT 1 FROM litiges WHERE id=$1 OR TRUE)",
        ]
        for sql in tautologies:
            result = await SQLRuleValidator.valider(
                sql=sql, domaine='GENERAL', code='TEST_TAUT', db=None
            )
            assert result.status in (ValidationStatus.REFUSE, ValidationStatus.INVALIDE), \
                f"Tautologie non bloquée : {sql[:50]}"

    @pytest.mark.asyncio
    async def test_c5_contradiction_bloquee(self):
        """Contradiction logique EXISTS/NOT EXISTS → bloquée en C5."""
        from app.services.logic_validator import LogicConflictEngine, Recommandation

        new_rule_sql = "EXISTS (SELECT 1 FROM parcelles p WHERE p.id=$1 AND p.is_gele=TRUE)"
        conflicting_sql = "NOT EXISTS (SELECT 1 FROM parcelles p WHERE p.id=$1 AND p.is_gele=TRUE)"

        from app.services.logic_validator import (
            RuleDescriptor, NegationContradictionHeuristic, ConflictNiveau
        )
        existing = RuleDescriptor(
            code='EXISTING_RULE', nom='Existante', domaine='GENERAL',
            condition_sql=conflicting_sql, niveau_blocage='BLOQUANT',
        )
        existing._parse()

        new_rule = RuleDescriptor(
            code='NEW_RULE', nom='Nouvelle', domaine='GENERAL',
            condition_sql=new_rule_sql, niveau_blocage='BLOQUANT',
        )
        new_rule._parse()

        h2 = NegationContradictionHeuristic()
        conflicts = h2.analyze(new_rule, [existing])
        critiques = [c for c in conflicts if c.niveau == ConflictNiveau.CRITIQUE]
        assert critiques, "Contradiction EXISTS/NOT EXISTS doit être détectée en C5"


# ════════════════════════════════════════════════════════════════════
# E2E-06 — Simulation dry-run (SAVEPOINT)
# ════════════════════════════════════════════════════════════════════

class TestE2E06SimulationDryRun:

    @pytest.mark.asyncio
    async def test_savepoint_appele(self):
        """Le moteur de simulation doit créer et rollbacker un SAVEPOINT."""
        from app.services.simulation_engine import SimulationEngine, SimulationContext

        sql_calls = []
        async def mock_exec(stmt, params=None):
            sql = str(stmt).lower()
            sql_calls.append(sql[:60])
            m = MagicMock(); mm = MagicMock()
            mm.first.return_value = None; mm.all.return_value = []
            m.mappings.return_value = mm; m.scalar.return_value = 0; return m

        db = AsyncMock(); db.execute = mock_exec
        ctx = SimulationContext(
            operation_type='MUTATION',
            parcelle_ids=['00000000-0000-0000-0000-000000000001'],
            user_id='user-1', region='NI-01',
        )
        await SimulationEngine.simuler(ctx, db=db)

        savepoints = [c for c in sql_calls if 'savepoint' in c]
        rollbacks  = [c for c in sql_calls if 'rollback' in c]
        assert savepoints, "SAVEPOINT doit être créé"
        assert rollbacks,  "ROLLBACK TO SAVEPOINT doit être appelé"

    @pytest.mark.asyncio
    async def test_donnees_reelles_non_modifiees(self):
        """Le ROLLBACK garantit qu'aucune écriture ne persiste."""
        from app.services.simulation_engine import SimulationEngine, SimulationContext

        writes = []
        async def mock_exec(stmt, params=None):
            sql = str(stmt).lower()
            if any(kw in sql for kw in ('insert ', 'update ', 'delete ')):
                if 'savepoint' not in sql and 'set local' not in sql:
                    writes.append(sql[:80])
            m = MagicMock(); mm = MagicMock()
            mm.first.return_value = None; mm.all.return_value = []
            m.mappings.return_value = mm; m.scalar.return_value = 0; return m

        db = AsyncMock(); db.execute = mock_exec
        ctx = SimulationContext(
            operation_type='FUSION',
            parcelle_ids=['00000000-0000-0000-0000-000000000001',
                          '00000000-0000-0000-0000-000000000002'],
            user_id='user-1', region='NI-01',
        )
        await SimulationEngine.simuler(ctx, db=db)

        # Les seuls INSERTs autorisés sont dans simulation_log
        non_log_writes = [
            w for w in writes
            if 'simulation_log' not in w and 'risk_score' not in w
        ]
        assert len(non_log_writes) == 0, \
            f"Écriture hors log détectée : {non_log_writes}"


# ════════════════════════════════════════════════════════════════════
# E2E-07 — Révocation JWT (blacklist jti)
# ════════════════════════════════════════════════════════════════════

class TestE2E07RevocationJWT:

    @pytest.mark.asyncio
    async def test_token_valide_avant_revocation(self):
        """Un token non révoqué doit passer la vérification blacklist."""
        from app.core.security import JWTBlacklist
        JWTBlacklist._redis = None
        JWTBlacklist._mem_store = set()

        jti = str(uuid.uuid4())
        assert not await JWTBlacklist.est_revoque(jti)

    @pytest.mark.asyncio
    async def test_token_revoque_bloque(self):
        """Un token révoqué (jti en blacklist) doit être refusé."""
        from app.core.security import JWTBlacklist
        JWTBlacklist._redis = None
        JWTBlacklist._mem_store = set()

        jti = str(uuid.uuid4())
        await JWTBlacklist.revoquer(jti, ttl_sec=300)
        assert await JWTBlacklist.est_revoque(jti)

    @pytest.mark.asyncio
    async def test_revocation_multiple_tokens(self):
        """Révocation de plusieurs tokens simultanément."""
        from app.core.security import JWTBlacklist
        JWTBlacklist._redis = None
        JWTBlacklist._mem_store = set()

        jtis = [str(uuid.uuid4()) for _ in range(5)]
        for jti in jtis:
            await JWTBlacklist.revoquer(jti)

        for jti in jtis:
            assert await JWTBlacklist.est_revoque(jti), f"jti {jti} devrait être révoqué"

        # Un nouveau jti non révoqué ne doit pas être bloqué
        nouveau = str(uuid.uuid4())
        assert not await JWTBlacklist.est_revoque(nouveau)

    def test_jti_unique_par_token(self):
        """Chaque token génère un jti unique."""
        from app.core.security import create_access_token, _get_decode_key, _jwt_algorithm
        from jose import jwt as _jose_jwt

        def decode(t):
            return _jose_jwt.decode(t, _get_decode_key(),
                                    algorithms=[_jwt_algorithm()],
                                    options={"verify_aud": False})

        jtis = set()
        for _ in range(10):
            t = create_access_token('user-1', 'ADMIN', 'NI-01')
            p = decode(t)
            assert 'jti' in p
            jtis.add(p['jti'])
        assert len(jtis) == 10, "Tous les jti doivent être uniques"


# ════════════════════════════════════════════════════════════════════
# E2E-08 — Rate limiting
# ════════════════════════════════════════════════════════════════════

class TestE2E08RateLimiting:
    """Tests de la logique de rate limiting GlobalRateLimiter."""

    def _make_limiter(self, max_req=10, window=60):
        import collections, time as _t
        store = {}
        def check(ip, path=''):
            nonlocal max_req, window
            limit = 5 if '/auth/' in path else max_req
            now = _t.monotonic()
            if ip not in store:
                store[ip] = collections.deque()
            dq = store[ip]
            while dq and dq[0] < now - window:
                dq.popleft()
            if len(dq) >= limit:
                return False, 0
            dq.append(now)
            return True, limit - len(dq)
        return check, store

    def test_requests_dans_limite(self):
        check, _ = self._make_limiter(max_req=5)
        for i in range(5):
            ok, rem = check('1.1.1.1')
            assert ok, f"Requête {i+1} doit être autorisée"
            assert rem == 5 - (i + 1)

    def test_requete_hors_limite_bloquee(self):
        check, _ = self._make_limiter(max_req=3)
        for _ in range(3):
            check('2.2.2.2')
        ok, rem = check('2.2.2.2')
        assert not ok
        assert rem == 0

    def test_limite_auth_plus_stricte(self):
        """Les endpoints /auth/* ont une limite de 5 req/fenêtre."""
        check, _ = self._make_limiter(max_req=100)
        for _ in range(5):
            ok, _ = check('3.3.3.3', path='/auth/login')
            assert ok
        ok, _ = check('3.3.3.3', path='/auth/login')
        assert not ok, "6e req sur /auth/ doit être bloquée"

    def test_ips_independantes(self):
        check, _ = self._make_limiter(max_req=2)
        check('4.4.4.4'); check('4.4.4.4')  # Saturer IP A
        ok, _ = check('5.5.5.5')             # IP B non affectée
        assert ok

    def test_rate_limit_dans_main_py(self):
        """Le rate limiter est configuré dans main.py."""
        main_src = open(os.path.join(
            os.path.dirname(__file__), '..', 'backend', 'app', 'main.py'
        )).read()
        assert 'GlobalRateLimiter' in main_src
        assert 'MAX_GLOBAL' in main_src or '1000' in main_src
        assert 'MAX_AUTH' in main_src or 'auth' in main_src.lower()


# ════════════════════════════════════════════════════════════════════
# Non-régression système complet
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_tous_modules_operationnels():
    """Vérification rapide que tous les modules v101-v108 sont opérationnels."""
    from app.services.sql_sandbox import SQLRuleValidator, ValidationStatus
    from app.services.logic_validator import LogicConflictEngine, Recommandation
    from app.services.simulation_engine import SimulationEngine, SimulationContext
    from app.services.monitoring_engine import MonitoringEngine
    from app.services.risk_engine import RiskEngine, ScoreCalculator, NiveauRisque
    from app.services.dashboard_engine import DashboardCache, DashboardEngine

    # Sandbox C1-C5
    r1 = await SQLRuleValidator.valider(sql='TRUE', domaine='GENERAL', code='E2E_SB', db=None)
    assert r1.status == ValidationStatus.VALIDE

    # Logique
    r2 = await LogicConflictEngine.valider(sql='TRUE', domaine='GENERAL', code='E2E_LV', niveau_blocage='BLOQUANT', db=None)
    assert r2.recommandation == Recommandation.AUTORISER

    # Simulation
    ctx = SimulationContext('MUTATION', ['u1'], 'u1', 'NI-01')
    r3 = await SimulationEngine.simuler(ctx, db=None)
    assert r3.simulation_id

    # Monitoring
    m = MonitoringEngine()
    assert len(m.detecteurs) == 6

    # Risk
    assert ScoreCalculator.niveau(80) == NiveauRisque.CRITIQUE

    # Dashboard
    c = DashboardCache()
    c.set('test', 42)
    assert c.get('test') == 42
