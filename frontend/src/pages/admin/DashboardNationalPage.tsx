/**
 * FONCIER+ ── Tableaux de Bord Décisionnels Nationaux
 *
 * Onglets :
 *   🇳🇪 Vue nationale     — carte + KPIs globaux
 *   📊 KPIs régionaux    — tableau comparatif 8 régions
 *   📈 Flux & séries      — graphiques temporels
 *   ⚖ Conflits           — litiges et taux de conflits
 *   📋 Synthèse           — rapport stratégique automatique + export
 */
import React, { useState, useEffect, useCallback } from "react";

// ARCHITECTURE v3.4.7 : données temps réel via useDataFetch
// Les fetch() inline sont conservés pour les actions (POST/PATCH/DELETE)
// Le chargement initial doit utiliser useDataFetch pour cohérence

// ─── Types ───────────────────────────────────────────────────────

interface KPI {
  code:       string;
  label:      string;
  valeur:     number;
  unite:      string;
  variation:  number | null;
  tendance:   "hausse" | "baisse" | "stable";
  alerte:     boolean;
  detail:     Record<string, any>;
}

interface ZoneData {
  code:                    string;
  nom:                     string;
  parcelles:               number;
  mutations_30j:           number;
  mutations_an:            number;
  litiges_actifs:          number;
  taux_conflit:            number;
  delai_validation_jours:  number;
  score_risque:            number;
  niveau_risque:           string;
}

interface Serie {
  label:    string;
  unite:    string;
  periodes: string[];
  valeurs:  number[];
}

interface Synthese {
  titre:             string;
  date_calcul:       string;
  periode:           string;
  indicateurs_cles:  string[];
  alertes:           string[];
  tendances:         string[];
  recommandations:   string[];
  zones_prioritaires: string[];
  score_sante:       number;
}

interface TableauBord {
  kpis:      KPI[];
  zones:     ZoneData[];
  series:    Serie[];
  synthese:  Synthese | null;
  filtres:   Record<string, string>;
  duree_ms:  number;
  calcule_a: string;
}

// ─── Constantes ──────────────────────────────────────────────────

const RISQUE_COLORS: Record<string, string> = {
  "FAIBLE":   "#22c55e",
  "MOYEN":    "#f59e0b",
  "ÉLEVÉ":    "#ef4444",
  "CRITIQUE": "#dc2626",
};

const REGIONS_COORDS: Record<string, { cx: number; cy: number }> = {
  "NI-01": { cx: 320, cy: 115 },
  "NI-02": { cx: 430, cy: 220 },
  "NI-03": { cx: 165, cy: 295 },
  "NI-04": { cx: 255, cy: 262 },
  "NI-05": { cx: 200, cy: 198 },
  "NI-06": { cx: 128, cy: 255 },
  "NI-07": { cx: 333, cy: 238 },
  "NI-08": { cx: 148, cy: 278 },
};

// ─── Composants utilitaires ───────────────────────────────────────

const KPICard: React.FC<{ kpi: KPI }> = ({ kpi }) => {
  const fmtVal = (v: number, u: string) => {
    if (u === "XOF" || v > 99999) return v.toLocaleString("fr-FR");
    if (u === "%") return `${v.toFixed(1)}%`;
    return v % 1 === 0 ? v.toLocaleString("fr-FR") : v.toFixed(1);
  };

  return (
    <div style={{
      background: "#fff", borderRadius: 10, padding: "14px 16px",
      border: `1px solid ${kpi.alerte ? "#fca5a5" : "#e2e8f0"}`,
      boxShadow: kpi.alerte ? "0 0 0 2px #fee2e222" : "none",
      flex: 1, minWidth: 140,
    }}>
      <div style={{
        fontSize: 10, fontWeight: 700, color: "#64748b",
        marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.05em",
      }}>{kpi.label}</div>
      <div style={{
        fontSize: 22, fontWeight: 800,
        color: kpi.alerte ? "#dc2626" : "#1e293b",
      }}>
        {fmtVal(kpi.valeur, kpi.unite)}
        <span style={{ fontSize: 11, fontWeight: 400, color: "#94a3b8", marginLeft: 4 }}>
          {kpi.unite !== "%" ? kpi.unite : ""}
        </span>
      </div>
      {kpi.variation !== null && (
        <div style={{
          fontSize: 11, marginTop: 3,
          color: kpi.tendance === "hausse" ? "#15803d"
               : kpi.tendance === "baisse" ? "#dc2626"
               : "#94a3b8",
        }}>
          {kpi.tendance === "hausse" ? "▲" : kpi.tendance === "baisse" ? "▼" : "→"}{" "}
          {Math.abs(kpi.variation).toFixed(1)}% vs mois préc.
        </div>
      )}
      {kpi.alerte && (
        <div style={{
          fontSize: 10, color: "#dc2626", marginTop: 4, fontWeight: 600,
        }}>⚠ Seuil dépassé</div>
      )}
    </div>
  );
};

