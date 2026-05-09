import { z } from 'zod'
/**
 * FONCIER+ ── Moteur de Simulation des Opérations Foncières
 * Interface de simulation pré-exécution en 6 couches.
 *
 * Onglets :
 *   ⚡ Simulateur      — formulaire + rapport temps réel
 *   🔬 Batch           — comparaison de scénarios alternatifs
 *   📊 Tableau de bord — santé, stats, historique
 */
import React, { useState, useEffect, useCallback } from "react";

// ARCHITECTURE v3.4.7 : données temps réel via useDataFetch
// Les fetch() inline sont conservés pour les actions (POST/PATCH/DELETE)
// Le chargement initial doit utiliser useDataFetch pour cohérence

// ─── Types ───────────────────────────────────────────────────────

type SimStatut   = "SUCCES" | "AVERTISSEMENT" | "ECHEC";
type ImpactNiveau= "BLOQUANT" | "AVERTISSEMENT" | "INFO";

interface Impact {
  couche:    string;
  niveau:    ImpactNiveau;
  entite:    string;
  entite_id: string;
  message:   string;
  detail:    string;
  correction: string;
}
interface Etape {
  couche:    string;
  nom:       string;
  statut:    string;
  duree_ms:  number;
  nb_impacts: number;
  details:   string[];
}
interface SimResult {
  simulation_id:    string;
  operation_type:   string;
  statut:           SimStatut;
  autorisee:        boolean;
  nb_bloquants:     number;
  nb_avertissements: number;
  nb_infos:         number;
  recommandation:   string;
  sha256_rapport:   string;
  duree_ms:         number;
  impacts:          Impact[];
  etapes:           Etape[];
  donnees_actuelles: Record<string, any>;
  effets_projetes:  Record<string, any>;
}
interface OperationDef {
  code:             string;
  libelle:          string;
  docs_requis:      string[];
  niveau_validation: number;
}
interface SanteSim {
  succes_24h:          number;
  avert_24h:           number;
  echecs_24h:          number;
  impacts_bloquants_24h: number;
  duree_moy_ms:        number;
}

// ─── Constantes ──────────────────────────────────────────────────

const COUCHE_DEFS: Record<string, { label: string; icon: string; color: string }> = {
  S0: { label: "Initialisation", icon: "⚙",  color: "#6b7280" },
  S1: { label: "Pré-conditions", icon: "🔍", color: "#2563eb" },
  S2: { label: "Règles métier",  icon: "📋", color: "#7c3aed" },
  S3: { label: "Droits fonciers",icon: "⚖",  color: "#0891b2" },
  S4: { label: "Documents",      icon: "📄", color: "#b45309" },
  S5: { label: "Juridiction",    icon: "🏛",  color: "#059669" },
  S6: { label: "Topologie",      icon: "🗺",  color: "#dc2626" },
  SX: { label: "Erreur interne", icon: "⚠",  color: "#dc2626" },
};

const NIVEAU_CFG: Record<ImpactNiveau, { bg: string; color: string; label: string }> = {
  BLOQUANT:     { bg: "#fee2e2", color: "#dc2626", label: "✗ BLOQUANT" },
  AVERTISSEMENT:{ bg: "#fef3c7", color: "#b45309", label: "⚠ AVERTISSEMENT" },
  INFO:         { bg: "#eff6ff", color: "#2563eb", label: "ℹ INFO" },
};

const STATUT_CFG: Record<SimStatut, { bg: string; color: string; border: string; icon: string; label: string }> = {
  SUCCES:        { bg: "#dcfce7", color: "#15803d", border: "#22c55e", icon: "✓", label: "SUCCÈS" },
  AVERTISSEMENT: { bg: "#fef3c7", color: "#b45309", border: "#f59e0b", icon: "⚠", label: "AVERTISSEMENT" },
  ECHEC:         { bg: "#fee2e2", color: "#dc2626", border: "#ef4444", icon: "✗", label: "ÉCHEC" },
};

// ─── Composant : Rapport simulation ──────────────────────────────

