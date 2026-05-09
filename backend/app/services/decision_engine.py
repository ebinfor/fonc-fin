"""
FONCIER+ -- Decision Engine Central
Arbitre toutes les decisions critiques du systeme foncier.

Responsabilites :
  - Evaluer les regles metier versionnees (table regle_metier)
  - Valider les juridictions (check_juridiction)
  - Gerer les verrous entites (entity_lock)
  - Journaliser dans audit_immutable
  - Detecter les conflits entre workflows
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger("decision_engine")


class DecisionResult:
    """Resultat d une decision du moteur."""
    def __init__(self, autorise: bool, decision: str, motifs: list,
                 sha256: str = "", regles: int = 0):
        self.autorise      = autorise
        self.decision      = decision
        self.motifs        = motifs
        self.sha256        = sha256
        self.regles_count  = regles

    def as_dict(self) -> dict:
        return {
            "autorise":  self.autorise,
            "decision":  self.decision,
            "motifs":    self.motifs,
            "sha256":    self.sha256,
        }

    def raise_if_refused(self, operation: str = "operation"):
        if not self.autorise:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "DECISION_REFUSEE",
                    "operation": operation,
                    "motifs": self.motifs,
                    "sha256": self.sha256,
                    "correction": "Verifier les conditions requises pour cette operation",
                }
            )


class DecisionEngine:
    """
    Moteur central de decision FONCIER+.
    Toutes les operations critiques doivent passer par ce moteur.
    """

    # ------------------------------------------------------------------
    # 1. EVALUATION DES REGLES METIER
    # ------------------------------------------------------------------

    @staticmethod
    async def evaluer(
        db: AsyncSession,
        entite_type: str,
        entite_id: str,
        operation: str,
        user_id: Optional[str] = None,
        contexte: Optional[dict] = None,
    ) -> DecisionResult:
        """
        Evalue toutes les regles metier applicables et retourne une decision.
        Journalise dans decision_engine_log.
        """
        try:
            r = await db.execute(text("""
                SELECT autorise, decision, regles_evaluees, blocages,
                       avertissements, motifs, sha256
                FROM decision_engine_evaluer(
                    :et, :eid::uuid, :op, :uid::uuid, :ctx::jsonb
                )
            """), {
                "et":  entite_type,
                "eid": entite_id,
                "op":  operation,
                "uid": user_id,
                "ctx": json.dumps(contexte or {}),
            })
            row = r.mappings().first()
            if row:
                return DecisionResult(
                    autorise  = bool(row["autorise"]),
                    decision  = row["decision"],
                    motifs    = list(row["motifs"] or []),
                    sha256    = row["sha256"] or "",
                    regles    = row["regles_evaluees"] or 0,
                )
        except Exception as e:
            _log.warning("decision_engine.evaluer: %s", e)

        # Fallback : autorise si le moteur ne peut pas evaluer
        return DecisionResult(autorise=True, decision="AUTORISE_FALLBACK",
                               motifs=["Moteur non disponible -- fallback permissif"], sha256="")

    # ------------------------------------------------------------------
    # 2. VERIFICATION DE JURIDICTION
    # ------------------------------------------------------------------

    @staticmethod
    async def verifier_juridiction(
        db: AsyncSession,
        user_id: str,
        region: str,
    ) -> bool:
        """Verifie qu un agent a la juridiction pour operer sur une region."""
        try:
            r = await db.execute(text(
                "SELECT check_juridiction(:uid::uuid, :reg)"
            ), {"uid": user_id, "reg": region})
            return bool(r.scalar())
        except Exception as e:
            _log.warning("check_juridiction: %s", e)
            return True  # Fallback permissif

    @staticmethod
    async def exiger_juridiction(
        db: AsyncSession,
        user_id: str,
        region: str,
        operation: str = "operation",
    ) -> None:
        """Leve une HTTPException 403 si la juridiction n est pas respectee."""
        ok = await DecisionEngine.verifier_juridiction(db, user_id, region)
        if not ok:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "HORS_JURIDICTION",
                    "message": f"Agent non autorise a effectuer '{operation}' dans la region '{region}'",
                    "region": region,
                    "correction": "Contacter l administrateur pour une extension de juridiction",
                }
            )

    # ------------------------------------------------------------------
    # 3. GESTION DES VERROUS
    # ------------------------------------------------------------------

    @staticmethod
    async def poser_verrou(
        db: AsyncSession,
        entite_type: str,
        entite_id: str,
        workflow_type: str,
        user_id: str,
        raison: Optional[str] = None,
        duree_heures: int = 24,
    ) -> dict:
        """Pose un verrou sur une entite. Echoue si deja verrouillee."""
        try:
            r = await db.execute(text("""
                SELECT ok, message, lock_id
                FROM entity_lock_poser(
                    :et, :eid::uuid, :wt, :uid::uuid, :raison, :duree
                )
            """), {
                "et": entite_type, "eid": entite_id, "wt": workflow_type,
                "uid": user_id, "raison": raison, "duree": duree_heures,
            })
            row = r.mappings().first()
            if row and not row["ok"]:
                from fastapi import HTTPException
                raise HTTPException(409, {
                    "code": "ENTITE_VERROUILLEE",
                    "message": row["message"],
                    "entite_type": entite_type,
                    "entite_id": entite_id,
                    "correction": "Attendre la liberation du verrou ou contacter le responsable du workflow",
                })
            await db.commit()
            return {"lock_id": str(row["lock_id"]) if row else None, "ok": True}
        except Exception as e:
            if "ENTITE_VERROUILLEE" in str(e):
                raise
            _log.warning("poser_verrou: %s", e)
            return {"lock_id": None, "ok": False}

    @staticmethod
    async def liberer_verrou(
        db: AsyncSession,
        entite_type: str,
        entite_id: str,
        user_id: str,
    ) -> bool:
        """Libere le verrou d une entite."""
        try:
            r = await db.execute(text(
                "SELECT entity_lock_liberer(:et, :eid::uuid, :uid::uuid)"
            ), {"et": entite_type, "eid": entite_id, "uid": user_id})
            await db.commit()
            return bool(r.scalar())
        except Exception as e:
            _log.warning("liberer_verrou echec (verrou non libere): %s", e)
            return False  # Le verrou reste actif — à libérer manuellement

    # ------------------------------------------------------------------
    # 4. AUDIT IMMUABLE
    # ------------------------------------------------------------------

    @staticmethod
    async def auditer(
        db: AsyncSession,
        entite_type: str,
        entite_id: str,
        operation: str,
        etat_avant: Optional[dict] = None,
        etat_apres: Optional[dict] = None,
        user_id: Optional[str] = None,
        region: Optional[str] = None,
    ) -> str:
        """
        Enregistre une entree dans l audit immuable (append-only, hash chaining).
        Retourne le hash SHA-256 de l entree.
        """
        try:
            r = await db.execute(text("""
                SELECT audit_enregistrer(
                    :et, :eid::uuid, :op,
                    :avant::jsonb, :apres::jsonb,
                    :uid::uuid, :reg, NULL
                )
            """), {
                "et":    entite_type,
                "eid":   entite_id,
                "op":    operation,
                "avant": json.dumps(etat_avant) if etat_avant else None,
                "apres": json.dumps(etat_apres) if etat_apres else None,
                "uid":   user_id,
                "reg":   region,
            })
            return r.scalar() or ""
        except Exception as e:
            _log.warning("audit_enregistrer: %s", e)
            return ""

    # ------------------------------------------------------------------
    # 5. VALIDATION MULTI-NIVEAUX
    # ------------------------------------------------------------------

    @staticmethod
    async def creer_pipeline_validation(
        db: AsyncSession,
        entite_type: str,
        entite_id: str,
        workflow_type: str,
        niveau_requis: int = 1,
    ) -> str:
        """Cree un pipeline de validation multi-niveaux. Retourne l ID."""
        try:
            sha_init = hashlib.sha256(
                f"{entite_type}|{entite_id}|{workflow_type}|INIT".encode()
            ).hexdigest()
            r = await db.execute(text("""
                INSERT INTO validation_pipeline (
                    entite_type, entite_id, workflow_type,
                    niveau_requis, sha256_chain
                ) VALUES (:et, :eid::uuid, :wt, :nr, :sha)
                RETURNING id
            """), {
                "et": entite_type, "eid": entite_id,
                "wt": workflow_type, "nr": niveau_requis, "sha": sha_init,
            })
            pid = r.scalar()
            await db.commit()
            return str(pid) if pid else ""
        except Exception as e:
            _log.warning("creer_pipeline: %s", e)
            return ""

    @staticmethod
    async def valider_niveau(
        db: AsyncSession,
        pipeline_id: str,
        validateur_id: str,
        commentaire: Optional[str] = None,
    ) -> str:
        """Passe au niveau suivant de validation. Retourne 'OK_NIVEAU_N' ou erreur."""
        try:
            r = await db.execute(text(
                "SELECT valider_niveau_suivant(:pid::uuid, :vid::uuid, :com)"
            ), {"pid": pipeline_id, "vid": validateur_id, "com": commentaire})
            await db.commit()
            return r.scalar() or "ERREUR"
        except Exception as e:
            _log.warning("valider_niveau: %s", e)
            return f"ERREUR: {str(e)[:100]}"

    # ------------------------------------------------------------------
    # 6. RESOLUTION DE CONFLITS INTER-WORKFLOWS
    # ------------------------------------------------------------------

    @staticmethod
    async def detecter_conflits(
        db: AsyncSession,
        parcelle_id: str,
        workflow_type: str,
    ) -> list:
        """Detecte les conflits entre workflows sur une meme parcelle."""
        conflits = []
        try:
            # Verrous actifs
            r = await db.execute(text("""
                SELECT el.workflow_type, el.raison, el.date_expiration,
                       u.email AS verrou_par
                FROM entity_lock el
                JOIN users u ON u.id = el.verrou_par
                WHERE el.entite_type = 'parcelle'
                  AND el.entite_id = :pid::uuid
                  AND el.actif = TRUE
                  AND el.date_expiration > NOW()
            """), {"pid": parcelle_id})
            for row in r.mappings().all():
                conflits.append({
                    "type": "VERROU_ACTIF",
                    "workflow": row["workflow_type"],
                    "raison": row["raison"],
                    "expiration": str(row["date_expiration"]),
                    "verrou_par": row["verrou_par"],
                })

            # Pipelines de validation en cours
            r2 = await db.execute(text("""
                SELECT workflow_type, statut, niveau_courant, niveau_requis
                FROM validation_pipeline
                WHERE entite_id = :pid::uuid
                  AND statut NOT IN ('VALIDE','REJETE')
                  AND entite_type = 'parcelle'
            """), {"pid": parcelle_id})
            for row in r2.mappings().all():
                conflits.append({
                    "type": "VALIDATION_EN_COURS",
                    "workflow": row["workflow_type"],
                    "statut": row["statut"],
                    "progression": f"{row['niveau_courant']}/{row['niveau_requis']}",
                })

        except Exception as e:
            _log.warning("detecter_conflits: %s", e)

        return conflits

    # ------------------------------------------------------------------
    # 7. VALIDATION TOPOLOGIQUE
    # ------------------------------------------------------------------

    @staticmethod
    async def valider_topologie(
        db: AsyncSession,
        parcelle_id: str,
        geom_wkt: str,
        operation: str = "VALIDATION",
    ) -> dict:
        """Valide la topologie d une geometrie (chevauchements, validite)."""
        try:
            r = await db.execute(text("""
                SELECT valide, bloque, message, overlap_count
                FROM topo_valider_geom(:pid::uuid, :wkt, :op)
            """), {"pid": parcelle_id, "wkt": geom_wkt, "op": operation})
            row = r.mappings().first()
            if row:
                return dict(row)
        except Exception as e:
            _log.warning("valider_topologie: %s", e)
        return {"valide": False, "bloque": False,
                "message": "Validation non disponible", "overlap_count": 0}

    # ------------------------------------------------------------------
    # 8. DELEGATION ADMINISTRATIVE
    # ------------------------------------------------------------------

    @staticmethod
    async def delegataire_actif(
        db: AsyncSession,
        user_id: str,
        domaine: str,
        region: Optional[str] = None,
    ) -> Optional[str]:
        """
        Retourne l ID du delegataire actif si une delegation est en cours.
        Retourne None si aucune delegation active.
        """
        try:
            r = await db.execute(text("""
                SELECT delegataire_id
                FROM delegation_administrative
                WHERE delegant_id = :uid::uuid
                  AND statut = 'ACTIVE'
                  AND date_fin > NOW()
                  AND (domaine = :dom OR domaine = 'GENERAL')
                  AND (region_concernee IS NULL OR region_concernee = :reg)
                ORDER BY date_debut DESC LIMIT 1
            """), {"uid": user_id, "dom": domaine, "reg": region})
            row = r.first()
            return str(row[0]) if row else None
        except Exception as e:
            _log.warning("delegataire_actif: erreur DB (delegation inactive): %s", e)
            return None  # Pas de délégation active détectée