// ─── Carte SVG nationale ──────────────────────────────────────────

const CarteNationale: React.FC<{
  zones: ZoneData[];
  selectedRegion: string | null;
  onSelect: (code: string) => void;
}> = ({ zones, selectedRegion, onSelect }) => {
  const zoneMap: Record<string, ZoneData> = {};
  zones.forEach(z => { zoneMap[z.code] = z; });

  return (
    <div style={{ position: "relative" }}>
      <svg viewBox="0 0 520 390" style={{ width: "100%", height: "auto" }}>
        <rect x="70" y="70" width="400" height="280" rx="10"
          fill="#f1f5f9" stroke="#e2e8f0" strokeWidth="1" />
        <text x="270" y="375" textAnchor="middle" fontSize="10" fill="#94a3b8">
          République du Niger — Données foncières nationales
        </text>

        {Object.entries(REGIONS_COORDS).map(([code, pos]) => {
          const z      = zoneMap[code];
          const niv    = z?.niveau_risque || "FAIBLE";
          const col    = RISQUE_COLORS[niv] || "#22c55e";
          const mut    = z?.mutations_30j || 0;
          const parc   = z?.parcelles || 0;
          const isSel  = selectedRegion === code;
          const r      = 22 + Math.min(mut / 5, 16);  // rayon ∝ activité

          return (
            <g key={code} style={{ cursor: "pointer" }} onClick={() => onSelect(code)}>
              {isSel && (
                <circle cx={pos.cx} cy={pos.cy} r={r + 9}
                  fill="none" stroke="#2563eb" strokeWidth="2.5"
                  strokeDasharray="5,3" />
              )}
              <circle cx={pos.cx} cy={pos.cy} r={r}
                fill={col + "44"} stroke={col}
                strokeWidth={isSel ? 2.5 : 1.5}
                style={{ transition: "all .2s" }} />
              {/* Nombre de mutations */}
              <text x={pos.cx} y={pos.cy - 3} textAnchor="middle"
                dominantBaseline="middle" fontSize="10" fontWeight="700" fill={col}>
                {mut}
              </text>
              <text x={pos.cx} y={pos.cy + 8} textAnchor="middle"
                fontSize="7" fill={col + "aa"}>mut/30j</text>
              {/* Nom zone */}
              <text x={pos.cx} y={pos.cy + r + 11} textAnchor="middle"
                fontSize="8.5" fontWeight="600" fill="#475569">
                {z?.nom || code}
              </text>
              <text x={pos.cx} y={pos.cy + r + 20} textAnchor="middle"
                fontSize="7" fill="#94a3b8">{parc.toLocaleString("fr-FR")} parc.</text>
            </g>
          );
        })}
      </svg>

      {/* Légende */}
      <div style={{
        position: "absolute", bottom: 16, right: 8,
        background: "#ffffffee", borderRadius: 8, padding: "8px 10px",
        border: "1px solid #e2e8f0", fontSize: 10,
      }}>
        <div style={{ fontWeight: 700, marginBottom: 4, color: "#475569" }}>Niveau risque</div>
        {["FAIBLE","MOYEN","ÉLEVÉ","CRITIQUE"].map(n => (
          <div key={n} style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 2 }}>
            <div style={{ width: 9, height: 9, borderRadius: "50%", background: RISQUE_COLORS[n] }} />
            <span style={{ color: "#475569" }}>{n}</span>
          </div>
        ))}
        <div style={{ marginTop: 4, color: "#94a3b8" }}>Taille ∝ mutations/30j</div>
      </div>
    </div>
  );
};

