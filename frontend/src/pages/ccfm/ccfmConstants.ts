/**
 * CCFM — Constantes partagées (source unique de vérité)
 *
 * Workflow officiel — 7 étapes :
 *   1. GUICHETIÈRE    → Enregistrement demande + paiement 50 000 FCFA → NUS
 *   2. SYSTÈME AUTO   → Vérification RNAF + BGU → Ticket de réponse (GPS + résultats) → Secrétaire
 *   3. SECRÉTAIRE     → Consulte le ticket → Émet ordre de mission (ticket joint au topographe)
 *   4. TOPOGRAPHE     → Fiche de Vérification Administrative Foncière (formulaire officiel)
 *   5. SYSTÈME AUTO   → Compile ticket + fiche → Rapport d'appréciation pour Directeur
 *   6. DIRECTEUR      → Consulte rapport → Signe → Télécharge certificat PDF
 *   7. ARCHIVISTE     → Archive ANNF + ancrage blockchain
 */

// ─── États du workflow ──────────────────────────────────────────────────────
export const ETAT_CONF: Record<string, { label: string; cls: string; acteur: string }> = {
  DEMANDE_RECUE:               { label: 'Demande enregistrée',         cls: 'bg-gray-100 text-gray-700',       acteur: 'Guichetière' },
  VERIFICATION_AUTO:           { label: 'Vérif. RNAF+BGU en cours…',   cls: 'bg-blue-100 text-blue-700',       acteur: 'Système' },
  TICKET_EMIS:                 { label: 'Ticket émis ✓',               cls: 'bg-cyan-100 text-cyan-700',       acteur: 'Système → Secrétaire' },
  VERIFICATION_AUTO_KO:        { label: 'Vérif. KO — Rejet',           cls: 'bg-red-100 text-red-700',         acteur: 'Système' },
  ORDRE_MISSION_EMIS:          { label: 'Ordre de mission émis',       cls: 'bg-amber-100 text-amber-700',     acteur: 'Secrétaire' },
  ATTENTE_VISITE_TERRAIN:      { label: 'Attente visite terrain',      cls: 'bg-yellow-100 text-yellow-700',   acteur: 'Topographe' },
  VISITE_EN_COURS:             { label: 'Visite terrain en cours',     cls: 'bg-orange-100 text-orange-700',   acteur: 'Topographe' },
  FICHE_CONSTAT_DEPOSEE:       { label: 'Fiche de constat déposée',    cls: 'bg-indigo-100 text-indigo-700',   acteur: 'Topographe' },
  RAPPORT_APPRECIATION:        { label: 'Rapport d\'appréciation généré', cls: 'bg-purple-100 text-purple-700', acteur: 'Système' },
  ATTENTE_SIGNATURE_DIRECTEUR: { label: 'Attente signature Directeur', cls: 'bg-violet-100 text-violet-700',   acteur: 'Directeur' },
  SIGNE_DIRECTEUR:             { label: 'Signé — PDF prêt',            cls: 'bg-emerald-100 text-emerald-700', acteur: 'Directeur' },
  ARCHIVE:                     { label: 'Archivé ANNF',                cls: 'bg-teal-100 text-teal-700',       acteur: 'Archiviste' },
  RETRAIT_EN_ATTENTE:          { label: 'Retrait en attente',          cls: 'bg-sky-100 text-sky-700',         acteur: 'Archiviste' },
  RETIRE:                      { label: 'Retiré',                      cls: 'bg-green-100 text-green-700',     acteur: 'Terminé' },
  REJETE:                      { label: 'Rejeté',                      cls: 'bg-red-200 text-red-800',         acteur: '—' },
};

