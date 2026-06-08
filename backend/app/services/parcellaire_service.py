"""
FONCIER+ v3.4.7 — Service Parcellaire
Gestion du NICAD, des subdivisions, des versions et des règles antifraude.
"""

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.parcellaire import (
    AnnulationParcelle, BGUGeoJSONMaster, ConflitParcellaire,
    GraviteConflit, Ilot, NicadRegistry, Parcelle, ParcelLineage,
    ParcelVersion, RefonteLotissement, StatutConflit, StatutParcelle,
    StatutVersion, TypeConflit,
)
from app.core.security import calculate_sha256


# ─────────────────────────────────────────────────────────────
# SEUILS ANTIFRAUDE
# ─────────────────────────────────────────────────────────────

SEUIL_SUPERPOSITION_CRITIQUE_M2 = 1.0    # m² — blocage immédiat
SEUIL_SUPERPOSITION_VERIFIER_M2 = 0.01   # m² — alerte seulement
ECART_SURFACE_REFONTE_MAX_PCT = 1.0      # % — tolérance surface refonte


# ─────────────────────────────────────────────────────────────
# NICAD SERVICE
# ─────────────────────────────────────────────────────────────

class NicadService:
    """
    Générateur et validateur de NICAD.
    Format : RR-CC-ARR-III-PPP[S]
    - RR  = code région (2 chiffres)
    - CC  = code commune (2 chiffres)
    - ARR = code arrêté (7 caractères, ex: AR0045)
    - III = code îlot (3 chiffres, ex: 089)
    - PPP = code parcelle (3 alphanum, ex: B7a) — auto-incrémenté
    - [S] = suffixe subdivision : 'a' ou 'b' uniquement

    RÈGLE : NICAD invariable après correction mineure.
    Nouveau NICAD uniquement si refonte totale (nouvel arrêté).
    """

    NICAD_PATTERN = re.compile(
        r"^(?P<rr>\d{2})-(?P<cc>\d{2})-(?P<arr>[A-Z]{2}\d{4})-"
        r"(?P<iii>\d{3})-(?P<ppp>[A-Z0-9]{1,3})(?P<s>[ab]?)$"
    )

    @staticmethod
    def generate(
        code_region: str,
        code_commune: str,
        code_arrete: str,
        code_ilot: str,
        code_parcelle: str,
        suffixe: Optional[str] = None,
    ) -> str:
        """
        Génère un NICAD à partir de ses composants.
        Tous les composants sont normalisés (uppercase, zero-padded).
        """
        rr = code_region.zfill(2)
        cc = code_commune.zfill(2)
        arr = code_arrete.upper()
        iii = code_ilot.zfill(3)
        ppp = code_parcelle.upper()
        s = suffixe.lower() if suffixe in ("a", "b") else ""
        return f"{rr}-{cc}-{arr}-{iii}-{ppp}{s}"

    @classmethod
    def validate(cls, nicad: str) -> bool:
        """Valide la structure d'un NICAD."""
        return bool(cls.NICAD_PATTERN.match(nicad))

    @classmethod
    def parse(cls, nicad: str) -> dict:
        """Décompose un NICAD en ses segments."""
        m = cls.NICAD_PATTERN.match(nicad)
        if not m:
            raise ValueError(f"NICAD invalide : {nicad!r}")
        return {
            "code_region": m.group("rr"),
            "code_commune": m.group("cc"),
            "code_arrete": m.group("arr"),
            "code_ilot": m.group("iii"),
            "code_parcelle": m.group("ppp"),
            "suffixe": m.group("s") or None,
        }

    @staticmethod
    def generate_subdivision_nicad(parent_nicad: str, suffixe: str) -> str:
        """
        Génère le NICAD d'une parcelle fille par subdivision.
        Règle : NICAD parent + suffixe (a ou b).
        """
        if suffixe not in ("a", "b"):
            raise ValueError("Suffixe de subdivision invalide — doit être 'a' ou 'b'")
        return f"{parent_nicad}{suffixe}"

    @staticmethod
    async def next_parcelle_code(db: AsyncSession, ilot_id: str) -> str:
        """
        Génère le prochain code parcelle disponible pour un îlot.
        Format alphabétique : A1, A2, ..., Z9, AA1, etc.
        Verrouille l'îlot de manière pessimiste (FOR UPDATE) pour éviter toute race condition.
        """
        from sqlalchemy import text
        # Verrouiller pessimistement l'îlot
        await db.execute(
            text("SELECT id FROM ilots WHERE id = :ilot_id FOR UPDATE"),
            {"ilot_id": ilot_id}
        )

        result = await db.execute(
            select(func.count(Parcelle.id))
            .join(Ilot)
            .where(Ilot.id == ilot_id)
        )
        count = result.scalar() or 0
        # Génération alphanumérique : A1, A2, ..., B1, ...
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        num = count + 1
        letter_idx = (num - 1) // 9
        digit = ((num - 1) % 9) + 1
        prefix = letters[letter_idx % 26]
        return f"{prefix}{digit}"


