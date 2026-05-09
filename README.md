# FONCIER+ v1.0.9

**Plateforme Nationale de Gestion Foncière — République du Niger**
*Ministère de l'Urbanisme et de l'Habitat — DNCD*

## Versions

| Version | Date | Points clés |
|---|---|---|
| v1.0.9 | Avril 2026 | CCFM v3, ScopeFilter 25 modules, 40 migrations, 747 tests |
| v1.0.7 | Mars 2026 | Dashboard, Monitoring, Risk Engine, SQL Sandbox |
| v1.0.1 | Fév 2026 | Architecture nationale, Decision Engine, Audit immuable |
| v3.4.7 | Jan 2026 | CCFM QR, PDF A5, vérification publique |

## Métriques v1.0.9

- **Routes API** : 449 (25 modules, préfixe /v1 et /api/v1 et /api/v3)
- **Tests** : 747 (21 suites pytest)
- **Migrations** : 40 Alembic (002→041)
- **Python** : 102 fichiers backend
- **Frontend** : 22 fichiers TSX/TS (React 18 + TypeScript)
- **ScopeFilter** : 21/25 modules filtrés par acteur
- **SQL Sandbox** : 4 couches, 8/8 vecteurs bloqués

## Démarrage rapide

```bash
make setup-env      # Génère les secrets
make deploy-check   # Valide l'environnement
make deploy         # Build + up + migrations
make seed           # 45 utilisateurs seed
```

## Architecture

```
Commune → Arrêté → JO → RNAF → Cadastre/BGU → RNP → Attribution → CCFM → Transaction
```

## Documentation complète

Voir `docs/foncier_plus.md` (461 lignes).