// ─── Mini graphique barres ────────────────────────────────────────

const MiniBarChart: React.FC<{ serie: Serie; height?: number }> = ({
  serie, height = 80,
}) => {
  if (!serie.valeurs.length) return null;
  const max = Math.max(...serie.valeurs, 1);
  const last6 = serie.valeurs.slice(-8);
  const labs6  = serie.periodes.slice(-8);

  return (
    <div>
      <div style={{ fontSize: 11, fontWeight: 600, color: "#64748b", marginBottom: 6 }}>
        {serie.label}
      </div>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height }}>
        {last6.map((v, i) => (
          <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column",
                                alignItems: "center", justifyContent: "flex-end" }}>
            <div title={`${labs6[i]}: ${v}`} style={{
              background: "linear-gradient(to top, #2563eb, #7c3aed)",
              borderRadius: "3px 3px 0 0",
              width: "100%",
              height: `${(v / max) * (height - 20)}px`,
              minHeight: 2,
              transition: "height 0.3s",
            }} />
            <div style={{ fontSize: 7, color: "#94a3b8", marginTop: 2, textAlign: "center",
                          transform: "rotate(-45deg)", transformOrigin: "top left",
                          whiteSpace: "nowrap" }}>
              {(labs6[i] || "").slice(5)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

// ─── Onglet Vue nationale ─────────────────────────────────────────

const VueNationale: React.FC<{
  tb: TableauBord;
  region: string | null;
  onRegionSelect: (r: string | null) => void;
  loading: boolean;
}> = ({ tb, region, onRegionSelect, loading }) => {
  const kpiPrio = tb.kpis.filter(k =>
    ["K01","K04","K06","K08","K09","K11","K13"].includes(k.code)
  ).slice(0, 7);

  const selZone = tb.zones.find(z => z.code === region);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* KPIs prioritaires */}
      <div>
        <div style={{ fontSize: 12, fontWeight: 700, color: "#64748b",
                      marginBottom: 8, textTransform: "uppercase" }}>
          Indicateurs clés nationaux
          {loading && <span style={{ color: "#94a3b8", fontWeight: 400, marginLeft: 8 }}>⏳ Calcul en cours…</span>}
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {kpiPrio.map(k => <KPICard key={k.code} kpi={k} />)}
        </div>
      </div>

      {/* Carte + détail */}
      <div style={{ display: "grid", gridTemplateColumns: "1.2fr 0.8fr", gap: 16 }}>
        <div style={{
          background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 16,
        }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: "#1e293b", marginBottom: 10 }}>
            🗺 Carte nationale des mutations foncières
          </div>
          <CarteNationale
            zones={tb.zones}
            selectedRegion={region}
            onSelect={r => onRegionSelect(region === r ? null : r)}
          />
        </div>

        {/* Panneau détail zone sélectionnée */}
        <div style={{ background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 16 }}>
          {selZone ? (
            <>
              <div style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                marginBottom: 12,
              }}>
                <div style={{ fontWeight: 700, fontSize: 15, color: "#1e293b" }}>
                  {selZone.nom} ({selZone.code})
                </div>
                <button onClick={() => onRegionSelect(null)} style={{
                  background: "#f1f5f9", border: "none", borderRadius: 6,
                  padding: "3px 8px", fontSize: 11, cursor: "pointer", color: "#64748b",
                }}>✕</button>
              </div>
              {[
                { l: "Parcelles enregistrées", v: selZone.parcelles.toLocaleString("fr-FR"), icon: "🏠" },
                { l: "Mutations (30 jours)",   v: selZone.mutations_30j.toString(),          icon: "🔄" },
                { l: "Mutations (12 mois)",    v: selZone.mutations_an.toLocaleString("fr-FR"), icon: "📊" },
                { l: "Litiges actifs",         v: selZone.litiges_actifs.toString(),          icon: "⚖" },
                { l: "Taux de conflits",       v: `${selZone.taux_conflit.toFixed(1)}%`,       icon: "⚠" },
                { l: "Délai validation moy.",  v: `${selZone.delai_validation_jours}j`,        icon: "⏱" },
                { l: "Score de risque",        v: `${selZone.score_risque}/100`,               icon: "🎯" },
              ].map(({ l, v, icon }) => (
                <div key={l} style={{
                  display: "flex", justifyContent: "space-between",
                  padding: "6px 0", borderBottom: "1px solid #f8fafc",
                  fontSize: 12,
                }}>
                  <span style={{ color: "#64748b" }}>{icon} {l}</span>
                  <span style={{ fontWeight: 700, color: "#1e293b" }}>{v}</span>
                </div>
              ))}
              <div style={{ marginTop: 8 }}>
                <span style={{
                  background: (RISQUE_COLORS[selZone.niveau_risque] || "#22c55e") + "22",
                  color: RISQUE_COLORS[selZone.niveau_risque] || "#22c55e",
                  padding: "3px 12px", borderRadius: 20, fontSize: 11, fontWeight: 700,
                }}>
                  Risque {selZone.niveau_risque}
                </span>
              </div>
            </>
          ) : (
            <div style={{ padding: "20px 0", textAlign: "center", color: "#94a3b8", fontSize: 13 }}>
              Cliquez sur une région pour voir le détail
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// ─── Onglet KPIs régionaux ────────────────────────────────────────

const KPIsRegionaux: React.FC<{ zones: ZoneData[] }> = ({ zones }) => {
  const [sortBy, setSortBy] = useState<keyof ZoneData>("mutations_30j");
  const sorted = [...zones].sort((a, b) =>
    (Number(b[sortBy]) || 0) - (Number(a[sortBy]) || 0)
  );

  const cols: { key: keyof ZoneData; label: string; fmt?: (v: any) => string }[] = [
    { key: "nom",                     label: "Région" },
    { key: "parcelles",               label: "Parcelles",     fmt: v => v.toLocaleString("fr-FR") },
    { key: "mutations_30j",           label: "Mutations 30j" },
    { key: "mutations_an",            label: "Mutations/an",  fmt: v => v.toLocaleString("fr-FR") },
    { key: "litiges_actifs",          label: "Litiges actifs" },
    { key: "taux_conflit",            label: "Taux conflit",  fmt: v => `${v.toFixed(1)}%` },
    { key: "delai_validation_jours",  label: "Délai valid.",  fmt: v => `${v}j` },
    { key: "score_risque",            label: "Score risque",  fmt: v => `${v}/100` },
    { key: "niveau_risque",           label: "Niveau" },
  ];

  return (
    <div style={{
      background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 16,
    }}>
      <div style={{ fontWeight: 700, fontSize: 14, color: "#1e293b", marginBottom: 14 }}>
        Comparatif KPIs — 8 régions du Niger
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ background: "#f8fafc" }}>
              {cols.map(c => (
                <th key={String(c.key)}
                  onClick={() => c.key !== "nom" && c.key !== "niveau_risque" && setSortBy(c.key)}
                  style={{
                    padding: "8px 10px", textAlign: c.key === "nom" ? "left" : "right",
                    color: sortBy === c.key ? "#2563eb" : "#64748b",
                    fontWeight: 700, borderBottom: "2px solid #e2e8f0",
                    cursor: c.key !== "nom" && c.key !== "niveau_risque" ? "pointer" : "default",
                    whiteSpace: "nowrap",
                  }}>
                  {c.label}{sortBy === c.key ? " ↓" : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((z, i) => (
              <tr key={z.code} style={{
                borderBottom: "1px solid #f1f5f9",
                background: i % 2 === 0 ? "#fff" : "#fafafa",
              }}>
                {cols.map(c => {
                  const v = z[c.key];
                  const disp = c.fmt ? c.fmt(v) : String(v);
                  if (c.key === "niveau_risque") {
                    return (
                      <td key={String(c.key)} style={{ padding: "7px 10px", textAlign: "center" }}>
                        <span style={{
                          background: (RISQUE_COLORS[String(v)] || "#22c55e") + "22",
                          color: RISQUE_COLORS[String(v)] || "#22c55e",
                          padding: "2px 8px", borderRadius: 10,
                          fontSize: 10, fontWeight: 700,
                        }}>{v}</span>
                      </td>
                    );
                  }
                  const isHigh = c.key === "taux_conflit" && Number(v) > 5;
                  const isWarn = c.key === "delai_validation_jours" && Number(v) > 20;
                  return (
                    <td key={String(c.key)} style={{
                      padding: "7px 10px",
                      textAlign: c.key === "nom" ? "left" : "right",
                      fontWeight: sortBy === c.key ? 700 : 400,
                      color: isHigh || isWarn ? "#dc2626" : "#1e293b",
                    }}>
                      {disp}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

// ─── Onglet Flux & séries ─────────────────────────────────────────

const FluxSeries: React.FC<{ series: Serie[] }> = ({ series }) => (
  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
    {series.map((s, i) => (
      <div key={i} style={{
        background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 16,
      }}>
        <MiniBarChart serie={s} height={100} />
        <div style={{
          display: "flex", justifyContent: "space-between", marginTop: 8,
          fontSize: 11, color: "#94a3b8",
        }}>
          <span>
            Total : {s.valeurs.reduce((a, b) => a + b, 0).toLocaleString("fr-FR")} {s.unite}
          </span>
          <span>
            Moy. : {s.valeurs.length
              ? (s.valeurs.reduce((a, b) => a + b, 0) / s.valeurs.length).toFixed(1)
              : 0} {s.unite}
          </span>
        </div>
      </div>
    ))}
    {!series.length && (
      <div style={{ gridColumn: "span 2", textAlign: "center", padding: 32, color: "#94a3b8" }}>
        Données de séries non disponibles
      </div>
    )}
  </div>
);

// ─── Onglet Conflits ──────────────────────────────────────────────

const Conflits: React.FC<{ zones: ZoneData[] }> = ({ zones }) => {
  const sorted = [...zones].sort((a, b) => b.litiges_actifs - a.litiges_actifs);
  const maxLit = Math.max(...zones.map(z => z.litiges_actifs), 1);

  return (
    <div style={{ background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 16 }}>
      <div style={{ fontWeight: 700, fontSize: 14, color: "#1e293b", marginBottom: 14 }}>
        ⚖ Conflits fonciers par région
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {sorted.map(z => {
          const pct = z.litiges_actifs / maxLit * 100;
          const isHigh = z.taux_conflit > 5;
          return (
            <div key={z.code} style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{ width: 90, fontSize: 12, fontWeight: 600, color: "#1e293b" }}>
                {z.nom}
              </div>
              <div style={{ flex: 1 }}>
                <div style={{
                  height: 18, background: "#f1f5f9", borderRadius: 4, overflow: "hidden",
                }}>
                  <div style={{
                    height: "100%", borderRadius: 4,
                    background: isHigh
                      ? "linear-gradient(90deg, #dc2626, #b91c1c)"
                      : "linear-gradient(90deg, #2563eb, #7c3aed)",
                    width: `${pct}%`, transition: "width 0.5s",
                    display: "flex", alignItems: "center",
                    paddingLeft: 6,
                  }}>
                    {pct > 15 && (
                      <span style={{ fontSize: 10, color: "#fff", fontWeight: 700 }}>
                        {z.litiges_actifs}
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <div style={{ width: 60, fontSize: 12, textAlign: "right",
                            fontWeight: 700, color: isHigh ? "#dc2626" : "#1e293b" }}>
                {z.litiges_actifs} lit.
              </div>
              <div style={{ width: 55, fontSize: 11, textAlign: "right",
                            color: isHigh ? "#dc2626" : "#64748b" }}>
                {z.taux_conflit.toFixed(1)}%
              </div>
              {isHigh && (
                <div style={{ fontSize: 12 }} title="Taux > 5%">⚠</div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

// ─── Onglet Synthèse stratégique ──────────────────────────────────

const SyntheseStrategique: React.FC<{
  synthese: Synthese | null;
  duree_ms: number;
  calcule_a: string;
  onExportCsv: () => void;
  onExportJson: () => void;
}> = ({ synthese, duree_ms, calcule_a, onExportCsv, onExportJson }) => {
  if (!synthese) return (
    <div style={{ padding: 32, textAlign: "center", color: "#94a3b8" }}>
      Synthèse non disponible
    </div>
  );

  const santeColor = synthese.score_sante >= 80 ? "#15803d"
                   : synthese.score_sante >= 60 ? "#b45309"
                   : "#dc2626";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Entête synthèse */}
      <div style={{
        background: "linear-gradient(135deg, #1e293b, #374151)",
        borderRadius: 12, padding: 20, color: "#fff",
      }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 800, marginBottom: 4 }}>
              {synthese.titre}
            </div>
            <div style={{ fontSize: 12, color: "#94a3b8" }}>
              {synthese.periode} · Calculé le {new Date(synthese.date_calcul).toLocaleString("fr-FR")}
              · {duree_ms}ms
            </div>
          </div>
          <div style={{ textAlign: "center" }}>
            <div style={{
              fontSize: 36, fontWeight: 900, color: santeColor,
              background: santeColor + "22", borderRadius: "50%",
              width: 64, height: 64, display: "flex", alignItems: "center",
              justifyContent: "center",
            }}>
              {synthese.score_sante.toFixed(0)}
            </div>
            <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 2 }}>Score santé/100</div>
          </div>
        </div>

        {/* Boutons export */}
        <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
          <button onClick={onExportCsv} style={{
            background: "#2563eb", color: "#fff", border: "none",
            borderRadius: 8, padding: "7px 18px", fontSize: 12,
            fontWeight: 700, cursor: "pointer",
          }}>
            ⬇ Export CSV
          </button>
          <button onClick={onExportJson} style={{
            background: "#475569", color: "#fff", border: "none",
            borderRadius: 8, padding: "7px 18px", fontSize: 12,
            fontWeight: 700, cursor: "pointer",
          }}>
            ⬇ Export JSON
          </button>
        </div>
      </div>

      {/* Sections */}
      {[
        { title: "📊 Indicateurs clés", items: synthese.indicateurs_cles, color: "#2563eb" },
        { title: "🚨 Alertes actives",  items: synthese.alertes,           color: "#dc2626" },
        { title: "📈 Tendances",         items: synthese.tendances,          color: "#7c3aed" },
        { title: "💡 Recommandations",   items: synthese.recommandations,    color: "#059669" },
        { title: "🎯 Zones prioritaires",items: synthese.zones_prioritaires, color: "#b45309" },
      ].filter(s => s.items.length > 0).map(({ title, items, color }) => (
        <div key={title} style={{
          background: "#fff", borderRadius: 12,
          border: `1px solid ${color}22`, padding: 16,
        }}>
          <div style={{
            fontWeight: 700, fontSize: 13, color, marginBottom: 10,
          }}>{title}</div>
          {items.map((item, i) => (
            <div key={i} style={{
              padding: "7px 12px", borderRadius: 7,
              background: color + "0a",
              marginBottom: 5, fontSize: 12, color: "#374151",
              borderLeft: `3px solid ${color}66`,
            }}>
              {item}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
};

// ─── Page principale ──────────────────────────────────────────────

type Tab = "national" | "regions" | "series" | "conflits" | "synthese";

export default function DashboardNationalPage() {
  const [tab,    setTab]    = useState<Tab>("national");
  const [tb,     setTb]     = useState<TableauBord | null>(null);
  const [loading, setLoading]= useState(true);
  const [region, setRegion] = useState<string | null>(null);
  const [nbMois, setNbMois] = useState(12);

  const load = useCallback(() => {
    setLoading(true);
    const params = new URLSearchParams({ nb_mois: String(nbMois) });
    if (region) params.set("region", region);
    fetch(`/api/v1/dashboard/national?${params}`)
      .then(r => r.json())
      .then(d => { setTb(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [region, nbMois]);

  useEffect(() => { load(); }, [load]);

  const handleExportCsv = async () => {
    const params = region ? `?region=${region}` : "";
    const res = await fetch(`/api/v1/dashboard/export/csv${params}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `foncier_dashboard_${region || "national"}_${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
  };

  const handleExportJson = async () => {
    const params = region ? `?region=${region}` : "";
    const res = await fetch(`/api/v1/dashboard/export/json${params}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `foncier_rapport_${region || "national"}_${new Date().toISOString().slice(0,10)}.json`;
    a.click();
  };

  const tabs: { id: Tab; label: string }[] = [
    { id: "national",  label: "🇳🇪 Vue nationale" },
    { id: "regions",   label: "📊 KPIs régionaux" },
    { id: "series",    label: "📈 Flux & séries" },
    { id: "conflits",  label: "⚖ Conflits" },
    { id: "synthese",  label: "📋 Synthèse & export" },
  ];

  const alertesKpi = tb?.kpis.filter(k => k.alerte).length || 0;

  return (
    <div style={{
      padding: "24px 28px", background: "#f8fafc", minHeight: "100vh",
      fontFamily: "system-ui, sans-serif",
    }}>
      {/* En-tête */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 12 }}>
          <div style={{
            width: 48, height: 48, borderRadius: 12,
            background: "linear-gradient(135deg, #1e293b, #2563eb)",
            display: "flex", alignItems: "center", justifyContent: "center", fontSize: 24,
          }}>🇳🇪</div>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: "#0f172a" }}>
              Tableau de Bord Décisionnel National
            </h1>
            <p style={{ margin: 0, fontSize: 13, color: "#64748b" }}>
              République du Niger · Ministère des Domaines et du Foncier ·{" "}
              {tb ? `${tb.kpis.length} KPIs · ${tb.duree_ms}ms` : "Chargement…"}
            </p>
          </div>

          {/* Filtres */}
          <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
            <select value={nbMois} onChange={e => setNbMois(Number(e.target.value))}
              style={{ padding: "6px 10px", borderRadius: 8, border: "1px solid #e2e8f0",
                       fontSize: 12, background: "#fff" }}>
              {[3, 6, 12, 24].map(n => (
                <option key={n} value={n}>{n} mois</option>
              ))}
            </select>
            {region && (
              <button onClick={() => setRegion(null)} style={{
                background: "#2563eb", color: "#fff", border: "none",
                borderRadius: 8, padding: "6px 12px", fontSize: 12,
                cursor: "pointer", fontWeight: 600,
              }}>
                {region} ✕
              </button>
            )}
            <button onClick={load} style={{
              background: "#f1f5f9", border: "1px solid #e2e8f0",
              borderRadius: 8, padding: "6px 12px", fontSize: 12,
              cursor: "pointer", color: "#475569",
            }}>↻ Actualiser</button>
          </div>
        </div>

        {/* Alertes banner */}
        {alertesKpi > 0 && (
          <div style={{
            background: "#fee2e2", borderRadius: 10, padding: "8px 14px",
            border: "1px solid #fca5a5", fontSize: 12, color: "#dc2626", fontWeight: 600,
          }}>
            ⚠ {alertesKpi} indicateur(s) en alerte — révision recommandée
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
            }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Contenu */}
      {!tb && loading ? (
        <div style={{ padding: 48, textAlign: "center", color: "#94a3b8", fontSize: 14 }}>
          ⏳ Calcul des indicateurs en cours…
        </div>
      ) : tb ? (
        <>
          {tab === "national" && (
            <VueNationale tb={tb} region={region} onRegionSelect={setRegion} loading={loading} />
          )}
          {tab === "regions"  && <KPIsRegionaux zones={tb.zones} />}
          {tab === "series"   && <FluxSeries series={tb.series} />}
          {tab === "conflits" && <Conflits zones={tb.zones} />}
          {tab === "synthese" && (
            <SyntheseStrategique
              synthese={tb.synthese}
              duree_ms={tb.duree_ms}
              calcule_a={tb.calcule_a}
              onExportCsv={handleExportCsv}
              onExportJson={handleExportJson}
            />
          )}
        </>
      ) : (
        <div style={{ padding: 32, textAlign: "center", color: "#dc2626", fontSize: 13 }}>
          Erreur de chargement du tableau de bord
        </div>
      )}
    </div>
  );
}