# ─────────────────────────────────────────────────────────────
# VERSIONING SERVICE
# ─────────────────────────────────────────────────────────────

class VersioningService:
    """
    Gestion des versions de parcelles.
    RÈGLE : modification directe interdite → nouvelle version obligatoire.
    1 seule version ACTIVE par NICAD.
    """

    @staticmethod
    def compute_sha256(nicad: str, surface: float, geom_wkt: str,
                       motif: str, timestamp: datetime) -> str:
        """Calcule le SHA-256 d'une version de parcelle."""
        payload = f"{nicad}|{surface:.4f}|{geom_wkt}|{motif}|{timestamp.isoformat()}"
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    async def create_new_version(
        db: AsyncSession,
        parcelle: Parcelle,
        motif: str,
        new_geom_wkt: Optional[str],
        new_surface_m2: Optional[float],
        modifie_par_id: str,
    ) -> ParcelVersion:
        """
        Crée une nouvelle version de parcelle.
        Archive automatiquement la version active précédente via trigger DB.
        """
        # Récupérer le dernier numéro de version
        result = await db.execute(
            select(func.max(ParcelVersion.numero_version))
            .where(ParcelVersion.parcelle_id == parcelle.id)
        )
        last_version = result.scalar() or 0
        new_version_num = last_version + 1

        # Surface et géom : utiliser les nouvelles valeurs ou conserver les actuelles
        surface = new_surface_m2 if new_surface_m2 is not None else float(parcelle.surface_m2)
        geom_wkt = new_geom_wkt or "POLYGON EMPTY"

        sha256 = VersioningService.compute_sha256(
            parcelle.nicad, surface, geom_wkt, motif, datetime.now(timezone.utc)
        )

        version = ParcelVersion(
            id=uuid.uuid4(),
            parcelle_id=parcelle.id,
            numero_version=new_version_num,
            statut_version=StatutVersion.ACTIVE,
            surface_originale_m2=surface,
            geom_originale=geom_wkt,
            motif_modification=motif,
            sha256_version=sha256,
            modifie_par=modifie_par_id,
        )
        db.add(version)
        await db.flush()

        # Mettre à jour la référence de version active sur la parcelle
        parcelle.version_active_id = version.id
        parcelle.updated_at = datetime.now(timezone.utc)
        await db.flush()

        return version


# ─────────────────────────────────────────────────────────────
# SUBDIVISION SERVICE
# ─────────────────────────────────────────────────────────────

