/**
 * FONCIER+ v3.4.7 — Mock Backend
 * Simule l'API FastAPI backend pour le développement frontend
 * Remplacé par apiClient.ts en production (backend réel)
 */

// ── Auth ──────────────────────────────────────────────────────────

const USERS: Record<string, { id: string; name: string; role: string; email: string; region: string }> = {
  'admin@foncier.gov.ne':        { id: 'u1', name: 'Amadou Diallo',     role: 'ADMIN',               email: 'admin@foncier.gov.ne',        region: 'NATIONAL' },
  'ccfm@foncier.gov.ne':         { id: 'u2', name: 'Mariama Kouyaté',   role: 'CHEF_CCFM',            email: 'ccfm@foncier.gov.ne',         region: 'NIA' },
  'urbaniste@foncier.gov.ne':    { id: 'u3', name: 'Issoufou Maïga',    role: 'DIRECTEUR_URBANISME',  email: 'urbaniste@foncier.gov.ne',    region: 'NATIONAL' },
  'cadastre@foncier.gov.ne':     { id: 'u4', name: 'Fatouma Moussa',    role: 'DIRECTEUR_CADASTRE',   email: 'cadastre@foncier.gov.ne',     region: 'NIA' },
  'notaire@foncier.gov.ne':      { id: 'u5', name: 'Maître Ali Soumana','role': 'NOTAIRE',            email: 'notaire@foncier.gov.ne',      region: 'NIA' },
  'juge@justice.foncier.ne':     { id: 'u6', name: 'Hadiza Oumarou',    role: 'JUGE_FONCIER',         email: 'juge@justice.foncier.ne',     region: 'NIA' },
  'directeur@banque.foncier.ne': { id: 'u7', name: 'Mounkaïla Garba',   role: 'BANQ_DIRECTEUR',       email: 'directeur@banque.foncier.ne', region: 'NIA' },
  'editeur.jo@foncier.gov.ne':   { id: 'u8', name: 'Rabi Saidou',       role: 'EDITEUR_JO',           email: 'editeur.jo@foncier.gov.ne',   region: 'NATIONAL' },
}

export function login(email: string, password: string) {
  const user = USERS[email]
  if (!user || password.length < 6) return null
  return { ...user, access_token: `mock_jwt_${user.id}_${Date.now()}` }
}

// ── KPIs Nationaux ────────────────────────────────────────────────

export function getKpisNationaux() {
  return {
    parcelles_totales:    148_372,
    ccfm_actifs:           89_441,
    rnaf_publies:          12_847,
    transactions_30j:       1_293,
    conflits_ouverts:          47,
    archives_annf:        203_918,
    migrations_wf30:           12,
    taux_conformite_pct:    94.7,
  }
}

// ── Activité récente ──────────────────────────────────────────────

export function getActiviteRecente() {
  return [
    { id: 'a1', type: 'CCFM',       action: 'Certificat émis',          ref: 'CCF-2026-0089441', region: 'Niamey',    time: '2 min', color: 'green' },
    { id: 'a2', type: 'MUTATION',   action: 'Mutation enregistrée',      ref: 'MUT-2026-0001293', region: 'Zinder',    time: '8 min', color: 'green' },
    { id: 'a3', type: 'LITIGE',     action: 'Litige ouvert',             ref: 'LIT-2026-0000047', region: 'Agadez',    time: '23 min',color: 'red'   },
    { id: 'a4', type: 'RNAF',       action: 'Arrêté publié au JO',       ref: 'ARR-2026-0012847', region: 'Tahoua',    time: '1h',    color: 'green' },
    { id: 'a5', type: 'BGU',        action: 'Géométrie scellée',         ref: 'BGU-NIA-2026-0547',region: 'Niamey',    time: '2h',    color: 'green' },
    { id: 'a6', type: 'ANNF',       action: 'Archive WORM créée',        ref: 'ANNF-2026-0203918',region: 'National',  time: '3h',    color: 'green' },
    { id: 'a7', type: 'HYPOTHEQUE', action: 'Hypothèque inscrite',       ref: 'HYP-2026-0000312', region: 'Maradi',    time: '4h',    color: 'amber' },
    { id: 'a8', type: 'WF30',       action: 'Migration étape 30.4 ✓',   ref: 'MIG-2026-0012',    region: 'Dosso',     time: '5h',    color: 'green' },
  ]
}

// ── Stats par module ──────────────────────────────────────────────

export function getStatsByModule() {
  return [
    { module: 'CCFM',          total: 89_441, ce_mois: 847,  actifs: 89_100, label: 'Certificats' },
    { module: 'RNAF',          total: 12_847, ce_mois: 234,  actifs: 12_412, label: 'Arrêtés' },
    { module: 'Cadastre',      total: 148_372,ce_mois: 1_204,actifs: 145_890,label: 'Parcelles' },
    { module: 'Notaire',       total: 34_891, ce_mois: 312,  actifs: 34_200, label: 'Actes' },
    { module: 'Banque',        total: 8_234,  ce_mois: 89,   actifs: 7_891,  label: 'Hypothèques' },
    { module: 'Justice',       total: 2_847,  ce_mois: 47,   actifs: 234,    label: 'Litiges' },
    { module: 'Domaine',       total: 15_234, ce_mois: 178,  actifs: 14_900, label: 'Dossiers' },
    { module: 'Journal Off.',  total: 3_412,  ce_mois: 56,   actifs: 3_412,  label: 'Publications' },
    { module: 'ANNF',          total: 203_918,ce_mois: 2_134,actifs: 201_700,label: 'Archives' },
  ]
}

