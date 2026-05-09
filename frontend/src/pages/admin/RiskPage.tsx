/**
 * FONCIER+ ── Analyse Prédictive des Risques Fonciers
 *
 * Onglets :
 *   🗺 Carte des risques   — visualisation choroplèthe des 8 régions Niger
 *   📊 Tableau de bord     — métriques globales + top entités
 *   🔍 Analyser            — analyse à la demande (parcelle/zone/user/op)
 *   🔔 Alertes             — alertes prédictives actives
 */
import React, { useState, useEffect, useCallback } from "react";

// ARCHITECTURE v3.4.7 : données temps réel via useDataFetch
// Les fetch() inline sont conservés pour les actions (POST/PATCH/DELETE)
// Le chargement initial doit utiliser useDataFetch pour cohérence

// ─── Types ───────────────────────────────────────────────────────

type Niveau = "FAIBLE" | "MOYEN" | "ÉLEVÉ" | "CRITIQUE";

interface RiskScore {
  entite_type:     string;
  entite_id:       string;
  score:           number;
  niveau:          Niveau;
  facteurs_cles:   string[];
  recommandations: string[];
  duree_ms:        number;
  sha256:          string;
  signaux: {
    analyseur:   string;
    valeur:      number;
    poids:       number;
    description: string;
    donnees:     Record<string, any>;
  }[];
}
interface ZoneData {
  region:         string;
  score:          number;
  niveau:         Niveau;
  nb_parcelles:   number;
  litiges_actifs: number;
  mutations_30j:  number;
  facteurs_json:  any;
}
interface Alerte {
  alerte_id:      string;
  niveau:         Niveau;
  entite_type:    string;
  entite_id:      string;
  titre:          string;
  message:        string;
  score:          number;
  recommandation: string;
  created_at:     string;
}
interface TableauBord {
  entites_critique: number;
  entites_eleve:    number;
  entites_moyen:    number;
  entites_faible:   number;
  alertes_critique: number;
  alertes_eleve:    number;
  alertes_moyen:    number;
  zone_risque_max:  string;
  score_zone_max:   number;
  parcelle_risque_max: string;
  score_parcelle_max:  number;
}

// ─── Constantes ──────────────────────────────────────────────────

const NIVEAU_CFG: Record<Niveau, { bg: string; color: string; border: string; icon: string }> = {
  FAIBLE:   { bg: "#f0fdf4", color: "#15803d", border: "#86efac", icon: "✓" },
  MOYEN:    { bg: "#fef3c7", color: "#b45309", border: "#fde68a", icon: "⚠" },
  "ÉLEVÉ":  { bg: "#fff1f2", color: "#dc2626", border: "#fecaca", icon: "▲" },
  CRITIQUE: { bg: "#fee2e2", color: "#dc2626", border: "#fca5a5", icon: "🚨" },
};

const CARTE_COULEURS: Record<Niveau, string> = {
  FAIBLE:   "#22c55e",
  MOYEN:    "#f59e0b",
  "ÉLEVÉ":  "#ef4444",
  CRITIQUE: "#dc2626",
};

// Coordonnées approximatives des 8 régions du Niger pour la carte SVG
const REGIONS_NIGER: { code: string; nom: string; cx: number; cy: number }[] = [
  { code: "NI-01", nom: "Agadez",    cx: 320, cy: 120 },
  { code: "NI-02", nom: "Diffa",     cx: 430, cy: 220 },
  { code: "NI-03", nom: "Dosso",     cx: 165, cy: 295 },
  { code: "NI-04", nom: "Maradi",    cx: 250, cy: 265 },
  { code: "NI-05", nom: "Tahoua",    cx: 200, cy: 200 },
  { code: "NI-06", nom: "Tillabéri", cx: 130, cy: 255 },
  { code: "NI-07", nom: "Zinder",    cx: 330, cy: 240 },
  { code: "NI-08", nom: "Niamey",    cx: 148, cy: 278 },
];

// ─── Carte choroplèthe SVG ────────────────────────────────────────