// ─── Étapes du workflow (dashboard, workflow page, stepper) ─────────────────
export const WORKFLOW_STEPS = [
  {
    n: 1, acteur: 'GUICHETIÈRE', icon: '🪟',
    color: 'bg-gray-500', gradient: 'from-gray-500 to-gray-600',
    titre: 'Réception & Enregistrement',
    details: [
      'Formulaire CCFM officiel complet',
      'Paiement forfaitaire 50 000 FCFA (Airtel Money, Moov Money, Banque, Trésor Public)',
      'Génération automatique du NUS (Numéro Unique de Suivi)',
      'QR Code de traçabilité généré à l\'enregistrement',
    ],
    etats: ['DEMANDE_RECUE'], duree: 'J+0',
  },
  {
    n: 2, acteur: 'SYSTÈME AUTOMATIQUE', icon: '🔍',
    color: 'bg-blue-500', gradient: 'from-blue-500 to-blue-600',
    titre: 'Vérification RNAF + BGU → Ticket de Réponse',
    details: [
      'Interrogation RNAF — Registre National des Arrêtés Fonciers',
      'Interrogation BGU v4.0.0 — Base Géospatiale Unique',
      'Extraction des coordonnées GPS de la parcelle depuis la BGU',
      'Génération du Ticket de Réponse : résultats RNAF/BGU + GPS parcelle',
      'Ticket transmis automatiquement à la secrétaire',
      'Résultat : Ticket émis (OK) ou Rejet automatique (KO)',
    ],
    etats: ['VERIFICATION_AUTO', 'TICKET_EMIS', 'VERIFICATION_AUTO_KO'], duree: '< 1h',
  },
  {
    n: 3, acteur: 'SECRÉTAIRE', icon: '📤',
    color: 'bg-amber-500', gradient: 'from-amber-500 to-amber-600',
    titre: 'Consultation Ticket → Ordre de Mission',
    details: [
      'Réception et consultation du Ticket (RNAF, BGU, coordonnées GPS parcelle)',
      'Vérification de la conformité avant toute émission',
      'Émission de l\'ordre de mission au Topographe (obligatoire)',
      'Le Ticket est joint automatiquement à l\'ordre de mission',
      'Numérotation ordre de mission : OM-AAAA-NNNNN',
    ],
    etats: ['ORDRE_MISSION_EMIS', 'ATTENTE_VISITE_TERRAIN'], duree: 'J+1',
  },
  {
    n: 4, acteur: 'TOPOGRAPHE', icon: '📐',
    color: 'bg-indigo-500', gradient: 'from-indigo-500 to-indigo-600',
    titre: 'Fiche de Vérification Administrative Foncière',
    details: [
      'Réception de l\'ordre de mission avec le Ticket GPS joint',
      'Consultation des coordonnées GPS du ticket avant intervention terrain',
      '§2 — Vérification arrêté de lotissement (numéro, date, publication JO)',
      '§3 — Vérification assiette foncière : GPS mesuré vs GPS du ticket',
      '§4 — Vérification titre foncier (authenticité aux archives domaniales)',
      '§7 — Décision : Autorisation de poursuivre / Refus motivé',
      'Photos géolocalisées du terrain',
    ],
    etats: ['VISITE_EN_COURS', 'FICHE_CONSTAT_DEPOSEE'], duree: '2–4 j',
  },
  {
    n: 5, acteur: 'SYSTÈME AUTOMATIQUE', icon: '📊',
    color: 'bg-purple-500', gradient: 'from-purple-500 to-purple-600',
    titre: 'Rapport d\'Appréciation',
    details: [
      'Compilation automatique : Ticket RNAF/BGU/GPS + Fiche de constat',
      'Analyse des écarts de superficie (déclarée vs mesurée)',
      'Contrôle concordance GPS ticket ↔ GPS mesuré sur terrain',
      'Vérification publication arrêté au Journal Officiel',
      'Verdict global : CONFORME / À VÉRIFIER / NON CONFORME',
      'Rapport complet transmis au Directeur',
    ],
    etats: ['RAPPORT_APPRECIATION', 'ATTENTE_SIGNATURE_DIRECTEUR'], duree: '< 1h',
  },
  {
    n: 6, acteur: 'DIRECTEUR', icon: '✅',
    color: 'bg-emerald-500', gradient: 'from-emerald-500 to-emerald-600',
    titre: 'Signature & Certificat PDF',
    details: [
      'Consultation du rapport d\'appréciation (ticket + fiche de constat)',
      'Examen synthétique : RNAF, BGU, GPS, superficie, titre foncier',
      'Décision : Favorable ou Défavorable',
      'Signature électronique (code PIN)',
      'Téléchargement du certificat PDF officiel avec QR Code',
    ],
    etats: ['SIGNE_DIRECTEUR'], duree: '1–2 j',
  },
  {
    n: 7, acteur: 'ARCHIVISTE', icon: '🏛️',
    color: 'bg-teal-500', gradient: 'from-teal-500 to-teal-600',
    titre: 'Archivage ANNF & Retrait',
    details: [
      'Enregistrement ANNF (Archives Nationales du Niger Foncier)',
      'Ancrage blockchain SHA-256',
      'Notification du bénéficiaire',
      'Retrait du certificat physique',
    ],
    etats: ['ARCHIVE', 'RETRAIT_EN_ATTENTE', 'RETIRE'], duree: 'J+1',
  },
];

