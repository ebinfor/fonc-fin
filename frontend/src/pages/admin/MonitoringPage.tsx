/**
 * FONCIER+ ── Monitoring Temps Réel
 * Tableau de bord de surveillance du système foncier.
 *
 * Connexion SSE : /api/v1/monitoring/stream
 * Onglets : Tableau de bord · Alertes · Événements · Historique
 */
import React, { useState, useEffect, useRef, useCallback } from "react";

// ARCHITECTURE v3.4.7 : données temps réel via useDataFetch
// Les fetch() inline sont conservés pour les actions (POST/PATCH/DELETE)
// Le chargement initial doit utiliser useDataFetch pour cohérence

// ─── Types ───────────────────────────────────────────────────────

type AlertNiveau = "INFO" | "WARNING" | "CRITICAL";
type Sante       = "OK" | "DÉGRADÉ" | "CRITIQUE";
type ConnStatus  = "connecting" | "connected" | "disconnected";

interface Alerte {
  id:           string;
  niveau:       AlertNiveau;
  detecteur:    string;
  titre:        string;
  message:      string;
  details:      Record<string, any>;
  acquittee:    boolean;
  created_at:   string;
}
interface Evenement {
  id:          string;
  categorie:   string;
  operation:   string;
  entite_type: string;
  entite_id:   string;
  details:     Record<string, any>;
  user_id:     string | null;
  region:      string | null;
  created_at:  string;
}
interface TableauBord {
  parcelles_actives:       number;
  workflows_en_cours:      number;
  workflows_bloques:       number;
  validations_en_attente:  number;
  verrous_actifs:          number;
  regles_actives:          number;
  ops_5min:                number;
  validations_5min:        number;
  echecs_5min:             number;
  alertes_critical:        number;
  alertes_warning:         number;
  alertes_info:            number;
  latence_moy_ms:          number;
  sante:                   Sante;
  calcule_a:               string;
}

// ─── Constantes ──────────────────────────────────────────────────

const NIVEAU_CFG: Record<AlertNiveau, { bg: string; color: string; border: string; icon: string }> = {
  CRITICAL: { bg: "#fee2e2", color: "#dc2626", border: "#fca5a5", icon: "🚨" },
  WARNING:  { bg: "#fef3c7", color: "#b45309", border: "#fde68a", icon: "⚠" },
  INFO:     { bg: "#eff6ff", color: "#2563eb", border: "#bfdbfe", icon: "ℹ" },
};

const SANTE_CFG: Record<Sante, { bg: string; color: string; icon: string }> = {
  "OK":       { bg: "#dcfce7", color: "#15803d", icon: "✓" },
  "DÉGRADÉ":  { bg: "#fef3c7", color: "#b45309", icon: "⚠" },
  "CRITIQUE": { bg: "#fee2e2", color: "#dc2626", icon: "✗" },
};

const CAT_COLORS: Record<string, string> = {
  PARCELLE: "#2563eb", WORKFLOW: "#7c3aed", VALIDATION: "#0891b2",
  REGLE: "#b45309", VERROU: "#dc2626", TRANSACTION: "#059669", SYSTEME: "#6b7280",
};

const MAX_EVENTS_DISPLAY = 100;

// ─── Hook SSE ────────────────────────────────────────────────────

function useMonitoringSSE() {
  const [alertes,   setAlertes]   = useState<Alerte[]>([]);
  const [evenements,setEvenements]= useState<Evenement[]>([]);
  const [connStatus,setConnStatus]= useState<ConnStatus>("connecting");
  const esRef = useRef<EventSource | null>(null);

  const connect = useCallback(() => {
    if (esRef.current) esRef.current.close();
    setConnStatus("connecting");

    const es = new EventSource("/api/v1/monitoring/stream");
    esRef.current = es;

    es.onopen = () => setConnStatus("connected");

    es.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "alerte") {
          setAlertes(prev => {
            const exists = prev.find(a => a.id === msg.payload.id);
            if (exists) return prev;
            return [msg.payload, ...prev].slice(0, 200);
          });
        } else if (msg.type === "evenement") {
          setEvenements(prev => [msg.payload, ...prev].slice(0, MAX_EVENTS_DISPLAY));
        } else if (msg.type === "alerte_acquittee") {
          setAlertes(prev => prev.map(a =>
            a.id === msg.payload.alerte_id ? { ...a, acquittee: true } : a
          ));
        }
      } catch {}
    };

    es.onerror = () => {
      setConnStatus("disconnected");
      es.close();
      // Reconnexion après 5s
      setTimeout(connect, 5000);
    };
  }, []);

  useEffect(() => {
    connect();
    return () => { esRef.current?.close(); };
  }, [connect]);

  return { alertes, evenements, connStatus, setAlertes };
}