const RapportSimulation: React.FC<{ result: SimResult; onReset: () => void }> = ({
  result, onReset,
}) => {
  const [tab, setTab] = useState<"pipeline"|"impacts"|"effets">("pipeline");
  const sc = STATUT_CFG[result.statut];

  return (
    <div style={{
      background: "#fff", borderRadius: 12,
      border: `2px solid ${sc.border}`,
    }}>
      {/* Header résultat */}
      <div style={{
        padding: "16px 20px",
        background: sc.bg,
        borderRadius: "10px 10px 0 0",
        borderBottom: `1px solid ${sc.border}`,
      }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{
              fontSize: 28, fontWeight: 900, color: sc.color,
            }}>{sc.icon}</span>
            <div>
              <div style={{ fontWeight: 800, fontSize: 18, color: sc.color }}>
                {sc.label} — {result.operation_type}
              </div>
              <div style={{ fontSize: 12, color: "#64748b", marginTop: 2 }}>
                {result.recommandation}
              </div>
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <div style={{ display: "flex", gap: 6 }}>
              {[
                { n: result.nb_bloquants,     c: "#dc2626", l: "BLOQUANT" },
                { n: result.nb_avertissements, c: "#b45309", l: "AVERT" },
                { n: result.nb_infos,          c: "#2563eb", l: "INFO" },
              ].map(({ n, c, l }) => n > 0 && (
                <span key={l} style={{
                  background: c + "22", color: c,
                  padding: "3px 10px", borderRadius: 20,
                  fontSize: 12, fontWeight: 700,
                }}>
                  {n} {l}
                </span>
              ))}
            </div>
            <span style={{ fontSize: 12, color: "#94a3b8" }}>⏱ {result.duree_ms}ms</span>
            <button onClick={onReset} style={{
              background: "#f1f5f9", border: "1px solid #e2e8f0",
              borderRadius: 8, padding: "5px 12px", fontSize: 12,
              cursor: "pointer", color: "#475569",
            }}>Nouvelle simulation</button>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div style={{
        display: "flex", gap: 2,
        padding: "8px 16px 0",
        borderBottom: "1px solid #f1f5f9",
      }}>
        {([
          { id: "pipeline", label: "🔄 Pipeline" },
          { id: "impacts",  label: `⚡ Impacts (${result.impacts.length})` },
          { id: "effets",   label: "📊 Effets projetés" },
        ] as const).map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            style={{
              padding: "6px 16px", border: "none", cursor: "pointer",
              background: "transparent",
              borderBottom: tab === t.id ? "2px solid #2563eb" : "2px solid transparent",
              color: tab === t.id ? "#2563eb" : "#64748b",
              fontWeight: tab === t.id ? 700 : 400,
              fontSize: 13,
            }}>
            {t.label}
          </button>
        ))}
      </div>

      <div style={{ padding: 20 }}>
        {tab === "pipeline" && <PipelineView etapes={result.etapes} />}
        {tab === "impacts"  && <ImpactsView impacts={result.impacts} />}
        {tab === "effets"   && <EffetsView effets={result.effets_projetes} />}
      </div>

      {/* Footer SHA */}
      <div style={{
        padding: "8px 20px", background: "#f8fafc",
        borderRadius: "0 0 10px 10px", borderTop: "1px solid #f1f5f9",
        fontSize: 10, color: "#94a3b8",
      }}>
        SHA-256 rapport : {result.sha256_rapport} · ID : {result.simulation_id.slice(0,16)}…
      </div>
    </div>
  );
};

// ─── Pipeline view ────────────────────────────────────────────────