// ─── Labels d'états détaillés (suivi/timeline) ──────────────────────────────
export const ETAT_LABELS: Record<string, string> = {
  DEMANDE_RECUE:               'Demande enregistrée au guichet — NUS généré',
  VERIFICATION_AUTO:           'Vérification RNAF + BGU en cours…',
  TICKET_EMIS:                 'Ticket de réponse émis (GPS + RNAF/BGU) — transmis à la secrétaire ✓',
  VERIFICATION_AUTO_KO:        'Vérification non conforme — dossier rejeté automatiquement',
  ORDRE_MISSION_EMIS:          'Ordre de mission émis au topographe (ticket GPS joint)',
  ATTENTE_VISITE_TERRAIN:      'En attente de visite terrain',
  VISITE_EN_COURS:             'Visite terrain en cours — fiche de constat en remplissage',
  FICHE_CONSTAT_DEPOSEE:       'Fiche de constat déposée par le topographe',
  RAPPORT_APPRECIATION:        'Rapport d\'appréciation généré par le système',
  ATTENTE_SIGNATURE_DIRECTEUR: 'En attente de signature du Directeur',
  SIGNE_DIRECTEUR:             'Signé par le Directeur — Certificat PDF disponible',
  ARCHIVE:                     'Archivé ANNF + ancrage blockchain',
  RETRAIT_EN_ATTENTE:          'En attente de retrait par le bénéficiaire',
  RETIRE:                      'Certificat retiré — procédure terminée',
  REJETE:                      'Dossier rejeté',
};

// ─── Transitions workflow par état ──────────────────────────────────────────
export const TRANSITIONS: Record<string, { label: string; action: string; color: string; icon: string }[]> = {
  DEMANDE_RECUE: [
    { label: 'Lancer vérification RNAF + BGU', action: 'TICKET_EMIS', color: 'bg-blue-600 hover:bg-blue-700', icon: '🔍' },
  ],
  TICKET_EMIS: [
    { label: 'Émettre ordre de mission', action: 'ORDRE_MISSION_EMIS', color: 'bg-amber-600 hover:bg-amber-700', icon: '📤' },
  ],
  ORDRE_MISSION_EMIS: [
    { label: 'Démarrer visite terrain', action: 'VISITE_EN_COURS', color: 'bg-orange-600 hover:bg-orange-700', icon: '📐' },
  ],
  VISITE_EN_COURS: [
    { label: 'Déposer fiche de constat', action: 'FICHE_CONSTAT_DEPOSEE', color: 'bg-indigo-600 hover:bg-indigo-700', icon: '📋' },
  ],
  // B17 — FICHE_CONSTAT_DEPOSEE → rapport généré auto dans deposerFicheConstat (plus besoin de transition manuelle)
  // Mais on garde le bouton pour les dossiers déjà à cet état
  FICHE_CONSTAT_DEPOSEE: [
    { label: 'Générer rapport d\'appréciation', action: 'RAPPORT_APPRECIATION', color: 'bg-purple-600 hover:bg-purple-700', icon: '📊' },
  ],
  ATTENTE_SIGNATURE_DIRECTEUR: [
    { label: 'Approuver & Signer — Générer PDF', action: 'SIGNE_DIRECTEUR', color: 'bg-emerald-600 hover:bg-emerald-700', icon: '✅' },
    { label: 'Rejeter le dossier',               action: 'REJETE',           color: 'bg-red-600 hover:bg-red-700',         icon: '✗'  },
  ],
  // B16 — SIGNE_DIRECTEUR : workflow complet → RETRAIT_EN_ATTENTE puis ARCHIVE
  SIGNE_DIRECTEUR: [
    { label: 'Préparer retrait bénéficiaire', action: 'RETRAIT_EN_ATTENTE', color: 'bg-sky-600 hover:bg-sky-700',   icon: '📬' },
    { label: 'Archiver directement',          action: 'ARCHIVE',            color: 'bg-teal-600 hover:bg-teal-700', icon: '🏛️' },
  ],
  RETRAIT_EN_ATTENTE: [
    { label: 'Marquer retiré',    action: 'RETIRE',  color: 'bg-green-600 hover:bg-green-700', icon: '✓'  },
    { label: 'Archiver dans ANNF', action: 'ARCHIVE', color: 'bg-teal-600 hover:bg-teal-700',  icon: '🏛️' },
  ],
  ARCHIVE: [
    { label: 'Marquer retiré', action: 'RETIRE', color: 'bg-green-600 hover:bg-green-700', icon: '✓' },
  ],
};