const CarteSVG: React.FC<{
  zones: ZoneData[];
  onZoneClick: (region: string) => void;
  selected: string | null;
}> = ({ zones, onZoneClick, selected }) => {
  const zoneMap: Record<string, ZoneData> = {};
  zones.forEach(z => { zoneMap[z.region] = z; });

  return (
    <div style={{ position: "relative" }}>
      <svg viewBox="0 0 500 380" style={{ width: "100%", height: "auto" }}>
        {/* Fond Niger */}
        <rect x="80" y="80" width="380" height="250" rx="8"
          fill="#f1f5f9" stroke="#e2e8f0" strokeWidth="1" />
        <text x="270" y="360" textAnchor="middle" fontSize="11" fill="#94a3b8">
          République du Niger — Carte des risques fonciers
        </text>

        {/* Régions */}
        {REGIONS_NIGER.map(reg => {
          const zd   = zoneMap[reg.code];
          const niv  = (zd?.niveau as Niveau) || "FAIBLE";
          const sc   = zd?.score || 0;
          const col  = CARTE_COULEURS[niv] || "#22c55e";
          const isSel = selected === reg.code;
          const r    = 28 + (sc / 100) * 18; // rayon proportionnel au score

          return (
            <g key={reg.code} style={{ cursor: "pointer" }}
               onClick={() => onZoneClick(reg.code)}>
              {/* Halo de sélection */}
              {isSel && (
                <circle cx={reg.cx} cy={reg.cy} r={r + 8}
                  fill="none" stroke="#2563eb" strokeWidth="3"
                  strokeDasharray="6,3" />
              )}
              {/* Cercle de risque */}
              <circle cx={reg.cx} cy={reg.cy} r={r}
                fill={col + "44"} stroke={col} strokeWidth={isSel ? 3 : 1.5}
                style={{ transition: "all 0.2s" }} />
              {/* Score au centre */}
              <text x={reg.cx} y={reg.cy + 1} textAnchor="middle"
                dominantBaseline="middle" fontSize="12" fontWeight="700"
                fill={col}>
                {sc.toFixed(0)}
              </text>
              {/* Nom de la région */}
              <text x={reg.cx} y={reg.cy + r + 12} textAnchor="middle"
                fontSize="9" fill="#475569" fontWeight="600">
                {reg.nom}
              </text>
              <text x={reg.cx} y={reg.cy + r + 21} textAnchor="middle"
                fontSize="8" fill="#94a3b8">
                {reg.code}
              </text>
            </g>
          );
        })}
      </svg>

      {/* Légende */}
      <div style={{
        position: "absolute", bottom: 8, right: 8,
        background: "#ffffffdd", borderRadius: 8, padding: "6px 10px",
        border: "1px solid #e2e8f0", fontSize: 10,
      }}>
        {(["FAIBLE","MOYEN","ÉLEVÉ","CRITIQUE"] as Niveau[]).map(n => (
          <div key={n} style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 2 }}>
            <div style={{ width: 10, height: 10, borderRadius: "50%",
                          background: CARTE_COULEURS[n] }} />
            <span style={{ color: "#475569" }}>{n}</span>
          </div>
        ))}
        <div style={{ marginTop: 4, color: "#94a3b8" }}>Taille ∝ score</div>
      </div>
    </div>
  );
};

// ─── Panneau score détaillé ───────────────────────────────────────

