import React, { Suspense, lazy } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './hooks/useAuth'

// ── Pages publiques ───────────────────────────────────────────────
const LandingPage    = lazy(() => import('./pages/LandingPage'))
const LoginPage      = lazy(() => import('./pages/LoginPage'))
const Dashboard      = lazy(() => import('./pages/Dashboard'))

// ── Module CCFM (7 sous-pages par acteur) ─────────────────────────
const CCFMLayout     = lazy(() => import('./pages/ccfm/CCFMLayout'))
const CCFMDashboard  = lazy(() => import('./pages/ccfm/CCFMDashboard'))
const CCFMDemandes   = lazy(() => import('./pages/ccfm/CCFMDemandesPage'))
const CCFMDocuments  = lazy(() => import('./pages/ccfm/CCFMDocumentsPage'))
const CCFMSuivi      = lazy(() => import('./pages/ccfm/CCFMSuiviPage'))
const CCFMFiche      = lazy(() => import('./pages/ccfm/CCFMFicheConstatTopographe'))
const CCFMSubPages   = lazy(() => import('./pages/ccfm/CCFMSubPages'))

// ── Pages Admin / Intelligence ────────────────────────────────────
const MonitoringPage     = lazy(() => import('./pages/admin/MonitoringPage'))
const RiskPage           = lazy(() => import('./pages/admin/RiskPage'))
const DashboardNational  = lazy(() => import('./pages/admin/DashboardNationalPage'))
const SimulationPage     = lazy(() => import('./pages/admin/SimulationPage'))
const SqlSandboxPage     = lazy(() => import('./pages/admin/SqlSandboxPage'))

// ── Portail public CCFM ───────────────────────────────────────────
const VerifyPage = lazy(() => Promise.resolve({
  default: function VerifyPage() {
    return (
      <div style={{
        minHeight: '100vh', background: '#3C4F33',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: 'Georgia, serif',
      }}>
        <div style={{
          background: '#F5EFE0', borderRadius: 16,
          padding: '48px', maxWidth: '480px', width: '90%',
        }}>
          <div style={{ textAlign: 'center', marginBottom: 24 }}>
            <div style={{ fontSize: 40, marginBottom: 12 }}>🔍</div>
            <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>
              Vérification CCFM
            </h1>
            <p style={{ color: '#64748b', fontSize: 13, marginTop: 8 }}>
              Portail public — aucune connexion requise
            </p>
          </div>
          <input
            placeholder="NUS-2026-00089"
            style={{
              width: '100%', padding: 12, border: '1px solid #d1d5db',
              borderRadius: 8, fontSize: 14, fontFamily: 'monospace',
              marginBottom: 12, boxSizing: 'border-box' as const,
            }}
          />
          <button style={{
            width: '100%', background: '#3C4F33', color: 'white',
            border: 'none', padding: 12, borderRadius: 8,
            fontSize: 14, fontWeight: 600, cursor: 'pointer',
          }}>
            Vérifier le certificat
          </button>
        </div>
      </div>
    )
  }
}))

// ── Loading spinner ───────────────────────────────────────────────
function PageLoader() {
  return (
    <div style={{
      minHeight: '100vh', background: '#3C4F33',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      flexDirection: 'column', gap: 16,
    }}>
      <div style={{ fontSize: 28, fontWeight: 700, color: 'white', fontFamily: 'Georgia' }}>
        FONCIER<span style={{ color: '#B8220E' }}>+</span>
      </div>
      <div style={{
        width: 36, height: 36,
        border: '3px solid rgba(255,255,255,.25)',
        borderTopColor: 'white', borderRadius: '50%',
        animation: 'spin .7s linear infinite',
      }} />
    </div>
  )
}

// ── Guard routes protégées ────────────────────────────────────────
function PrivateRoute({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

// ── Application ───────────────────────────────────────────────────
export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Suspense fallback={<PageLoader />}>
          <Routes>
            {/* ── Publiques ── */}
            <Route path="/"     element={<LandingPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/verify"            element={<VerifyPage />} />
            <Route path="/verify/:nus"       element={<VerifyPage />} />
            <Route path="/ccfm/verification-publique" element={<VerifyPage />} />

            {/* ── Dashboard principal ── */}
            <Route path="/dashboard"   element={<PrivateRoute><Dashboard /></PrivateRoute>} />
            <Route path="/dashboard/*" element={<PrivateRoute><Dashboard /></PrivateRoute>} />

            {/* ── Module CCFM — sous-routes imbriquées ── */}
            <Route path="/dashboard/ccfm" element={<PrivateRoute><CCFMLayout /></PrivateRoute>}>
              <Route index                element={<CCFMDashboard />} />
              <Route path="demandes"      element={<CCFMDemandes />} />
              <Route path="documents"     element={<CCFMDocuments />} />
              <Route path="suivi"         element={<CCFMSuivi />} />
              <Route path="fiche-constat" element={<CCFMFiche />} />
              <Route path=":workspace"    element={<CCFMSubPages />} />
            </Route>

            {/* ── Admin / Intelligence nationale ── */}
            <Route path="/dashboard/monitoring"  element={<PrivateRoute><MonitoringPage /></PrivateRoute>} />
            <Route path="/dashboard/risk"        element={<PrivateRoute><RiskPage /></PrivateRoute>} />
            <Route path="/dashboard/national"    element={<PrivateRoute><DashboardNational /></PrivateRoute>} />
            <Route path="/dashboard/simulation"  element={<PrivateRoute><SimulationPage /></PrivateRoute>} />
            <Route path="/dashboard/sandbox"     element={<PrivateRoute><SqlSandboxPage /></PrivateRoute>} />

            {/* ── Fallback ── */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </AuthProvider>
  )
}