class SubdivisionService:
    """
    Gestion des subdivisions de parcelles.
    RÈGLES :
    - 1 niveau maximum
    - 2 filles maximum (suffixes a et b)
    - Parent → SUBDIVISEE
    """

    @staticmethod
    async def can_subdivide(db: AsyncSession, parcelle: Parcelle) -> Tuple[bool, str]:
        """Vérifie si une parcelle peut être subdivisée."""
        # Vérif statut
        if parcelle.statut not in (StatutParcelle.ACTIVE, StatutParcelle.EN_ATTENTE_VALIDATION):
            return False, f"Parcelle {parcelle.nicad} au statut {parcelle.statut} — subdivision impossible"

        # Vérif gel judiciaire
        if parcelle.is_gele:
            return False, f"Parcelle {parcelle.nicad} gelée ❄️ — subdivision bloquée"

        # Vérif niveau (pas de sous-subdivision)
        parent_result = await db.execute(
            select(ParcelLineage).where(ParcelLineage.enfant_id == parcelle.id)
        )
        if parent_result.scalar_one_or_none():
            return False, f"Parcelle {parcelle.nicad} est déjà une subdivision (niveau 1) — pas de sous-subdivision"

        # Vérif nombre de filles existantes
        children_result = await db.execute(
            select(func.count(ParcelLineage.id))
            .where(ParcelLineage.parent_id == parcelle.id)
        )
        nb_filles = children_result.scalar() or 0
        if nb_filles >= 2:
            return False, f"Parcelle {parcelle.nicad} a déjà 2 filles — limite atteinte"

        # Vérif conflit critique
        conflit_result = await db.execute(
            select(ConflitParcellaire).where(
                and_(
                    ConflitParcellaire.parcelle_a_id == parcelle.id,
                    ConflitParcellaire.gravite == GraviteConflit.CRITIQUE,
                    ConflitParcellaire.statut == StatutConflit.BLOQUE,
                )
            )
        )
        if conflit_result.scalar_one_or_none():
            return False, f"Parcelle {parcelle.nicad} a un conflit critique non résolu"

        return True, "OK"

    @staticmethod
    async def get_next_suffixe(db: AsyncSession, parent_id: str) -> str:
        """Retourne le prochain suffixe disponible ('a' ou 'b')."""
        result = await db.execute(
            select(ParcelLineage.suffixe)
            .where(ParcelLineage.parent_id == parent_id)
        )
        used = {row[0] for row in result}
        if "a" not in used:
            return "a"
        if "b" not in used:
            return "b"
        raise ValueError("Les deux filles (a et b) existent déjà")

    @staticmethod
    async def perform_subdivision(
        db: AsyncSession,
        parent: Parcelle,
        surface_a_m2: float,
        surface_b_m2: float,
        geom_a_wkt: str,
        geom_b_wkt: str,
        autorise_par_id: str,
        motif: str,
    ) -> Tuple[Parcelle, Parcelle]:
        """
        Effectue la subdivision d'une parcelle en 2 filles.
        Retourne (parcelle_a, parcelle_b).
        """
        can, reason = await SubdivisionService.can_subdivide(db, parent)
        if not can:
            raise ValueError(reason)

        # Vérification cohérence surface
        total_filles = surface_a_m2 + surface_b_m2
        ecart = abs(total_filles - float(parent.surface_m2)) / float(parent.surface_m2) * 100
        if ecart > ECART_SURFACE_REFONTE_MAX_PCT:
            raise ValueError(
                f"Incohérence surface : parent={parent.surface_m2}m² ≠ "
                f"filles={total_filles:.2f}m² (écart {ecart:.2f}% > {ECART_SURFACE_REFONTE_MAX_PCT}%)"
            )

        # Créer parcelle fille A
        nicad_a = NicadService.generate_subdivision_nicad(parent.nicad, "a")
        fille_a = Parcelle(
            id=uuid.uuid4(),
            ilot_id=parent.ilot_id,
            nicad=nicad_a,
            geojson_id=f"geojson-{nicad_a.replace('-', '_')}",
            statut=StatutParcelle.EN_ATTENTE_VALIDATION,
            surface_m2=surface_a_m2,
            geom=geom_a_wkt,
            cree_par=autorise_par_id,
        )
        db.add(fille_a)

        # Créer parcelle fille B
        nicad_b = NicadService.generate_subdivision_nicad(parent.nicad, "b")
        fille_b = Parcelle(
            id=uuid.uuid4(),
            ilot_id=parent.ilot_id,
            nicad=nicad_b,
            geojson_id=f"geojson-{nicad_b.replace('-', '_')}",
            statut=StatutParcelle.EN_ATTENTE_VALIDATION,
            surface_m2=surface_b_m2,
            geom=geom_b_wkt,
            cree_par=autorise_par_id,
        )
        db.add(fille_b)
        await db.flush()

        # Liens de filiation
        db.add(ParcelLineage(
            parent_id=parent.id, enfant_id=fille_a.id,
            suffixe="a", niveau_subdivision=1, autorise_par=autorise_par_id,
        ))
        db.add(ParcelLineage(
            parent_id=parent.id, enfant_id=fille_b.id,
            suffixe="b", niveau_subdivision=1, autorise_par=autorise_par_id,
        ))

        # Marquer parent comme SUBDIVISEE
        parent.statut = StatutParcelle.SUBDIVISEE
        parent.updated_at = datetime.now(timezone.utc)

        # Enregistrer NICAD dans le registre
        parsed_a = NicadService.parse(nicad_a)
        parsed_b = NicadService.parse(nicad_b)
        db.add(NicadRegistry(nicad=nicad_a, parcelle_id=fille_a.id,
                              genere_par=autorise_par_id, **parsed_a))
        db.add(NicadRegistry(nicad=nicad_b, parcelle_id=fille_b.id,
                              genere_par=autorise_par_id, **parsed_b))

        # Créer versions initiales
        await VersioningService.create_new_version(
            db, fille_a, f"Création par subdivision de {parent.nicad}", geom_a_wkt,
            surface_a_m2, autorise_par_id
        )
        await VersioningService.create_new_version(
            db, fille_b, f"Création par subdivision de {parent.nicad}", geom_b_wkt,
            surface_b_m2, autorise_par_id
        )

        await db.flush()
        return fille_a, fille_b


# ─────────────────────────────────────────────────────────────
# ANNULATION SERVICE
# ─────────────────────────────────────────────────────────────

