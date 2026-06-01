/**
 * FONCIER+ v3.5.3 — Real API Client
 * Gère l'intégration avec le backend FastAPI et sert d'adaptateur.
 * Supporte le mode hybride : utilise mockBackend.ts par défaut ou se connecte
 * à l'API Gateway en mode de production réel (VITE_API_MODE=live).
 */

import axios from 'axios';
import * as mock from './mockBackend';

const IS_LIVE = import.meta.env.VITE_API_MODE === 'live';
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

// Configuration Axios
export const http = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Intercepteur : injecter le token JWT stocké dans le localStorage
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('foncier_jwt');
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ── Auth ──────────────────────────────────────────────────────────
export async function login(email: string, password: string) {
  if (!IS_LIVE) {
    const res = mock.login(email, password);
    if (res) {
      localStorage.setItem('foncier_jwt', res.access_token);
      localStorage.setItem('foncier_user', JSON.stringify(res));
    }
    return res;
  }
  try {
    const response = await http.post('/v1/auth/login', { email, password });
    const user = response.data;
    localStorage.setItem('foncier_jwt', user.access_token);
    localStorage.setItem('foncier_user', JSON.stringify(user));
    return user;
  } catch (error) {
    console.error('Erreur authentification:', error);
    return null;
  }
}

export function logout() {
  localStorage.removeItem('foncier_jwt');
  localStorage.removeItem('foncier_user');
}

// ── KPIs Nationaux ────────────────────────────────────────────────
export async function getKpisNationaux() {
  if (!IS_LIVE) return mock.getKpisNationaux();
  try {
    const response = await http.get('/api/v1/dashboard/kpis');
    return response.data;
  } catch (error) {
    console.error('Erreur KPIs:', error);
    return mock.getKpisNationaux(); // fallback
  }
}

// ── Activité récente ──────────────────────────────────────────────
export async function getActiviteRecente() {
  if (!IS_LIVE) return mock.getActiviteRecente();
  try {
    const response = await http.get('/api/v1/monitoring/activity');
    return response.data;
  } catch (error) {
    console.error('Erreur Activité récente:', error);
    return mock.getActiviteRecente();
  }
}

// ── Stats par module ──────────────────────────────────────────────
export async function getStatsByModule() {
  if (!IS_LIVE) return mock.getStatsByModule();
  try {
    const response = await http.get('/api/v1/dashboard/module-stats');
    return response.data;
  } catch (error) {
    console.error('Erreur Stats Module:', error);
    return mock.getStatsByModule();
  }
}

// ── CCFM ──────────────────────────────────────────────────────────
export async function getDemandeCCFM() {
  if (!IS_LIVE) return mock.getDemandeCCFM();
  try {
    const response = await http.get('/v1/ccfm/demandes');
    return response.data;
  } catch (error) {
    console.error('Erreur Demandes CCFM:', error);
    return mock.getDemandeCCFM();
  }
}

// ── Parcelles ─────────────────────────────────────────────────────
export async function getParcelles(page = 1, limit = 20) {
  if (!IS_LIVE) return mock.getParcelles(page);
  try {
    const response = await http.get(`/v1/cadastre/parcelles/?page=${page}&limit=${limit}`);
    return response.data;
  } catch (error) {
    console.error('Erreur Parcelles:', error);
    return mock.getParcelles(page);
  }
}

// ── Notaire ───────────────────────────────────────────────────────
export async function getActesNotarials() {
  if (!IS_LIVE) return mock.getActesNotarials();
  try {
    const response = await http.get('/v1/notaire/actes');
    return response.data;
  } catch (error) {
    console.error('Erreur Actes Notariés:', error);
    return mock.getActesNotarials();
  }
}

// ── Workflows ─────────────────────────────────────────────────────
export async function getWorkflowsEnCours() {
  if (!IS_LIVE) return mock.getWorkflowsEnCours();
  try {
    const response = await http.get('/v1/workflows/en-cours');
    return response.data;
  } catch (error) {
    console.error('Erreur Workflows:', error);
    return mock.getWorkflowsEnCours();
  }
}

// ── Alertes antifraude ────────────────────────────────────────────
export async function getAlertesAntifraude() {
  if (!IS_LIVE) return mock.getAlertesAntifraude();
  try {
    const response = await http.get('/api/v1/monitoring/anomalies');
    return response.data;
  } catch (error) {
    console.error('Erreur Alertes Antifraude:', error);
    return mock.getAlertesAntifraude();
  }
}

// ── Vérification CCFM publique ────────────────────────────────────
export async function verifierCCFM(nus: string) {
  if (!IS_LIVE) return mock.verifierCCFM(nus);
  try {
    const response = await http.get(`/v1/verify/ccfm/${nus}`);
    return response.data;
  } catch (error) {
    console.error('Erreur Vérification CCFM:', error);
    return null;
  }
}