// ─── Composants utilitaires ──────────────────────────────────────

const ConnBadge: React.FC<{ status: ConnStatus }> = ({ status }) => {
  const cfg = {
    connected:    { bg: "#dcfce7", color: "#15803d", dot: "#22c55e", label: "Connecté" },
    connecting:   { bg: "#fef3c7", color: "#b45309", dot: "#f59e0b", label: "Connexion…" },
    disconnected: { bg: "#fee2e2", color: "#dc2626", dot: "#ef4444", label: "Déconnecté" },
  }[status];
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 6,
      background: cfg.bg, color: cfg.color,
      padding: "3px 10px", borderRadius: 20, fontSize: 12, fontWeight: 600,
    }}>
      <span style={{
        width: 7, height: 7, borderRadius: "50%",
        background: cfg.dot,
        boxShadow: status === "connected" ? `0 0 0 2px ${cfg.dot}44` : "none",
        animation: status === "connected" ? "pulse 2s infinite" : "none",
      }} />
      {cfg.label}
    </span>
  );
};

const MetricCard: React.FC<{
  label: string; value: number | string;
  color?: string; icon: string; alert?: boolean;
}> = ({ label, value, color = "#1e293b", icon, alert }) => (
  <div style={{
    background: "#fff", borderRadius: 10,
    border: `1px solid ${alert ? "#fca5a5" : "#e2e8f0"}`,
    padding: "14px 16px", flex: 1, minWidth: 110,
    boxShadow: alert ? "0 0 0 2px #fee2e222" : "none",
  }}>
    <div style={{ fontSize: 11, color: "#64748b", fontWeight: 600, marginBottom: 4 }}>
      {icon} {label}
    </div>
    <div style={{ fontSize: 26, fontWeight: 800, color }}>{value}</div>
  </div>
);

// ─── Tableau de bord ──────────────────────────────────────────────