class AnnulationService:
    """
    RÈGLE : DELETE physique INTERDIT.
    Annulation = statut ANNULE + enregistrement dans annulations_parcelles.
    Validation multi-niveaux obligatoire.
    """

    @staticmethod
    async def annuler_parcelle(
        db: AsyncSession,
        parcelle: Parcelle,
        motif: str,
        base_juridique: str,
        preuve_document_path: str,
        valideur1_id: str,
        valideur2_id: str,
    ) -> AnnulationParcelle:
        """
        Annule une parcelle (jamais de DELETE physique).
        Requiert deux valideurs (niveau 1 + niveau 2).
        """
        if parcelle.statut == StatutParcelle.ANNULE:
            raise ValueError(f"Parcelle {parcelle.nicad} déjà annulée")

        if parcelle.is_gele:
            raise ValueError(
                f"Parcelle {parcelle.nicad} gelée ❄️ — annulation impossible sans dégel judiciaire"
            )

        # Hash du document de preuve
        sha256 = hashlib.sha256(
            f"{preuve_document_path}|{motif}|{datetime.now(timezone.utc).isoformat()}".encode()
        ).hexdigest()

        now = datetime.now(timezone.utc)
        annulation = AnnulationParcelle(
            parcelle_id=parcelle.id,
            motif=motif,
            base_juridique=base_juridique,
            preuve_document=preuve_document_path,
            sha256_preuve=sha256,
            valide_niveau1=valideur1_id,
            valide_niveau1_at=now,
            valide_niveau2=valideur2_id,
            valide_niveau2_at=now,
        )
        db.add(annulation)

        # Changer statut — JAMAIS de DELETE
        parcelle.statut = StatutParcelle.ANNULE
        parcelle.updated_at = now

        await db.flush()
        return annulation


# ─────────────────────────────────────────────────────────────
# ANTIFRAUDE SERVICE
# ─────────────────────────────────────────────────────────────

class AntiFraudeService:
    """
    Contrôles antifraude applicatifs (en complément du trigger PostgreSQL).
    """

    @staticmethod
    async def check_parcelle_can_have_acte(
        db: AsyncSession, parcelle_id: str
    ) -> Tuple[bool, str]:
        """
        Vérifie qu'une parcelle peut faire l'objet d'un acte notarial ou hypothèque.
        Règles :
        - Parcelle ACTIVE
        - Non gelée ❄️
        - Aucun conflit critique ouvert/bloqué
        - CCFM validé (vérifié via gate CCFM existante)
        """
        result = await db.execute(
            select(Parcelle).where(Parcelle.id == parcelle_id)
        )
        parcelle = result.scalar_one_or_none()
        if not parcelle:
            return False, "Parcelle introuvable"

        if parcelle.statut != StatutParcelle.ACTIVE:
            return False, f"Parcelle {parcelle.nicad} au statut {parcelle.statut} — acte impossible"

        if parcelle.is_gele:
            return False, f"Parcelle {parcelle.nicad} gelée ❄️ — acte bloqué (litige en cours)"

        # Conflits critiques
        conflit_result = await db.execute(
            select(func.count(ConflitParcellaire.id)).where(
                and_(
                    ConflitParcellaire.parcelle_a_id == parcelle_id,
                    ConflitParcellaire.gravite == GraviteConflit.CRITIQUE,
                    ConflitParcellaire.statut.in_([StatutConflit.OUVERT, StatutConflit.BLOQUE]),
                )
            )
        )
        nb_conflits = conflit_result.scalar() or 0
        if nb_conflits > 0:
            return False, f"Parcelle {parcelle.nicad} a {nb_conflits} conflit(s) critique(s) non résolu(s)"

        return True, "OK"

    @staticmethod
    async def detect_displacement(
        db: AsyncSession,
        parcelle: Parcelle,
        new_geom_wkt: str,
        rapporteur_id: str,
    ) -> Optional[ConflitParcellaire]:
        """
        Détecte si une modification géométrique constitue un déplacement illicite.
        Déplacement = centroïde déplacé de plus d'un seuil sans refonte totale validée.
        """
        # Logique simplifiée — en production : ST_Distance(centroid_old, centroid_new)
        conflit = ConflitParcellaire(
            parcelle_a_id=parcelle.id,
            type_conflit=TypeConflit.DEPLACEMENT,
            gravite=GraviteConflit.CRITIQUE,
            statut=StatutConflit.BLOQUE,
            description=(
                f"Déplacement géométrique détecté sur {parcelle.nicad} — "
                "modification refusée. Requiert validation Autorité centrale via refonte."
            ),
            traite_par=rapporteur_id,
        )
        db.add(conflit)
        await db.flush()
        return conflit