const ScoreDetail: React.FC<{ score: RiskScore; onClose: () => void }> = ({
  score, onClose,
}) => {
  const nc = NIVEAU_CFG[score.niveau as Niveau] || NIVEAU_CFG.FAIBLE;

  return (
    <div style={{
      background: "#fff", borderRadius: 12, padding: 20,
      border: `2px solid ${nc.border}`,
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
        <div style={{
          background: nc.bg, color: nc.color,
          borderRadius: 8, padding: "8px 14px",
          fontWeight: 800, fontSize: 16,
        }}>
          {nc.icon} {score.score.toFixed(1)} / 100
        </div>
        <div>
          <div style={{ fontWeight: 700, fontSize: 14, color: "#1e293b" }}>
            Risque {score.niveau} — {score.entite_type}
          </div>
          <div style={{ fontSize: 11, color: "#64748b" }}>
            {score.entite_id.slice(0, 20)}… · {score.duree_ms}ms
          </div>
        </div>
        <button onClick={onClose} style={{
          marginLeft: "auto", background: "#f1f5f9",
          border: "none", borderRadius: 6, padding: "4px 10px",
          fontSize: 12, cursor: "pointer", color: "#64748b",
        }}>✕ Fermer</button>
      </div>

      {/* Jauge score */}
      <div style={{ marginBottom: 16 }}>
        <div style={{
          background: "#f1f5f9", borderRadius: 10, height: 12, overflow: "hidden",
        }}>
          <div style={{
            height: "100%", borderRadius: 10,
            background: `linear-gradient(90deg, #22c55e, #f59e0b, ${nc.color})`,
            width: `${score.score}%`,
            transition: "width 0.5s ease",
          }} />
        </div>
        <div style={{
          display: "flex", justifyContent: "space-between",
          fontSize: 9, color: "#94a3b8", marginTop: 2,
        }}>
          <span>0 FAIBLE</span>
          <span>25 MOYEN</span>
          <span>50 ÉLEVÉ</span>
          <span>75 CRITIQUE 100</span>
        </div>
      </div>

      {/* Signaux */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontWeight: 700, fontSize: 12, color: "#475569",
                      marginBottom: 8, textTransform: "uppercase" }}>
          Signaux analyseurs
        </div>
        {score.signaux.map((s, i) => (
          <div key={i} style={{
            marginBottom: 6, background: "#f8fafc",
            borderRadius: 7, padding: "7px 10px",
            border: "1px solid #f1f5f9",
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
              <span style={{
                background: s.valeur > 50 ? "#fee2e2" : s.valeur > 25 ? "#fef3c7" : "#f0fdf4",
                color: s.valeur > 50 ? "#dc2626" : s.valeur > 25 ? "#b45309" : "#15803d",
                padding: "1px 8px", borderRadius: 10,
                fontSize: 10, fontWeight: 700,
              }}>{s.valeur.toFixed(0)}</span>
              <span style={{ fontSize: 11, fontWeight: 600, color: "#1e293b" }}>
                {s.analyseur}
              </span>
              <span style={{ fontSize: 10, color: "#94a3b8" }}>
                poids {s.poids}
              </span>
              {/* Barre contribution */}
              <div style={{ flex: 1, height: 4, background: "#e2e8f0",
                            borderRadius: 2, marginLeft: 8 }}>
                <div style={{
                  height: "100%", borderRadius: 2,
                  background: s.valeur > 50 ? "#dc2626" : s.valeur > 25 ? "#f59e0b" : "#22c55e",
                  width: `${s.valeur}%`,
                }} />
              </div>
            </div>
            <div style={{ fontSize: 11, color: "#64748b" }}>{s.description}</div>
          </div>
        ))}
      </div>

      {/* Facteurs & recommandations */}
      {score.facteurs_cles.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontWeight: 700, fontSize: 12, color: "#475569",
                        marginBottom: 6, textTransform: "uppercase" }}>
            Facteurs clés
          </div>
          {score.facteurs_cles.map((f, i) => (
            <div key={i} style={{
              fontSize: 12, color: "#374151",
              padding: "4px 0",
              borderBottom: i < score.facteurs_cles.length - 1 ? "1px solid #f1f5f9" : "none",
            }}>
              › {f}
            </div>
          ))}
        </div>
      )}

      {score.recommandations.length > 0 && (
        <div>
          <div style={{ fontWeight: 700, fontSize: 12, color: "#475569",
                        marginBottom: 6, textTransform: "uppercase" }}>
            Recommandations
          </div>
          {score.recommandations.map((r, i) => (
            <div key={i} style={{
              background: "#f0fdf4", borderRadius: 6, padding: "6px 10px",
              fontSize: 11, color: "#15803d", marginBottom: 4,
            }}>
              💡 {r}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ─── Onglet Carte ─────────────────────────────────────────────────

const CarteRisque: React.FC = () => {
  const [zones,     setZones]     = useState<ZoneData[]>([]);
  const [selected,  setSelected]  = useState<string | null>(null);
  const [score,     setScore]     = useState<RiskScore | null>(null);
  const [loading,   setLoading]   = useState(false);
  const [recalc,    setRecalc]    = useState(false);

  useEffect(() => {
    fetch("/api/v1/risk/carte").then(r => r.json()).then(setZones).catch(() => {});
  }, []);

  const handleZoneClick = async (region: string) => {
    setSelected(region);
    setLoading(true);
    try {
      const res = await fetch(`/api/v1/risk/analyser/zone/${region}`, { method: "POST" });
      const data = await res.json();
      setScore(data);
      // Mettre à jour les données de la carte
      setZones(prev => prev.map(z =>
        z.region === region
          ? { ...z, score: data.score, niveau: data.niveau }
          : z
      ));
    } catch {} finally { setLoading(false); }
  };

  const handleRecalcAll = async () => {
    setRecalc(true);
    try {
      const res = await fetch("/api/v1/risk/carte/recalculer");
      const data = await res.json();
      fetch("/api/v1/risk/carte").then(r => r.json()).then(setZones).catch(() => {});
    } catch {} finally { setRecalc(false); }
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
      <div style={{
        background: "#fff", borderRadius: 12,
        border: "1px solid #e2e8f0", padding: 16,
      }}>
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          marginBottom: 12,
        }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: "#1e293b" }}>
            🗺 Carte des risques fonciers
          </div>
          <button onClick={handleRecalcAll} disabled={recalc} style={{
            background: recalc ? "#94a3b8" : "#2563eb", color: "#fff",
            border: "none", borderRadius: 8, padding: "5px 12px",
            fontSize: 12, cursor: "pointer", fontWeight: 600,
          }}>
            {recalc ? "↻ Calcul…" : "↻ Recalculer tout"}
          </button>
        </div>
        <CarteSVG zones={zones} onZoneClick={handleZoneClick} selected={selected} />
        <div style={{ fontSize: 11, color: "#94a3b8", textAlign: "center", marginTop: 4 }}>
          Cliquez sur une région pour voir son analyse détaillée
        </div>
      </div>

      <div>
        {loading && (
          <div style={{ padding: 24, textAlign: "center", color: "#94a3b8" }}>
            Analyse en cours…
          </div>
        )}
        {!loading && score && (
          <ScoreDetail score={score} onClose={() => setScore(null)} />
        )}
        {!loading && !score && (
          <div style={{
            background: "#fff", borderRadius: 12,
            border: "1px solid #e2e8f0", padding: 20,
          }}>
            <div style={{ fontWeight: 700, fontSize: 14, color: "#1e293b", marginBottom: 12 }}>
              Résumé régional
            </div>
            {zones.sort((a, b) => b.score - a.score).map(z => {
              const nc = NIVEAU_CFG[z.niveau as Niveau] || NIVEAU_CFG.FAIBLE;
              return (
                <div key={z.region}
                  onClick={() => handleZoneClick(z.region)}
                  style={{
                    display: "flex", alignItems: "center", gap: 10,
                    padding: "8px 10px", borderRadius: 8,
                    background: "#f8fafc", marginBottom: 5,
                    cursor: "pointer", border: "1px solid #f1f5f9",
                    transition: "all 0.15s",
                  }}>
                  <span style={{
                    background: nc.bg, color: nc.color,
                    padding: "2px 8px", borderRadius: 10,
                    fontSize: 11, fontWeight: 700, minWidth: 36, textAlign: "center",
                  }}>{z.score.toFixed(0)}</span>
                  <span style={{ fontWeight: 600, fontSize: 13, color: "#1e293b" }}>
                    {z.region}
                  </span>
                  <span style={{ fontSize: 11, color: "#64748b" }}>
                    {z.nb_parcelles} parcelles · {z.litiges_actifs} litiges · {z.mutations_30j} mutations/30j
                  </span>
                  <span style={{
                    marginLeft: "auto", fontSize: 10, fontWeight: 700,
                    color: nc.color,
                  }}>{nc.icon} {z.niveau}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

// ─── Onglet Tableau de bord ───────────────────────────────────────

const TBDashboard: React.FC = () => {
  const [tb,       setTb]       = useState<TableauBord | null>(null);
  const [alertes,  setAlertes]  = useState<Alerte[]>([]);
  const [parcTop,  setParcTop]  = useState<any[]>([]);
  const [loading,  setLoading]  = useState(true);

  useEffect(() => {
    Promise.all([
      fetch("/api/v1/risk/tableau-bord").then(r => r.json()),
      fetch("/api/v1/risk/alertes").then(r => r.json()),
      fetch("/api/v1/risk/parcelles-top?limite=10").then(r => r.json()),
    ]).then(([tbData, alerData, parcData]) => {
      setTb(tbData); setAlertes(alerData); setParcTop(parcData);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ padding: 24, textAlign: "center", color: "#94a3b8" }}>Chargement…</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Compteurs */}
      {tb && (
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, color: "#64748b",
                        marginBottom: 8, textTransform: "uppercase" }}>Entités analysées</div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {[
              { l: "CRITIQUE", v: tb.entites_critique, c: "#dc2626", icon: "🚨" },
              { l: "ÉLEVÉ",    v: tb.entites_eleve,    c: "#dc2626", icon: "▲" },
              { l: "MOYEN",    v: tb.entites_moyen,    c: "#b45309", icon: "⚠" },
              { l: "FAIBLE",   v: tb.entites_faible,   c: "#15803d", icon: "✓" },
            ].map(({ l, v, c, icon }) => (
              <div key={l} style={{
                flex: 1, minWidth: 100, background: "#fff",
                borderRadius: 10, padding: "12px 16px",
                border: "1px solid #e2e8f0",
              }}>
                <div style={{ fontSize: 11, color: "#64748b", fontWeight: 600, marginBottom: 4 }}>
                  {icon} {l}
                </div>
                <div style={{ fontSize: 26, fontWeight: 800, color: v > 0 ? c : "#94a3b8" }}>{v}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Alertes prédictives */}
      {alertes.length > 0 && (
        <div style={{ background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 16 }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: "#1e293b", marginBottom: 10 }}>
            🔔 Alertes prédictives actives ({alertes.length})
          </div>
          {alertes.slice(0, 5).map(a => {
            const nc = NIVEAU_CFG[a.niveau as Niveau] || NIVEAU_CFG.FAIBLE;
            return (
              <div key={a.alerte_id} style={{
                padding: "8px 12px", borderRadius: 8, marginBottom: 6,
                background: nc.bg, border: `1px solid ${nc.border}`,
                borderLeft: `4px solid ${nc.color}`,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontWeight: 700, fontSize: 12, color: nc.color }}>
                    {nc.icon} {a.niveau}
                  </span>
                  <span style={{ fontSize: 12, color: "#1e293b", flex: 1 }}>{a.titre}</span>
                  <span style={{ fontSize: 11, color: "#94a3b8" }}>
                    score {a.score.toFixed(0)}
                  </span>
                </div>
                {a.recommandation && (
                  <div style={{ fontSize: 11, color: "#64748b", marginTop: 3 }}>
                    💡 {a.recommandation}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Top parcelles */}
      {parcTop.length > 0 && (
        <div style={{ background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 16 }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: "#1e293b", marginBottom: 10 }}>
            📋 Top parcelles à risque
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "#f8fafc" }}>
                  {["NICAD","Région","Commune","Score","Niveau"].map(h => (
                    <th key={h} style={{
                      padding: "6px 10px", textAlign: "left",
                      color: "#64748b", fontWeight: 700,
                      borderBottom: "1px solid #e2e8f0",
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {parcTop.map((p: any, i: number) => {
                  const nc = NIVEAU_CFG[p.niveau as Niveau] || NIVEAU_CFG.FAIBLE;
                  return (
                    <tr key={i} style={{ borderBottom: "1px solid #f8fafc" }}>
                      <td style={{ padding: "6px 10px", fontFamily: "monospace", fontSize: 11 }}>
                        {p.nicad || p.entite_id?.slice(0,12) || "—"}
                      </td>
                      <td style={{ padding: "6px 10px", color: "#475569" }}>{p.region || "—"}</td>
                      <td style={{ padding: "6px 10px", color: "#475569" }}>{p.commune || "—"}</td>
                      <td style={{ padding: "6px 10px", fontWeight: 700, color: nc.color }}>
                        {parseFloat(p.score).toFixed(1)}
                      </td>
                      <td style={{ padding: "6px 10px" }}>
                        <span style={{
                          background: nc.bg, color: nc.color,
                          padding: "2px 8px", borderRadius: 10,
                          fontSize: 10, fontWeight: 700,
                        }}>{p.niveau}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};

// ─── Onglet Analyser ──────────────────────────────────────────────

const Analyser: React.FC = () => {
  const [type,    setType]    = useState<"parcelle"|"zone"|"utilisateur"|"operation">("parcelle");
  const [id,      setId]      = useState("");
  const [score,   setScore]   = useState<RiskScore | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string|null>(null);

  const run = async () => {
    if (!id.trim()) return;
    setLoading(true); setError(null); setScore(null);
    try {
      const res = await fetch(`/api/v1/risk/analyser/${type}/${encodeURIComponent(id)}`, {
        method: "POST"
      });
      const data = await res.json();
      if (!res.ok) setError(data.detail || "Erreur");
      else setScore(data);
    } catch(e) { setError(String(e)); }
    finally { setLoading(false); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 20 }}>
        <div style={{ fontWeight: 700, fontSize: 14, color: "#1e293b", marginBottom: 14 }}>
          🔍 Analyse à la demande
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
          {(["parcelle","zone","utilisateur","operation"] as const).map(t => (
            <button key={t} onClick={() => { setType(t); setId(""); setScore(null); }}
              style={{
                padding: "6px 16px", borderRadius: 20, border: "none",
                background: type === t ? "#2563eb" : "#f1f5f9",
                color: type === t ? "#fff" : "#64748b",
                fontSize: 12, fontWeight: 600, cursor: "pointer",
              }}>
              {t.toUpperCase()}
            </button>
          ))}
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <input value={id} onChange={e => setId(e.target.value)}
            placeholder={
              type === "parcelle" ? "UUID de la parcelle" :
              type === "zone" ? "Code région (NI-01…NI-08)" :
              type === "utilisateur" ? "UUID de l'utilisateur" :
              "Type opération (MUTATION, FUSION…)"
            }
            style={{
              flex: 1, padding: "9px 14px", borderRadius: 8,
              border: "1px solid #e2e8f0", fontSize: 13,
            }} />
          <button onClick={run} disabled={loading || !id.trim()}
            style={{
              background: loading ? "#94a3b8" : "#2563eb", color: "#fff",
              border: "none", borderRadius: 8, padding: "9px 24px",
              fontSize: 14, fontWeight: 700, cursor: "pointer",
            }}>
            {loading ? "⏳ Analyse…" : "▶ Analyser"}
          </button>
        </div>
        {error && <div style={{ marginTop: 8, color: "#dc2626", fontSize: 12 }}>❌ {error}</div>}
      </div>
      {score && <ScoreDetail score={score} onClose={() => setScore(null)} />}
    </div>
  );
};

// ─── Onglet Alertes ───────────────────────────────────────────────

const AlertesRisk: React.FC = () => {
  const [alertes, setAlertes] = useState<Alerte[]>([]);
  const [load, setLoad] = useState(true);

  const refresh = useCallback(() => {
    setLoad(true);
    fetch("/api/v1/risk/alertes/historique?limite=50")
      .then(r => r.json()).then(d => { setAlertes(d); setLoad(false); })
      .catch(() => setLoad(false));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const acquitter = async (id: string) => {
    await fetch(`/api/v1/risk/alertes/${id}/acquitter`, { method: "POST" });
    refresh();
  };

  if (load) return <div style={{ padding: 24, textAlign: "center", color: "#94a3b8" }}>Chargement…</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button onClick={refresh} style={{
          background: "#f1f5f9", border: "none", borderRadius: 8,
          padding: "6px 14px", fontSize: 12, cursor: "pointer", color: "#475569",
        }}>↻ Actualiser</button>
      </div>
      {!alertes.length && (
        <div style={{ padding: 24, textAlign: "center", color: "#15803d", fontWeight: 600 }}>
          ✓ Aucune alerte prédictive active
        </div>
      )}
      {alertes.map(a => {
        const nc = NIVEAU_CFG[a.niveau as Niveau] || NIVEAU_CFG.FAIBLE;
        return (
          <div key={a.alerte_id} style={{
            background: a.acquittee ? "#f8fafc" : nc.bg,
            borderRadius: 10, padding: "12px 16px",
            border: `1px solid ${a.acquittee ? "#e2e8f0" : nc.border}`,
            borderLeft: `4px solid ${a.acquittee ? "#e2e8f0" : nc.color}`,
            opacity: a.acquittee ? 0.7 : 1,
          }}>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  <span style={{
                    background: nc.color + "22", color: nc.color,
                    padding: "1px 8px", borderRadius: 10, fontSize: 10, fontWeight: 800,
                  }}>{a.niveau}</span>
                  <span style={{
                    background: "#f1f5f9", color: "#64748b",
                    padding: "1px 7px", borderRadius: 8, fontSize: 10,
                  }}>{a.entite_type}</span>
                  <span style={{ fontSize: 11, fontWeight: 700, color: nc.color }}>
                    score {a.score?.toFixed(0)}
                  </span>
                  <span style={{ fontSize: 10, color: "#94a3b8", marginLeft: "auto" }}>
                    {new Date(a.created_at).toLocaleString("fr-FR")}
                  </span>
                </div>
                <div style={{ fontWeight: 700, fontSize: 13, color: "#1e293b", marginBottom: 3 }}>
                  {a.titre}
                </div>
                <div style={{ fontSize: 12, color: "#475569" }}>{a.message}</div>
                {a.recommandation && (
                  <div style={{
                    background: "#f0fdf4", borderRadius: 6, padding: "4px 10px",
                    fontSize: 11, color: "#15803d", marginTop: 5,
                  }}>💡 {a.recommandation}</div>
                )}
              </div>
              {!a.acquittee && (
                <button onClick={() => acquitter(a.alerte_id)} style={{
                  background: "#fff", border: `1px solid ${nc.border}`,
                  borderRadius: 6, padding: "4px 10px",
                  fontSize: 11, cursor: "pointer", color: nc.color,
                  fontWeight: 600, whiteSpace: "nowrap",
                }}>✓ Acquitter</button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
};

// ─── Page principale ──────────────────────────────────────────────

type Tab = "carte" | "dashboard" | "analyser" | "alertes";

export default function RiskPage() {
  const [tab, setTab] = useState<Tab>("carte");

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
            background: "linear-gradient(135deg, #dc2626, #b45309)",
            display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22,
          }}>⚡</div>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: "#0f172a" }}>
              Analyse Prédictive des Risques Fonciers
            </h1>
            <p style={{ margin: 0, fontSize: 13, color: "#64748b" }}>
              6 analyseurs · Scores FAIBLE/MOYEN/ÉLEVÉ/CRITIQUE · Alertes prédictives
            </p>
          </div>
        </div>

        {/* Badges analyseurs */}
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {[
            { id: "A1", label: "Conflits zone",       c: "#dc2626" },
            { id: "A2", label: "Fréq. mutations",     c: "#b45309" },
            { id: "A3", label: "Utilisateur instable",c: "#7c3aed" },
            { id: "A4", label: "Densité mutations",   c: "#0891b2" },
            { id: "A5", label: "Historique anomalie", c: "#059669" },
            { id: "A6", label: "Opération risquée",   c: "#64748b" },
          ].map(({ id, label, c }) => (
            <span key={id} style={{
              background: c + "15", border: `1px solid ${c}44`,
              color: c, borderRadius: 8, padding: "3px 10px",
              fontSize: 11, fontWeight: 700,
            }}>
              {id} {label}
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
          { id: "carte",     label: "🗺 Carte des risques" },
          { id: "dashboard", label: "📊 Tableau de bord" },
          { id: "analyser",  label: "🔍 Analyser" },
          { id: "alertes",   label: "🔔 Alertes" },
        ] as const).map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            style={{
              padding: "8px 18px", borderRadius: 8, border: "none",
              background: tab === t.id ? "#fff" : "transparent",
              fontWeight: tab === t.id ? 700 : 400,
              color: tab === t.id ? "#dc2626" : "#64748b",
              fontSize: 13, cursor: "pointer",
              boxShadow: tab === t.id ? "0 1px 4px rgba(0,0,0,.08)" : "none",
            }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Contenu */}
      <div>
        {tab === "carte"     && <CarteRisque />}
        {tab === "dashboard" && <TBDashboard />}
        {tab === "analyser"  && <Analyser />}
        {tab === "alertes"   && <AlertesRisk />}
      </div>
    </div>
  );
}