const PipelineView: React.FC<{ etapes: Etape[] }> = ({ etapes }) => (
  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
    {etapes.map((e, idx) => {
      const c = COUCHE_DEFS[e.couche] || { label: e.nom, icon: "?", color: "#6b7280" };
      const sc: Record<string, string> = {
        PASS: "#15803d", FAIL: "#dc2626", WARN: "#b45309", SKIP: "#9ca3af",
      };
      const bg: Record<string, string> = {
        PASS: "#f0fdf4", FAIL: "#fff5f5", WARN: "#fffbeb", SKIP: "#f8fafc",
      };
      return (
        <div key={idx} style={{
          background: bg[e.statut] || "#f8fafc",
          border: `1px solid ${sc[e.statut] || "#e2e8f0"}44`,
          borderLeft: `4px solid ${sc[e.statut] || "#e2e8f0"}`,
          borderRadius: 8, padding: "10px 14px",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{
              background: c.color, color: "#fff",
              borderRadius: 6, padding: "2px 8px",
              fontSize: 11, fontWeight: 800,
            }}>{c.icon} {e.couche}</span>
            <span style={{ fontWeight: 600, fontSize: 13, color: "#1e293b" }}>{e.nom}</span>
            <span style={{
              marginLeft: "auto", fontSize: 11, fontWeight: 700,
              color: sc[e.statut] || "#6b7280",
            }}>{e.statut}</span>
            <span style={{ fontSize: 11, color: "#94a3b8" }}>{e.duree_ms}ms</span>
            {e.nb_impacts > 0 && (
              <span style={{
                background: "#fef3c7", color: "#92400e",
                padding: "1px 8px", borderRadius: 10, fontSize: 11,
              }}>{e.nb_impacts} impact(s)</span>
            )}
          </div>
          {e.details.slice(0, 2).map((d, i) => (
            <div key={i} style={{
              fontSize: 11, color: "#64748b", marginTop: 4,
              paddingLeft: 8,
            }}>› {d}</div>
          ))}
        </div>
      );
    })}
  </div>
);

// ─── Impacts view ─────────────────────────────────────────────────

const ImpactsView: React.FC<{ impacts: Impact[] }> = ({ impacts }) => {
  if (!impacts.length) return (
    <div style={{
      textAlign: "center", padding: "24px 0",
      color: "#15803d", fontSize: 14, fontWeight: 600,
    }}>
      ✓ Aucun impact détecté — simulation complète sans anomalie
    </div>
  );

  const byNiveau = ["BLOQUANT", "AVERTISSEMENT", "INFO"] as ImpactNiveau[];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {byNiveau.map(niv => {
        const filtered = impacts.filter(i => i.niveau === niv);
        if (!filtered.length) return null;
        const nc = NIVEAU_CFG[niv];
        return (
          <div key={niv}>
            <div style={{
              fontSize: 12, fontWeight: 700, color: nc.color,
              marginBottom: 8,
            }}>
              {nc.label} ({filtered.length})
            </div>
            {filtered.map((imp, i) => {
              const c = COUCHE_DEFS[imp.couche] || { label: imp.couche, icon: "?", color: "#6b7280" };
              return (
                <div key={i} style={{
                  background: nc.bg, borderRadius: 8,
                  padding: "10px 14px", marginBottom: 6,
                  borderLeft: `4px solid ${nc.color}`,
                  border: `1px solid ${nc.color}22`,
                  borderLeftWidth: 4,
                }}>
                  <div style={{ display: "flex", gap: 8, marginBottom: 4 }}>
                    <span style={{
                      background: c.color + "22", color: c.color,
                      padding: "1px 7px", borderRadius: 8,
                      fontSize: 10, fontWeight: 700,
                    }}>{c.icon} {imp.couche}</span>
                    <span style={{
                      background: "#f1f5f9", color: "#64748b",
                      padding: "1px 7px", borderRadius: 8, fontSize: 10,
                    }}>{imp.entite}</span>
                  </div>
                  <div style={{ fontWeight: 600, fontSize: 13, color: "#1e293b", marginBottom: 3 }}>
                    {imp.message}
                  </div>
                  {imp.detail && (
                    <div style={{ fontSize: 11, color: "#64748b", marginBottom: 4 }}>
                      {imp.detail}
                    </div>
                  )}
                  {imp.correction && (
                    <div style={{
                      background: "#f0fdf4", borderRadius: 6,
                      padding: "4px 10px", fontSize: 11, color: "#15803d",
                    }}>
                      💡 {imp.correction}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
};

// ─── Effets projetés ──────────────────────────────────────────────

const EffetsView: React.FC<{ effets: Record<string, any> }> = ({ effets }) => {
  if (!Object.keys(effets).length) return (
    <div style={{ textAlign: "center", padding: 24, color: "#94a3b8", fontSize: 13 }}>
      Aucun effet projeté calculé.
    </div>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {Object.entries(effets).map(([k, v]) => (
        <div key={k} style={{
          background: "#f8fafc", borderRadius: 8, padding: "10px 14px",
          border: "1px solid #e2e8f0",
        }}>
          <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, marginBottom: 4 }}>
            {k.replace(/_/g, " ").toUpperCase()}
          </div>
          <pre style={{
            fontSize: 12, color: "#1e293b", margin: 0,
            whiteSpace: "pre-wrap", wordBreak: "break-word",
          }}>
            {typeof v === "object" ? JSON.stringify(v, null, 2) : String(v)}
          </pre>
        </div>
      ))}
    </div>
  );
};

// ─── Formulaire de simulation ─────────────────────────────────────

const SimulateurForm: React.FC<{
  operations: OperationDef[];
  onResult:   (r: SimResult) => void;
}> = ({ operations, onResult }) => {
  const [opType,    setOpType]    = useState("MUTATION");
  const [parcelIds, setParcelIds] = useState("");
  const [region,    setRegion]    = useState("NI-01");
  const [titulaire, setTitulaire] = useState("");
  const [nbLots,    setNbLots]    = useState("");
  const [motif,     setMotif]     = useState("");
  const [loading,   setLoading]   = useState(false);
  const [error,     setError]     = useState<string | null>(null);

  const currentOp = operations.find(o => o.code === opType);

  const handleSubmit = async () => {
    const ids = parcelIds.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
    if (!ids.length) { setError("Saisir au moins un UUID de parcelle."); return; }

    setLoading(true); setError(null);
    try {
      const body: Record<string, any> = {
        operation_type: opType,
        parcelle_ids:   ids,
        region,
        motif: motif || undefined,
      };
      if (titulaire) body.nouveau_titulaire_id = titulaire;
      if (nbLots)    body.nb_lots = parseInt(nbLots);

      const res = await fetch("/api/v1/admin/simulation/lancer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      onResult(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      background: "#fff", borderRadius: 12,
      border: "1px solid #e2e8f0", padding: 20,
    }}>
      <div style={{ fontWeight: 700, fontSize: 15, color: "#1e293b", marginBottom: 16 }}>
        ⚡ Paramètres de simulation
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 14 }}>
        {/* Type d'opération */}
        <div style={{ flex: 2, minWidth: 180 }}>
          <label style={{ fontSize: 12, color: "#64748b", fontWeight: 600,
                          display: "block", marginBottom: 4 }}>
            Type d'opération
          </label>
          <select value={opType} onChange={e => setOpType(e.target.value)}
            style={{ width: "100%", padding: "8px 10px", borderRadius: 8,
                     border: "1px solid #e2e8f0", fontSize: 13 }}>
            {operations.map(op => (
              <option key={op.code} value={op.code}>
                {op.code} — {op.libelle}
              </option>
            ))}
          </select>
          {currentOp && (
            <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 4 }}>
              Docs requis : {currentOp.docs_requis.join(", ")} ·
              Niveau validation : {currentOp.niveau_validation}
            </div>
          )}
        </div>

        {/* Région */}
        <div style={{ flex: 1, minWidth: 100 }}>
          <label style={{ fontSize: 12, color: "#64748b", fontWeight: 600,
                          display: "block", marginBottom: 4 }}>Région</label>
          <select value={region} onChange={e => setRegion(e.target.value)}
            style={{ width: "100%", padding: "8px 10px", borderRadius: 8,
                     border: "1px solid #e2e8f0", fontSize: 13 }}>
            {["NI-01","NI-02","NI-03","NI-04","NI-05","NI-06","NI-07","NI-08"].map(r => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </div>
      </div>

      {/* IDs des parcelles */}
      <div style={{ marginBottom: 12 }}>
        <label style={{ fontSize: 12, color: "#64748b", fontWeight: 600,
                        display: "block", marginBottom: 4 }}>
          UUIDs des parcelles (un par ligne, ou séparés par virgule)
        </label>
        <textarea value={parcelIds} onChange={e => setParcelIds(e.target.value)}
          rows={3} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
          style={{
            width: "100%", boxSizing: "border-box",
            fontFamily: "monospace", fontSize: 12,
            border: "1px solid #e2e8f0", borderRadius: 8,
            padding: "8px 12px", background: "#f8fafc", resize: "vertical",
          }}
        />
      </div>

      {/* Champs contextuels */}
      {["MUTATION","SUCCESSION"].includes(opType) && (
        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 12, color: "#64748b", fontWeight: 600,
                          display: "block", marginBottom: 4 }}>
            UUID nouveau titulaire
          </label>
          <input value={titulaire} onChange={e => setTitulaire(e.target.value)}
            placeholder="UUID du nouveau titulaire"
            style={{ width: "100%", boxSizing: "border-box",
                     padding: "8px 12px", borderRadius: 8,
                     border: "1px solid #e2e8f0", fontSize: 13 }} />
        </div>
      )}
      {["SCISSION","LOTISSEMENT"].includes(opType) && (
        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 12, color: "#64748b", fontWeight: 600,
                          display: "block", marginBottom: 4 }}>Nombre de lots</label>
          <input type="number" min={2} max={100}
            value={nbLots} onChange={e => setNbLots(e.target.value)}
            style={{ width: 100, padding: "8px 12px", borderRadius: 8,
                     border: "1px solid #e2e8f0", fontSize: 13 }} />
        </div>
      )}

      {/* Motif */}
      <div style={{ marginBottom: 16 }}>
        <label style={{ fontSize: 12, color: "#64748b", fontWeight: 600,
                        display: "block", marginBottom: 4 }}>Motif (facultatif)</label>
        <input value={motif} onChange={e => setMotif(e.target.value)}
          placeholder="Motif de l'opération..."
          style={{ width: "100%", boxSizing: "border-box",
                   padding: "8px 12px", borderRadius: 8,
                   border: "1px solid #e2e8f0", fontSize: 13 }} />
      </div>

      <button onClick={handleSubmit} disabled={loading}
        style={{
          background: loading
            ? "#94a3b8"
            : "linear-gradient(135deg, #2563eb, #7c3aed)",
          color: "#fff", border: "none", borderRadius: 8,
          padding: "11px 28px", fontSize: 14, fontWeight: 700,
          cursor: loading ? "not-allowed" : "pointer",
        }}>
        {loading ? "⏳ Simulation en cours…" : "▶ Lancer la simulation"}
      </button>

      {error && (
        <div style={{ marginTop: 10, color: "#dc2626", fontSize: 12 }}>❌ {error}</div>
      )}
    </div>
  );
};

// ─── Tableau de bord ──────────────────────────────────────────────

const SimDashboard: React.FC = () => {
  const [data, setData]   = useState<any>(null);
  const [load, setLoad]   = useState(true);

  useEffect(() => {
    fetch("/api/v1/admin/simulation/rapport")
      .then(r => r.json())
      .then(d => { setData(d); setLoad(false); })
      .catch(() => setLoad(false));
  }, []);

  if (load) return <div style={{ padding: 24, textAlign: "center", color: "#94a3b8" }}>
    Chargement…
  </div>;
  if (!data) return null;

  const s = data.sante || {};
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Santé */}
      <div style={{
        background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 20,
      }}>
        <div style={{ fontWeight: 700, fontSize: 14, color: "#1e293b", marginBottom: 14 }}>
          📊 Santé du moteur (24h)
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {[
            { l: "Succès",       v: s.succes_24h,           c: "#15803d", icon: "✓" },
            { l: "Avertissements",v: s.avert_24h,           c: "#b45309", icon: "⚠" },
            { l: "Échecs",       v: s.echecs_24h,           c: "#dc2626", icon: "✗" },
            { l: "Impacts BLOQUANT", v: s.impacts_bloquants_24h, c: "#dc2626", icon: "🚫" },
            { l: "Durée moy. ms", v: s.duree_moy_ms,        c: "#7c3aed", icon: "⏱" },
          ].map(({ l, v, c, icon }) => (
            <div key={l} style={{
              flex: 1, minWidth: 100, background: "#f8fafc",
              borderRadius: 10, padding: "12px 16px",
              border: "1px solid #e2e8f0",
            }}>
              <div style={{ fontSize: 11, color: "#64748b", fontWeight: 600, marginBottom: 4 }}>
                {icon} {l}
              </div>
              <div style={{ fontSize: 24, fontWeight: 800, color: c }}>
                {v ?? "—"}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Stats par opération */}
      {data.stats_30j?.length > 0 && (
        <div style={{
          background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 20,
        }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: "#1e293b", marginBottom: 12 }}>
            📈 Statistiques 30 jours par opération
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "#f8fafc" }}>
                  {["Opération","Total","Succès","Échecs","Taux autorisé","Durée moy.","Bloquants/sim"].map(h => (
                    <th key={h} style={{
                      padding: "8px 12px", textAlign: "left",
                      color: "#64748b", fontWeight: 700, borderBottom: "1px solid #e2e8f0",
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.stats_30j.map((row: any) => (
                  <tr key={row.operation_type} style={{ borderBottom: "1px solid #f1f5f9" }}>
                    <td style={{ padding: "8px 12px", fontWeight: 600, color: "#1e293b" }}>
                      {row.operation_type}
                    </td>
                    <td style={{ padding: "8px 12px", color: "#475569" }}>{row.nb_total}</td>
                    <td style={{ padding: "8px 12px", color: "#15803d" }}>{row.nb_succes}</td>
                    <td style={{ padding: "8px 12px", color: "#dc2626" }}>{row.nb_echecs}</td>
                    <td style={{ padding: "8px 12px" }}>
                      <span style={{
                        background: (row.taux_autorisation_pct||0) >= 70 ? "#dcfce7" : "#fee2e2",
                        color: (row.taux_autorisation_pct||0) >= 70 ? "#15803d" : "#dc2626",
                        padding: "2px 8px", borderRadius: 10, fontWeight: 700,
                      }}>
                        {row.taux_autorisation_pct ?? 0}%
                      </span>
                    </td>
                    <td style={{ padding: "8px 12px", color: "#475569" }}>{row.duree_moy_ms}ms</td>
                    <td style={{ padding: "8px 12px", color: "#b45309" }}>{row.moy_bloquants}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};

// ─── Batch simulator ──────────────────────────────────────────────

const BatchSimulator: React.FC<{ operations: OperationDef[] }> = ({ operations }) => {
  const [json,    setJson]    = useState(
    JSON.stringify([
      { nom: "Scénario A — MUTATION",
        simulation: { operation_type:"MUTATION", parcelle_ids:["00000000-0000-0000-0000-000000000001"], region:"NI-01" } },
      { nom: "Scénario B — SUCCESSION",
        simulation: { operation_type:"SUCCESSION", parcelle_ids:["00000000-0000-0000-0000-000000000001"], region:"NI-01" } },
    ], null, 2)
  );
  const [result, setResult]  = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/v1/admin/simulation/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenarios: JSON.parse(json) }),
      });
      setResult(await res.json());
    } catch(e) { console.error(e); }
    finally { setLoading(false); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{
        background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 20,
      }}>
        <div style={{ fontWeight: 700, fontSize: 15, color: "#1e293b", marginBottom: 12 }}>
          🔬 Simulation multi-scénarios (max 5)
        </div>
        <textarea value={json} onChange={e => setJson(e.target.value)} rows={12}
          style={{
            width: "100%", boxSizing: "border-box",
            fontFamily: "monospace", fontSize: 12,
            border: "1px solid #e2e8f0", borderRadius: 8,
            padding: "10px 14px", background: "#f8fafc", resize: "vertical",
          }} />
        <button onClick={run} disabled={loading}
          style={{
            marginTop: 12, background: "#7c3aed", color: "#fff",
            border: "none", borderRadius: 8, padding: "10px 24px",
            fontSize: 14, fontWeight: 700, cursor: "pointer",
          }}>
          {loading ? "⏳ Simulation batch…" : "🔬 Lancer la comparaison"}
        </button>
      </div>

      {result && (
        <div style={{
          background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 20,
        }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: "#1e293b", marginBottom: 12 }}>
            Résultats — meilleur scénario :
            <span style={{
              background: "#dcfce7", color: "#15803d",
              marginLeft: 8, padding: "2px 10px", borderRadius: 10, fontSize: 13,
            }}>
              {result.meilleur_scenario}
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {result.scenarios?.map((sc: any) => {
              const cfg = STATUT_CFG[sc.statut as SimStatut] || STATUT_CFG.ECHEC;
              return (
                <div key={sc.scenario} style={{
                  background: cfg.bg, borderRadius: 8, padding: "10px 14px",
                  border: `1px solid ${cfg.border}44`,
                  display: "flex", alignItems: "center", gap: 12,
                }}>
                  <span style={{ fontSize: 18, fontWeight: 900, color: cfg.color }}>
                    {cfg.icon}
                  </span>
                  <span style={{ fontWeight: 700, fontSize: 13, color: "#1e293b" }}>
                    {sc.scenario}
                  </span>
                  <span style={{ fontSize: 12, color: "#64748b" }}>
                    {sc.operation_type}
                  </span>
                  <span style={{ marginLeft: "auto", fontSize: 11, color: cfg.color, fontWeight: 700 }}>
                    {sc.nb_bloquants}B / {sc.nb_avertissements}A
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};

// ─── Page principale ──────────────────────────────────────────────

type Tab = "simulateur" | "batch" | "dashboard";


// Schéma de validation simulation
const simulationSchema = z.object({
  type_simulation: z.string().min(1, 'Type requis'),
  parcelle_id:     z.string().uuid('UUID invalide').optional(),
  parametres:      z.record(z.unknown()).optional(),
})
type SimulationForm = z.infer<typeof simulationSchema>

function validateSimulationForm(data: SimulationForm): string | null {
  try {
    simulationSchema.parse(data)
    return null
  } catch (err: unknown) {
    if (err instanceof z.ZodError) return err.errors[0]?.message ?? 'Formulaire invalide'
    return 'Erreur de validation'
  }
}

export default function SimulationPage() {
  const [tab,        setTab]       = useState<Tab>("simulateur");
  const [result,     setResult]    = useState<SimResult | null>(null);
  const [operations, setOperations] = useState<OperationDef[]>([]);

  useEffect(() => {
    fetch("/api/v1/admin/simulation/operations")
      .then(r => r.json())
      .then(setOperations)
      .catch(() => {});
  }, []);

  return (
    <div style={{
      padding: "24px 28px", background: "#f8fafc", minHeight: "100vh",
      fontFamily: "system-ui, sans-serif",
    }}>
      {/* En-tête */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 8 }}>
          <div style={{
            width: 44, height: 44, borderRadius: 12,
            background: "linear-gradient(135deg, #2563eb, #059669)",
            display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22,
          }}>▶</div>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: "#0f172a" }}>
              Simulation des Opérations Foncières
            </h1>
            <p style={{ margin: 0, fontSize: 13, color: "#64748b" }}>
              Dry-run complet en 6 couches · SAVEPOINT PostgreSQL · Données réelles intactes
            </p>
          </div>
        </div>

        {/* Badges couches */}
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 10 }}>
          {Object.entries(COUCHE_DEFS)
            .filter(([k]) => !["S0","SX"].includes(k))
            .map(([k, v]) => (
              <span key={k} style={{
                background: v.color + "15",
                border: `1px solid ${v.color}44`,
                color: v.color, borderRadius: 8,
                padding: "3px 10px", fontSize: 11, fontWeight: 700,
              }}>
                {v.icon} {k} {v.label}
              </span>
            ))}
        </div>
      </div>

      {/* Tabs */}
      <div style={{
        display: "flex", gap: 4, marginBottom: 20,
        background: "#f1f5f9", borderRadius: 10, padding: 4, width: "fit-content",
      }}>
        {([
          { id: "simulateur", label: "⚡ Simulateur",      icon: "" },
          { id: "batch",      label: "🔬 Multi-scénarios", icon: "" },
          { id: "dashboard",  label: "📊 Tableau de bord", icon: "" },
        ] as const).map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            style={{
              padding: "8px 18px", borderRadius: 8, border: "none",
              background: tab === t.id ? "#fff" : "transparent",
              fontWeight: tab === t.id ? 700 : 400,
              color: tab === t.id ? "#2563eb" : "#64748b",
              fontSize: 13, cursor: "pointer",
              boxShadow: tab === t.id ? "0 1px 4px rgba(0,0,0,.08)" : "none",
            }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Contenu */}
      {tab === "simulateur" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {!result
            ? <SimulateurForm operations={operations} onResult={setResult} />
            : <RapportSimulation result={result} onReset={() => setResult(null)} />
          }
        </div>
      )}
      {tab === "batch" && <BatchSimulator operations={operations} />}
      {tab === "dashboard" && <SimDashboard />}
    </div>
  );
}