const TBDashboard: React.FC<{ alertes: Alerte[] }> = ({ alertes }) => {
  const [tb,   setTb]   = useState<TableauBord | null>(null);
  const [load, setLoad] = useState(true);

  const refresh = useCallback(() => {
    fetch("/api/v1/monitoring/tableau-bord")
      .then(r => r.json())
      .then(d => { setTb(d); setLoad(false); })
      .catch(() => setLoad(false));
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15000);
    return () => clearInterval(t);
  }, [refresh]);

  const nb_critical = alertes.filter(a => !a.acquittee && a.niveau === "CRITICAL").length;
  const nb_warning  = alertes.filter(a => !a.acquittee && a.niveau === "WARNING").length;

  if (load || !tb) return (
    <div style={{ padding: 24, textAlign: "center", color: "#94a3b8" }}>Chargement…</div>
  );

  const sc = SANTE_CFG[tb.sante as Sante] || SANTE_CFG["OK"];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Bannière santé */}
      <div style={{
        background: sc.bg, borderRadius: 12, padding: "14px 20px",
        display: "flex", alignItems: "center", gap: 12,
        border: `1px solid ${sc.color}44`,
      }}>
        <span style={{ fontSize: 24, fontWeight: 900, color: sc.color }}>{sc.icon}</span>
        <div>
          <div style={{ fontWeight: 800, fontSize: 18, color: sc.color }}>
            Santé système : {tb.sante}
          </div>
          <div style={{ fontSize: 12, color: "#64748b" }}>
            Mis à jour {new Date(tb.calcule_a).toLocaleTimeString("fr-FR")}
          </div>
        </div>
        <button onClick={refresh} style={{
          marginLeft: "auto", background: "#fff",
          border: "1px solid #e2e8f0", borderRadius: 8,
          padding: "5px 14px", fontSize: 12, cursor: "pointer", color: "#475569",
        }}>↻ Actualiser</button>
      </div>

      {/* Métriques systèmes */}
      <div>
        <div style={{ fontSize: 12, fontWeight: 700, color: "#64748b",
                      marginBottom: 8, textTransform: "uppercase" }}>
          État du système
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <MetricCard label="Parcelles actives"      value={tb.parcelles_actives}       icon="🏠" color="#2563eb" />
          <MetricCard label="Workflows en cours"     value={tb.workflows_en_cours}      icon="🔄" color="#7c3aed" />
          <MetricCard label="Workflows bloqués"      value={tb.workflows_bloques}       icon="🚫"
            color={tb.workflows_bloques > 0 ? "#dc2626" : "#15803d"}
            alert={tb.workflows_bloques > 0} />
          <MetricCard label="Validations en attente" value={tb.validations_en_attente}  icon="⏳" color="#b45309" />
          <MetricCard label="Verrous actifs"         value={tb.verrous_actifs}          icon="🔒"
            color={tb.verrous_actifs > 15 ? "#b45309" : "#6b7280"}
            alert={tb.verrous_actifs > 15} />
          <MetricCard label="Règles actives"         value={tb.regles_actives}          icon="📋" color="#059669" />
        </div>
      </div>

      {/* Activité 5 min */}
      <div>
        <div style={{ fontSize: 12, fontWeight: 700, color: "#64748b",
                      marginBottom: 8, textTransform: "uppercase" }}>
          Activité — 5 dernières minutes
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <MetricCard label="Opérations totales"   value={tb.ops_5min}          icon="⚡" color="#2563eb" />
          <MetricCard label="Validations"          value={tb.validations_5min}  icon="✓"  color="#059669" />
          <MetricCard label="Échecs / Refus"       value={tb.echecs_5min}       icon="✗"
            color={tb.echecs_5min > 5 ? "#dc2626" : "#6b7280"}
            alert={tb.echecs_5min > 5} />
          <MetricCard label="Latence moy. (ms)"   value={`${tb.latence_moy_ms}ms`} icon="⏱" color="#7c3aed" />
        </div>
      </div>

      {/* Alertes */}
      <div>
        <div style={{ fontSize: 12, fontWeight: 700, color: "#64748b",
                      marginBottom: 8, textTransform: "uppercase" }}>
          Alertes actives
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <MetricCard label="CRITICAL" value={nb_critical} icon="🚨"
            color={nb_critical > 0 ? "#dc2626" : "#6b7280"}
            alert={nb_critical > 0} />
          <MetricCard label="WARNING"  value={nb_warning}  icon="⚠"
            color={nb_warning > 0 ? "#b45309" : "#6b7280"} />
          <MetricCard label="INFO"     value={alertes.filter(a => !a.acquittee && a.niveau === "INFO").length} icon="ℹ" color="#2563eb" />
        </div>
      </div>
    </div>
  );
};

// ─── Panneau alertes ──────────────────────────────────────────────