// ── CCFM ──────────────────────────────────────────────────────────

export function getDemandeCCFM() {
  return Array.from({ length: 12 }, (_, i) => ({
    id: `ccfm-${i+1}`,
    nus: `CCF-2026-${String(89000+i+1).padStart(7,'0')}`,
    demandeur: ['Oumarou Hamidou','Aïchatou Moussa','Ibrahim Sani','Kadiatou Diallo','Soulé Amadou'][i%5],
    parcelle:  `NIA-${String(4210+i).padStart(4,'0')}`,
    statut:    ['demande','verification','constat','certificat','archive'][i%5],
    region:    ['Niamey','Maradi','Zinder','Tahoua','Dosso'][i%5],
    date:      `2026-04-${String(1+i).padStart(2,'0')}`,
    sha256:    `a${Math.random().toString(36).slice(2,10)}...`,
  }))
}

// ── Parcelles ─────────────────────────────────────────────────────

export function getParcelles(page = 1) {
  return Array.from({ length: 20 }, (_, i) => ({
    id: `p${(page-1)*20+i+1}`,
    nicad: `NIA/COM1/LOT${String(i+1).padStart(3,'0')}/SEC-${String(i+1).padStart(3,'0')}-${String(i+1).padStart(3,'0')}/${String(i+1).padStart(6,'0')}`,
    surface_m2: Math.round(200 + Math.random()*800),
    statut: ['actif','gele','litige','archive'][i%4],
    commune: ['Niamey I','Niamey II','Niamey III','Niamey IV','Niamey V'][i%5],
    has_ccfm: i%3 !== 2,
    has_bgu:  i%4 !== 3,
  }))
}

// ── Notaire ───────────────────────────────────────────────────────

export function getActesNotarials() {
  return Array.from({ length: 10 }, (_, i) => ({
    id: `acte-${i+1}`,
    ref: `NOT-2026-${String(i+1).padStart(7,'0')}`,
    type: ['vente','donation','succession','hypotheque','mainlevee'][i%5],
    parties: `${['Moussa','Aïcha','Ibrahim','Kadiatou'][i%4]} → ${['Sani','Diallo','Hamidou','Oumarou'][i%4]}`,
    statut: ['redaction','verification','signature','enregistrement','archive'][i%5],
    date: `2026-04-${String(1+i).padStart(2,'0')}`,
  }))
}

// ── Workflows ─────────────────────────────────────────────────────

export function getWorkflowsEnCours() {
  return Array.from({ length: 8 }, (_, i) => ({
    id: `wf-${i+1}`,
    type: ['RNAF','CCFM','BGU','MUTATION_VENTE','HYPOTHEQUE','SUCCESSION_FONCIERE','REGULARISATION','ARCHIVAGE_DEFINITIF'][i],
    etape_courante: ['redaction','verification','constat','depot_mutation','demande','declaration_deces','demande','detection_inactivite'][i],
    attendu_de_role: ['DIRECTEUR_URBANISME','CHEF_CCFM','DIRECTEUR_CADASTRE','NOTAIRE','BANQ_DIRECTEUR','JUGE_FONCIER','MAIRE','ARCHIVISTE_ANNF'][i],
    statut: 'EN_COURS',
    entite_ref: `REF-2026-${String(1000+i).padStart(7,'0')}`,
    depuis: `${i+1}j`,
  }))
}

// ── Alertes antifraude ────────────────────────────────────────────

export function getAlertesAntifraude() {
  return [
    { id: 'af1', type: 'SUPERPOSITION', gravite: 'CRITIQUE', parcelle: 'NIA-0012', message: 'Superposition géom > 1m² détectée', timestamp: '2026-04-17 08:23' },
    { id: 'af2', type: 'PARTS_100',     gravite: 'BLOQUANT', parcelle: 'NIA-0089', message: 'Somme parts héritiers = 112%',       timestamp: '2026-04-17 09:45' },
    { id: 'af3', type: 'HYPOTHEQUE',    gravite: 'WARN',     parcelle: 'ZDR-0234', message: 'Acte sur parcelle hypothéquée',      timestamp: '2026-04-17 11:12' },
    { id: 'af4', type: 'WORM',          gravite: 'INFO',     parcelle: 'N/A',      message: '3 archives WORM scellées aujourd\'hui',timestamp: '2026-04-17 14:00' },
  ]
}

// ── Vérification CCFM publique ────────────────────────────────────

export function verifierCCFM(nus: string) {
  if (!nus.startsWith('CCF-')) return null
  return {
    valide: true,
    nus,
    titulaire: 'Oumarou Hamidou',
    parcelle: 'NIA/COM1/LOT001/SEC-001-001/000001',
    superficie: '450 m²',
    date_emission: '2026-03-15',
    sha256: 'a8f3c2e1d4b7...', 
    etat: 'valide',
  }
}