const AlertesPanel: React.FC<{
  alertes: Alerte[];
  onAcquitter: (id: string) => void;
}> = ({ alertes, onAcquitter }) => {
  const actives = alertes.filter(a => !a.acquittee);

  if (!actives.length) return (
    <div style={{
      textAlign: "center", padding: "32px 0",
      color: "#15803d", fontSize: 14, fontWeight: 600,
    }}>
      ✓ Aucune alerte active — système nominal
    </div>
  );

  const ordre: Record<AlertNiveau, number> = { CRITICAL: 0, WARNING: 1, INFO: 2 };
  const triees = [...actives].sort((a, b) => ordre[a.niveau] - ordre[b.niveau]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {triees.map(alerte => {
        const nc = NIVEAU_CFG[alerte.niveau];
        return (
          <div key={alerte.id} style={{
            background: nc.bg, borderRadius: 10, padding: "12px 16px",
            border: `1px solid ${nc.border}`,
            borderLeft: `4px solid ${nc.color}`,
          }}>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
              <span style={{ fontSize: 18 }}>{nc.icon}</span>
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  <span style={{
                    background: nc.color + "22", color: nc.color,
                    padding: "1px 8px", borderRadius: 10,
                    fontSize: 10, fontWeight: 800,
                  }}>{alerte.niveau}</span>
                  <span style={{
                    background: "#f1f5f9", color: "#64748b",
                    padding: "1px 7px", borderRadius: 8, fontSize: 10,
                  }}>{alerte.detecteur}</span>
                  <span style={{ fontSize: 11, color: "#94a3b8", marginLeft: "auto" }}>
                    {new Date(alerte.created_at).toLocaleTimeString("fr-FR")}
                  </span>
                </div>
                <div style={{ fontWeight: 700, fontSize: 13, color: "#1e293b", marginBottom: 3 }}>
                  {alerte.titre}
                </div>
                <div style={{ fontSize: 12, color: "#475569" }}>{alerte.message}</div>
                {alerte.details && Object.keys(alerte.details).length > 0 && (
                  <div style={{ marginTop: 6, fontSize: 11, color: "#64748b" }}>
                    {Object.entries(alerte.details).slice(0, 3).map(([k, v]) => (
                      <span key={k} style={{ marginRight: 10 }}>
                        <strong>{k}</strong>: {JSON.stringify(v)}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <button
                onClick={() => onAcquitter(alerte.id)}
                style={{
                  background: "#fff", border: `1px solid ${nc.border}`,
                  borderRadius: 6, padding: "4px 10px",
                  fontSize: 11, cursor: "pointer", color: nc.color,
                  fontWeight: 600, whiteSpace: "nowrap",
                }}>
                ✓ Acquitter
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
};

// ─── Flux événements ──────────────────────────────────────────────

const EvenementsFlux: React.FC<{ evenements: Evenement[] }> = ({ evenements }) => {
  const [filtre, setFiltre] = useState<string>("TOUS");
  const categories = ["TOUS", "PARCELLE", "WORKFLOW", "VALIDATION", "REGLE", "VERROU", "TRANSACTION", "SYSTEME"];

  const filtered = filtre === "TOUS"
    ? evenements
    : evenements.filter(e => e.categorie === filtre);

  return (
    <div>
      {/* Filtres */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
        {categories.map(cat => (
          <button key={cat} onClick={() => setFiltre(cat)}
            style={{
              padding: "4px 12px", borderRadius: 20, border: "none",
              background: filtre === cat
                ? (CAT_COLORS[cat] || "#1e293b")
                : "#f1f5f9",
              color: filtre === cat ? "#fff" : "#64748b",
              fontSize: 12, fontWeight: 600, cursor: "pointer",
            }}>
            {cat}
          </button>
        ))}
      </div>

      {/* Liste */}
      <div style={{
        maxHeight: 500, overflowY: "auto",
        display: "flex", flexDirection: "column", gap: 4,
      }}>
        {filtered.length === 0 ? (
          <div style={{ padding: 24, textAlign: "center", color: "#94a3b8" }}>
            Aucun événement reçu
          </div>
        ) : filtered.map(evt => (
          <div key={evt.id} style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "7px 12px", borderRadius: 7,
            background: "#f8fafc", border: "1px solid #f1f5f9",
          }}>
            <span style={{
              background: (CAT_COLORS[evt.categorie] || "#6b7280") + "22",
              color: CAT_COLORS[evt.categorie] || "#6b7280",
              padding: "1px 7px", borderRadius: 8,
              fontSize: 10, fontWeight: 700, whiteSpace: "nowrap",
            }}>{evt.categorie}</span>
            <span style={{ fontSize: 12, fontWeight: 600, color: "#1e293b", minWidth: 180 }}>
              {evt.operation}
            </span>
            <span style={{ fontSize: 11, color: "#64748b", flex: 1 }}>
              {evt.entite_type}
              {evt.entite_id && (
                <code style={{ marginLeft: 4, fontSize: 10, color: "#94a3b8" }}>
                  {evt.entite_id.slice(0, 8)}…
                </code>
              )}
            </span>
            {evt.region && (
              <span style={{
                background: "#f0fdf4", color: "#15803d",
                padding: "1px 6px", borderRadius: 6, fontSize: 10,
              }}>{evt.region}</span>
            )}
            <span style={{ fontSize: 10, color: "#94a3b8", whiteSpace: "nowrap" }}>
              {new Date(evt.created_at).toLocaleTimeString("fr-FR")}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};

// ─── Historique (DB) ─────────────────────────────────────────────

const Historique: React.FC = () => {
  const [hist,    setHist]    = useState<any[]>([]);
  const [niveau,  setNiveau]  = useState<string>("TOUS");
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    const param = niveau !== "TOUS" ? `?niveau=${niveau}` : "";
    fetch(`/api/v1/monitoring/alertes/historique?limite=100${param ? "&" + param.slice(1) : ""}`)
      .then(r => r.json())
      .then(d => { setHist(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [niveau]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div style={{ display: "flex", gap: 8, marginBottom: 12, alignItems: "center" }}>
        {["TOUS","CRITICAL","WARNING","INFO"].map(n => (
          <button key={n} onClick={() => setNiveau(n)}
            style={{
              padding: "4px 12px", borderRadius: 20, border: "none",
              background: niveau === n ? "#1e293b" : "#f1f5f9",
              color: niveau === n ? "#fff" : "#64748b",
              fontSize: 12, fontWeight: 600, cursor: "pointer",
            }}>{n}</button>
        ))}
        <button onClick={load} style={{
          marginLeft: "auto", background: "#f1f5f9",
          border: "none", borderRadius: 8, padding: "5px 12px",
          fontSize: 12, cursor: "pointer", color: "#475569",
        }}>↻</button>
      </div>

      {loading ? (
        <div style={{ textAlign: "center", padding: 20, color: "#94a3b8" }}>Chargement…</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 500, overflowY: "auto" }}>
          {hist.map((a: any) => {
            const nc = NIVEAU_CFG[a.niveau as AlertNiveau] || NIVEAU_CFG.INFO;
            return (
              <div key={a.alerte_id} style={{
                padding: "8px 12px", borderRadius: 8,
                background: a.acquittee ? "#f8fafc" : nc.bg,
                border: `1px solid ${a.acquittee ? "#e2e8f0" : nc.border}`,
                opacity: a.acquittee ? 0.7 : 1,
                display: "flex", alignItems: "center", gap: 10,
              }}>
                <span style={{ fontSize: 16 }}>{a.acquittee ? "✓" : nc.icon}</span>
                <span style={{
                  background: nc.color + "22", color: nc.color,
                  padding: "1px 7px", borderRadius: 8, fontSize: 10, fontWeight: 700,
                  whiteSpace: "nowrap",
                }}>{a.niveau}</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: "#1e293b", flex: 1 }}>
                  {a.titre}
                </span>
                <span style={{ fontSize: 10, color: "#94a3b8", whiteSpace: "nowrap" }}>
                  {new Date(a.created_at).toLocaleString("fr-FR")}
                </span>
              </div>
            );
          })}
          {!hist.length && (
            <div style={{ textAlign: "center", padding: 20, color: "#94a3b8" }}>
              Aucune alerte dans l'historique
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// ─── Page principale ──────────────────────────────────────────────

type Tab = "dashboard" | "alertes" | "evenements" | "historique";

export default function MonitoringPage() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const { alertes, evenements, connStatus, setAlertes } = useMonitoringSSE();

  const nb_alertes_actives = alertes.filter(a => !a.acquittee).length;
  const nb_critical        = alertes.filter(a => !a.acquittee && a.niveau === "CRITICAL").length;

  const acquitter = useCallback(async (id: string) => {
    try {
      await fetch(`/api/v1/monitoring/alertes/${id}/acquitter`, { method: "POST" });
      setAlertes(prev => prev.map(a => a.id === id ? { ...a, acquittee: true } : a));
    } catch {}
  }, [setAlertes]);

  const tabs: { id: Tab; label: string; badge?: number }[] = [
    { id: "dashboard",  label: "📊 Tableau de bord" },
    { id: "alertes",    label: "🔔 Alertes",    badge: nb_alertes_actives },
    { id: "evenements", label: "⚡ Événements",  badge: evenements.length },
    { id: "historique", label: "🗂 Historique" },
  ];

  return (
    <div style={{
      padding: "24px 28px", background: "#f8fafc", minHeight: "100vh",
      fontFamily: "system-ui, sans-serif",
    }}>
      {/* En-tête */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 8 }}>
          <div style={{
            width: 44, height: 44, borderRadius: 12,
            background: nb_critical > 0
              ? "linear-gradient(135deg, #dc2626, #b91c1c)"
              : "linear-gradient(135deg, #2563eb, #059669)",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 22,
            animation: nb_critical > 0 ? "pulse 1.5s infinite" : "none",
          }}>📡</div>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: "#0f172a" }}>
              Monitoring Temps Réel
            </h1>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 2 }}>
              <ConnBadge status={connStatus} />
              <span style={{ fontSize: 12, color: "#94a3b8" }}>
                · {nb_alertes_actives} alerte(s) active(s)
                · {evenements.length} événement(s) reçu(s)
              </span>
            </div>
          </div>
        </div>

        {/* Bannière CRITICAL si alertes */}
        {nb_critical > 0 && (
          <div style={{
            background: "#fee2e2", borderRadius: 10, padding: "10px 16px",
            border: "1px solid #fca5a5", display: "flex", alignItems: "center", gap: 10,
          }}>
            <span style={{ fontSize: 18 }}>🚨</span>
            <span style={{ fontWeight: 700, color: "#dc2626" }}>
              {nb_critical} alerte(s) CRITICAL active(s) — action immédiate requise
            </span>
            <button onClick={() => setTab("alertes")} style={{
              marginLeft: "auto", background: "#dc2626", color: "#fff",
              border: "none", borderRadius: 6, padding: "5px 12px",
              fontSize: 12, cursor: "pointer", fontWeight: 700,
            }}>Voir les alertes →</button>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div style={{
        display: "flex", gap: 4, marginBottom: 20,
        background: "#f1f5f9", borderRadius: 10, padding: 4, width: "fit-content",
      }}>
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            style={{
              padding: "8px 18px", borderRadius: 8, border: "none",
              background: tab === t.id ? "#fff" : "transparent",
              fontWeight: tab === t.id ? 700 : 400,
              color: tab === t.id ? "#2563eb" : "#64748b",
              fontSize: 13, cursor: "pointer",
              boxShadow: tab === t.id ? "0 1px 4px rgba(0,0,0,.08)" : "none",
              display: "flex", alignItems: "center", gap: 6,
            }}>
            {t.label}
            {t.badge !== undefined && t.badge > 0 && (
              <span style={{
                background: t.id === "alertes" && nb_critical > 0 ? "#dc2626" : "#2563eb",
                color: "#fff", borderRadius: 10,
                padding: "0 6px", fontSize: 10, fontWeight: 800,
              }}>{t.badge}</span>
            )}
          </button>
        ))}
      </div>

      {/* Contenu */}
      <div style={{
        background: "#fff", borderRadius: 12,
        border: "1px solid #e2e8f0", padding: 20,
      }}>
        {tab === "dashboard"  && <TBDashboard alertes={alertes} />}
        {tab === "alertes"    && (
          <AlertesPanel alertes={alertes} onAcquitter={acquitter} />
        )}
        {tab === "evenements" && <EvenementsFlux evenements={evenements} />}
        {tab === "historique" && <Historique />}
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.6; }
        }
      `}</style>
    </div>
  );
}
