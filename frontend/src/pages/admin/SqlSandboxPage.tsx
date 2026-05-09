/**
 * FONCIER+ ── SQL Sandbox Admin
 * Visualisation interactive du pipeline de validation 4 couches.
 *
 * Le composant PipelineDiagram reproduit fidèlement le diagramme
 * technique officiel avec animation du flux lors de la validation.
 */
import React, { useState, useEffect, useCallback, useRef } from "react";

// ARCHITECTURE v3.4.7 : données temps réel via useDataFetch
// Les fetch() inline sont conservés pour les actions (POST/PATCH/DELETE)
// Le chargement initial doit utiliser useDataFetch pour cohérence

// ─── Types ───────────────────────────────────────────────────────

type LayerStatut = "IDLE" | "RUNNING" | "PASS" | "FAIL" | "SKIP" | "WARN";
type ValidationStatus = "VALIDE" | "INVALIDE" | "REFUSE" | null;

interface LayerTrace {
  couche:   number;
  nom:      string;
  statut:   string;
  duree_ms: number;
  details:  string[];
}
interface ValidationError { code: string; message: string; couche: number }
interface LogicConflict {
  heuristique:  string;
  niveau:       "CRITIQUE" | "MAJEUR" | "MINEUR";
  regle_cible:  string;
  message:      string;
  details:      string;
  suggestion:   string;
}
interface LogicReport {
  recommandation:   string;
  bloque:           boolean;
  nb_critique:      number;
  nb_majeur:        number;
  nb_mineur:        number;
  regles_impactees: string[];
  regles_analysees: number;
  duree_ms:         number;
  conflits:         LogicConflict[];
}
interface ValidationResult {
  status:        ValidationStatus;
  valide:        boolean;
  errors:        ValidationError[];
  warnings:      string[];
  bypass_alerts: string[];
  sha256_regle:  string;
  duree_ms:      number;
  safe_sql:      string | null;
  layers:        LayerTrace[];
  logic_report:  LogicReport | null;
}
interface SandboxSante {
  validations_ok_24h:      number;
  validations_ko_24h:      number;
  validations_refuse_24h:  number;
  duree_moy_ms:            number;
  nb_non_validees:         number;
  nb_bypass_24h:           number;
  regles_validees_7j:      number;
}

// ─── Constantes ──────────────────────────────────────────────────

const DOMAINES = [
  "GENERAL","CCFM","MUTATION","RNAF","BGU",
  "JUSTICE","HYPOTHEQUE","SUCCESSION","URBANISME",
];

const LAYER_DEFS = [
  {
    id: 1, label: "COUCHE 1", titre: "Validation statique",
    subtitle: "Python pur · 0 dépendance",
    couleur: "#ea580c", bg: "#fff7ed", border: "#fed7aa",
    icon: "⚙",
    puces: [
      "Tokenisation SQL + détection 40+ mots-clés interdits",
      "DROP, DELETE, INSERT, UPDATE, TRUNCATE, EXECUTE…",
      "Commentaires (-- et /* */), dollar-quoting ($$)",
      "Multi-instructions (;), UNION, INTERSECT, INTO",
      "Fonctions dangereuses : pg_sleep, dblink, pg_read_file",
      "Profondeur d'imbrication max 6",
    ],
    resultat: "OK | REFUSE immédiat",
  },
  {
    id: 2, label: "COUCHE 2", titre: "Sémantique foncière",
    subtitle: "Logique métier Niger",
    couleur: "#7c3aed", bg: "#faf5ff", border: "#ddd6fe",
    icon: "🏛",
    puces: [
      "Whitelist de 30 tables autorisées",
      "Détection de pg_shadow, pg_catalog, information_schema",
      "Vérification présence paramètres $1 / $2",
      "Cohérence domaine / tables référencées",
      "Heuristique expression booléenne",
    ],
    resultat: "OK | INVALIDE avec détail",
  },
  {
    id: 3, label: "COUCHE 3", titre: "Sandbox PostgreSQL",
    subtitle: "Si DB disponible",
    couleur: "#0891b2", bg: "#ecfeff", border: "#a5f3fc",
    icon: "🔐",
    puces: [
      "SAVEPOINT + EXPLAIN (pas d'exécution réelle)",
      "SET LOCAL default_transaction_read_only = ON",
      "Timeout 2 secondes strict",
      "Détection tentative d'écriture → REFUSE",
    ],
    resultat: "OK | INVALIDE / REFUSE",
  },
  {
    id: 4, label: "COUCHE 4", titre: "Normalisation + Signature SHA-256",
    subtitle: "Non-répudiation",
    couleur: "#059669", bg: "#f0fdf4", border: "#bbf7d0",
    icon: "🔏",
    puces: [
      "Normalisation des espaces",
      "SHA-256(sql | domaine | code | niveau | user | date)",
      "Non-répudiation : qui a validé quoi et quand",
    ],
    resultat: "safe_sql + sha256_regle",
  },
  {
    id: 5, label: "COUCHE 5", titre: "Validation logique",
    subtitle: "Cohérence inter-règles",
    couleur: "#dc2626", bg: "#fff5f5", border: "#fecaca",
    icon: "🧠",
    puces: [
      "H1 Détection de doublons (similarité Jaccard ≥ 85 %)",
      "H2 Contradictions EXISTS / NOT EXISTS",
      "H3 Conflits entre règles BLOQUANT du même domaine",
      "H4 Subsomption logique (règle redondante)",
      "H5 Impossibilité totale (combinaison toujours-KO)",
      "HG Cohérence globale domaine + collision GENERAL",
    ],
    resultat: "AUTORISER | CORRIGER | REFUSER",
  },
];

const EXEMPLES = [
  {
    label: "EXISTS CCFM", domaine: "CCFM",
    sql: "EXISTS (SELECT 1 FROM demandes_ccfm dc WHERE dc.etat IN ('SIGNE_DIRECTEUR','ARCHIVE') AND dc.nicad_code IN (SELECT nicad FROM parcelles WHERE id=$1))",
  },
  {
    label: "NOT EXISTS gel", domaine: "GENERAL",
    sql: "NOT EXISTS (SELECT 1 FROM parcelles p WHERE p.id=$1 AND p.is_gele=TRUE)",
  },
  {
    label: "Agent autorité", domaine: "GENERAL",
    sql: "EXISTS (SELECT 1 FROM agent_profil ap WHERE ap.user_id=$2 AND ap.niveau_autorite >= 2)",
  },
  {
    label: "Pas de verrou", domaine: "GENERAL",
    sql: "NOT EXISTS (SELECT 1 FROM entity_lock el WHERE el.entite_id=$1 AND el.actif=TRUE)",
  },
  {
    label: "⚠ DROP injection", domaine: "GENERAL",
    sql: "EXISTS (SELECT 1 FROM users); DROP TABLE users; --",
  },
  {
    label: "⚠ pg_shadow", domaine: "GENERAL",
    sql: "EXISTS (SELECT 1 FROM pg_shadow WHERE usename=$2)",
  },
];

// ─── Utilitaires couleurs ─────────────────────────────────────────

function statutColor(s: LayerStatut | string): { text: string; bg: string; border: string } {
  switch (s) {
    case "PASS": return { text: "#15803d", bg: "#dcfce7", border: "#86efac" };
    case "FAIL": return { text: "#dc2626", bg: "#fee2e2", border: "#fca5a5" };
    case "WARN": return { text: "#b45309", bg: "#fef3c7", border: "#fde68a" };
    case "SKIP": return { text: "#6b7280", bg: "#f3f4f6", border: "#e5e7eb" };
    case "RUNNING": return { text: "#2563eb", bg: "#eff6ff", border: "#93c5fd" };
    default:    return { text: "#9ca3af", bg: "#f9fafb", border: "#e5e7eb" };
  }
}

function statusFinal(s: ValidationStatus): { label: string; color: string; bg: string } {
  switch (s) {
    case "VALIDE":   return { label: "✓ VALIDE",   color: "#15803d", bg: "#dcfce7" };
    case "INVALIDE": return { label: "⚠ INVALIDE", color: "#b45309", bg: "#fef3c7" };
    case "REFUSE":   return { label: "✗ REFUSÉE",  color: "#dc2626", bg: "#fee2e2" };
    default:         return { label: "—",           color: "#9ca3af", bg: "#f9fafb" };
  }
}

// ─── PipelineDiagram ─────────────────────────────────────────────

interface PipelineProps {
  layerStates: Record<number, LayerStatut>;
  layerTraces: LayerTrace[];
  finalStatus: ValidationStatus;
  running:     boolean;
}

const PipelineDiagram: React.FC<PipelineProps> = ({
  layerStates, layerTraces, finalStatus, running,
}) => {
  const getTrace = (id: number) =>
    layerTraces.find(l => l.couche === id);

  return (
    <div style={{ fontFamily: "system-ui, sans-serif" }}>

      {/* En-tête requête */}
      <div style={{
        background: "#1e293b", color: "#f1f5f9",
        borderRadius: "10px 10px 0 0", padding: "12px 20px",
        fontSize: 13, fontWeight: 600, letterSpacing: "0.3px",
        display: "flex", alignItems: "center", gap: 10,
      }}>
        <span style={{
          background: "#3b82f6", padding: "2px 10px",
          borderRadius: 6, fontSize: 11, fontWeight: 700,
        }}>POST</span>
        <span>/admin/regles-metier</span>
        {running && (
          <span style={{
            marginLeft: "auto", fontSize: 11, color: "#93c5fd",
            animation: "pulse 1s infinite",
          }}>● Validation en cours…</span>
        )}
      </div>

      {/* Flèche d'entrée */}
      <div style={{ display: "flex", justifyContent: "center", padding: "4px 0" }}>
        <Arrow active={running || finalStatus !== null} />
      </div>

      {/* Couches */}
      {LAYER_DEFS.map((def, idx) => {
        const state  = layerStates[def.id] ?? "IDLE";
        const trace  = getTrace(def.id);
        const sc     = statutColor(state);
        const isLast = idx === LAYER_DEFS.length - 1;

        return (
          <React.Fragment key={def.id}>
            <LayerBox
              def={def}
              state={state}
              sc={sc}
              trace={trace}
            />
            {!isLast && (
              <ConnectorArrow
                active={state === "PASS" || state === "WARN"}
                label="si OK"
              />
            )}
          </React.Fragment>
        );
      })}

      {/* Résultat final */}
      {finalStatus && (
        <>
          <div style={{ display: "flex", justifyContent: "center", padding: "4px 0" }}>
            <Arrow active color={statusFinal(finalStatus).color} />
          </div>
          <FinalResult status={finalStatus} />
        </>
      )}
    </div>
  );
};

// ─── LayerBox ────────────────────────────────────────────────────

const LayerBox: React.FC<{
  def: typeof LAYER_DEFS[0];
  state: LayerStatut;
  sc: ReturnType<typeof statutColor>;
  trace?: LayerTrace;
}> = ({ def, state, sc, trace }) => {
  const [expanded, setExpanded] = useState(false);

  // Auto-expand quand une couche a des détails
  useEffect(() => {
    if ((state === "FAIL" || state === "WARN") && trace?.details?.length) {
      setExpanded(true);
    }
  }, [state, trace]);

  return (
    <div style={{
      border: `2px solid ${state !== "IDLE" ? sc.border : def.border}`,
      borderRadius: 10,
      margin: "0 0 0 0",
      background: state !== "IDLE" ? sc.bg : def.bg,
      transition: "all 0.3s ease",
      overflow: "hidden",
      boxShadow: state === "RUNNING"
        ? `0 0 0 3px ${def.couleur}44`
        : state === "FAIL"
        ? "0 2px 8px rgba(220,38,38,.2)"
        : "none",
    }}>
      {/* Header couche */}
      <div
        onClick={() => setExpanded(e => !e)}
        style={{
          padding: "12px 16px",
          cursor: "pointer",
          display: "flex",
          alignItems: "flex-start",
          gap: 12,
        }}
      >
        {/* Badge couche */}
        <div style={{
          background: def.couleur,
          color: "#fff",
          borderRadius: 8,
          padding: "4px 10px",
          fontSize: 11,
          fontWeight: 800,
          whiteSpace: "nowrap",
          flexShrink: 0,
        }}>
          {def.icon} {def.label}
        </div>

        {/* Titre + puces */}
        <div style={{ flex: 1 }}>
          <div style={{
            fontWeight: 700, fontSize: 14, color: "#1e293b", marginBottom: 2,
          }}>
            {def.titre}
            <span style={{
              fontWeight: 400, fontSize: 11, color: "#64748b", marginLeft: 8,
            }}>
              {def.subtitle}
            </span>
          </div>

          {/* Puces — toujours visibles */}
          <div style={{ marginTop: 4 }}>
            {def.puces.map((p, i) => (
              <div key={i} style={{
                fontSize: 12, color: "#475569",
                marginBottom: 2,
                display: "flex", alignItems: "flex-start", gap: 6,
              }}>
                <span style={{ color: def.couleur, flexShrink: 0 }}>•</span>
                <span>{p}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Statut + durée */}
        <div style={{
          display: "flex", flexDirection: "column", alignItems: "flex-end",
          gap: 4, flexShrink: 0,
        }}>
          <StatutBadge state={state} couleur={def.couleur} />
          {trace && trace.duree_ms > 0 && (
            <span style={{ fontSize: 11, color: "#9ca3af" }}>
              {trace.duree_ms} ms
            </span>
          )}
        </div>
      </div>

      {/* Résultat attendu */}
      <div style={{
        padding: "4px 16px 10px",
        fontSize: 11, color: "#94a3b8",
        fontStyle: "italic",
      }}>
        → Résultat : {def.resultat}
      </div>

      {/* Détails trace (expandable) */}
      {trace?.details && trace.details.length > 0 && expanded && (
        <div style={{
          borderTop: `1px solid ${sc.border}`,
          padding: "10px 16px",
          background: state === "FAIL" ? "#fff5f5" : "#f8fafc",
        }}>
          <div style={{
            fontSize: 11, fontWeight: 700,
            color: state === "FAIL" ? "#dc2626" : "#64748b",
            marginBottom: 6,
            textTransform: "uppercase", letterSpacing: "0.5px",
          }}>
            {state === "FAIL" ? "✗ Erreurs détectées" : "ℹ Détails"}
          </div>
          {trace.details.map((d, i) => (
            <div key={i} style={{
              fontSize: 12, color: state === "FAIL" ? "#b91c1c" : "#475569",
              padding: "3px 0",
              borderBottom: i < trace.details.length - 1
                ? "1px solid #f1f5f9" : "none",
            }}>
              {state === "FAIL" ? "✗" : "›"} {d}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ─── StatutBadge ─────────────────────────────────────────────────

const StatutBadge: React.FC<{ state: LayerStatut; couleur: string }> = ({ state, couleur }) => {
  const configs: Record<LayerStatut, { label: string; bg: string; color: string }> = {
    IDLE:    { label: "EN ATTENTE", bg: "#f1f5f9", color: "#94a3b8" },
    RUNNING: { label: "▶ EN COURS", bg: "#eff6ff", color: "#2563eb" },
    PASS:    { label: "✓ OK",        bg: "#dcfce7", color: "#15803d" },
    FAIL:    { label: "✗ ÉCHOUÉ",   bg: "#fee2e2", color: "#dc2626" },
    WARN:    { label: "⚠ WARNING",  bg: "#fef3c7", color: "#b45309" },
    SKIP:    { label: "⊘ IGNORÉ",   bg: "#f1f5f9", color: "#9ca3af" },
  };
  const c = configs[state];
  return (
    <span style={{
      background: c.bg, color: c.color,
      padding: "3px 10px", borderRadius: 20,
      fontSize: 11, fontWeight: 700,
      border: `1px solid ${c.color}44`,
    }}>
      {c.label}
    </span>
  );
};

// ─── Flèches connecteurs ──────────────────────────────────────────

const Arrow: React.FC<{ active?: boolean; color?: string }> = ({
  active = false, color = "#94a3b8",
}) => (
  <div style={{
    display: "flex", flexDirection: "column", alignItems: "center",
    color: active ? (color || "#3b82f6") : "#cbd5e1",
    transition: "color 0.3s",
  }}>
    <div style={{ width: 2, height: 16, background: "currentColor" }} />
    <div style={{
      width: 0, height: 0,
      borderLeft: "6px solid transparent",
      borderRight: "6px solid transparent",
      borderTop: "8px solid currentColor",
    }} />
  </div>
);

const ConnectorArrow: React.FC<{ active?: boolean; label?: string }> = ({
  active = false, label,
}) => (
  <div style={{
    display: "flex", alignItems: "center", padding: "0 24px",
    color: active ? "#94a3b8" : "#cbd5e1",
    transition: "color 0.3s",
    fontSize: 11,
  }}>
    <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center" }}>
      {label && (
        <span style={{
          color: active ? "#64748b" : "#cbd5e1",
          marginBottom: 2, fontSize: 10, fontStyle: "italic",
        }}>
          {label}
        </span>
      )}
      <Arrow active={active} />
    </div>
  </div>
);

// ─── Résultat final ───────────────────────────────────────────────

const FinalResult: React.FC<{ status: ValidationStatus }> = ({ status }) => {
  const f = statusFinal(status);
  return (
    <div style={{
      border: `2px solid ${f.color}`,
      borderRadius: "0 0 10px 10px",
      background: f.bg,
      padding: "14px 20px",
      display: "flex",
      alignItems: "center",
      gap: 12,
    }}>
      <span style={{
        fontSize: 20, fontWeight: 900, color: f.color,
      }}>{f.label}</span>
      {status === "VALIDE" && (
        <span style={{ fontSize: 12, color: "#64748b" }}>
          Règle prête pour activation via
          <code style={{ background: "#f1f5f9", padding: "1px 6px",
                         borderRadius: 4, margin: "0 4px", fontSize: 11 }}>
            PATCH /regles-metier/&#123;id&#125;/activer
          </code>
        </span>
      )}
    </div>
  );
};

// ─── Éditeur SQL ──────────────────────────────────────────────────

interface EditorProps {
  onResult: (r: ValidationResult | null, layers: LayerTrace[]) => void;
}

const SqlEditor: React.FC<EditorProps> = ({ onResult }) => {
  const [sql,     setSql]     = useState("");
  const [domaine, setDomaine] = useState("GENERAL");
  const [code,    setCode]    = useState("MA_REGLE");
  const [niveau,  setNiveau]  = useState("BLOQUANT");
  const [mode,    setMode]    = useState<"STATIQUE" | "COMPLET">("COMPLET");
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  const validate = async () => {
    if (!sql.trim()) return;
    setLoading(true); setError(null);
    onResult(null, []);
    try {
      const res = await fetch("/api/v1/admin/regles-metier/valider", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql, domaine, code, niveau_blocage: niveau, mode }),
      });
      const data = await res.json();
      if (!res.ok) {
        const detail = data.detail || data;
        const fakeLayers: LayerTrace[] = (detail.layers || []).map((l: any) => ({
          couche: l.couche, nom: l.nom, statut: l.statut, duree_ms: 0, details: [],
        }));
        onResult({
          status: "REFUSE",
          valide: false,
          errors: detail.errors || [],
          warnings: [], bypass_alerts: [], sha256_regle: "",
          duree_ms: 0, safe_sql: null,
          layers: fakeLayers,
        }, fakeLayers);
      } else {
        onResult(data, data.layers || []);
      }
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
      <div style={{
        fontSize: 13, fontWeight: 700, color: "#1e293b",
        marginBottom: 12, display: "flex", alignItems: "center", gap: 8,
      }}>
        📝 Condition SQL à valider
      </div>

      {/* Exemples */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12 }}>
        {EXEMPLES.map(ex => (
          <button key={ex.label} onClick={() => {
            setSql(ex.sql); setDomaine(ex.domaine);
            setCode("EX_" + ex.label.replace(/[^A-Z0-9]/gi, "_").toUpperCase());
          }} style={{
            fontSize: 11, padding: "3px 10px", borderRadius: 8,
            background: ex.label.startsWith("⚠") ? "#fee2e2" : "#f1f5f9",
            border: "1px solid #e2e8f0", cursor: "pointer",
            color: ex.label.startsWith("⚠") ? "#b91c1c" : "#475569",
            fontWeight: ex.label.startsWith("⚠") ? 600 : 400,
          }}>
            {ex.label}
          </button>
        ))}
      </div>

      {/* Textarea SQL */}
      <textarea
        value={sql}
        onChange={e => setSql(e.target.value)}
        placeholder="EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND is_gele=FALSE)"
        rows={4}
        style={{
          width: "100%", boxSizing: "border-box",
          fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
          fontSize: 13, lineHeight: 1.6,
          border: "1px solid #e2e8f0", borderRadius: 8,
          padding: "10px 14px", background: "#f8fafc",
          resize: "vertical", outline: "none",
          color: "#1e293b",
        }}
      />
      <div style={{
        fontSize: 11, color: sql.length > 1800 ? "#dc2626" : "#94a3b8",
        textAlign: "right", marginBottom: 12,
      }}>
        {sql.length} / 2000
      </div>

      {/* Paramètres */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
        {[
          { label: "Domaine", value: domaine, set: setDomaine,
            options: DOMAINES.map(d => ({ v: d, l: d })) },
          { label: "Niveau", value: niveau, set: setNiveau,
            options: ["BLOQUANT","AVERTISSEMENT","INFO"].map(n => ({ v: n, l: n })) },
          { label: "Mode validation", value: mode, set: setMode as any,
            options: [
              { v: "STATIQUE", l: "STATIQUE (C1+C2+C4)" },
              { v: "COMPLET",  l: "COMPLET (C1+C2+C3+C4)" },
            ]},
        ].map(({ label, value, set, options }) => (
          <div key={label} style={{ flex: 1, minWidth: 130 }}>
            <label style={{ fontSize: 11, color: "#64748b",
                            display: "block", marginBottom: 4, fontWeight: 600 }}>
              {label}
            </label>
            <select value={value} onChange={e => set(e.target.value as any)}
              style={{
                width: "100%", padding: "7px 10px", borderRadius: 8,
                border: "1px solid #e2e8f0", fontSize: 13,
                background: "#fff", color: "#1e293b",
              }}>
              {options.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
            </select>
          </div>
        ))}
        <div style={{ flex: 1, minWidth: 130 }}>
          <label style={{ fontSize: 11, color: "#64748b",
                          display: "block", marginBottom: 4, fontWeight: 600 }}>
            Code règle
          </label>
          <input value={code} onChange={e => setCode(e.target.value)}
            style={{
              width: "100%", boxSizing: "border-box",
              padding: "7px 10px", borderRadius: 8,
              border: "1px solid #e2e8f0", fontSize: 13, color: "#1e293b",
            }}
          />
        </div>
      </div>

      <button
        onClick={validate}
        disabled={loading || !sql.trim()}
        style={{
          background: loading ? "#94a3b8" : "linear-gradient(135deg, #2563eb, #7c3aed)",
          color: "#fff", border: "none", borderRadius: 8,
          padding: "11px 28px", fontSize: 14, fontWeight: 700,
          cursor: loading ? "not-allowed" : "pointer",
          letterSpacing: "0.3px",
        }}
      >
        {loading ? "⏳ Validation en cours…" : "🔒 Lancer la validation"}
      </button>

      {error && (
        <div style={{
          marginTop: 12, background: "#fee2e2", borderRadius: 8,
          padding: 12, fontSize: 12, color: "#dc2626",
        }}>
          ❌ Erreur réseau : {error}
        </div>
      )}
    </div>
  );
};

// ─── Panneau résumé résultat ──────────────────────────────────────

const ResultSummary: React.FC<{ result: ValidationResult }> = ({ result }) => {
  const f = statusFinal(result.status);
  return (
    <div style={{
      background: "#fff", borderRadius: 12,
      border: `2px solid ${f.color}44`,
      padding: 16,
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 12, marginBottom: 12,
      }}>
        <span style={{
          background: f.bg, color: f.color,
          padding: "4px 14px", borderRadius: 20,
          fontSize: 14, fontWeight: 800,
        }}>{f.label}</span>
        <span style={{ fontSize: 12, color: "#94a3b8" }}>
          ⏱ {result.duree_ms} ms total
        </span>
        {result.sha256_regle && (
          <code style={{
            fontSize: 10, color: "#64748b",
            background: "#f1f5f9", padding: "2px 8px", borderRadius: 4,
          }} title={result.sha256_regle}>
            SHA-256: {result.sha256_regle.slice(0, 20)}…
          </code>
        )}
      </div>

      {/* Bypass alerts */}
      {result.bypass_alerts?.length > 0 && (
        <div style={{
          background: "#fee2e2", borderRadius: 8, padding: 10, marginBottom: 10,
        }}>
          <div style={{ fontWeight: 700, color: "#dc2626", fontSize: 12, marginBottom: 4 }}>
            🚨 Obfuscation détectée (C0)
          </div>
          {result.bypass_alerts.map((a, i) => (
            <div key={i} style={{ fontSize: 11, color: "#b91c1c" }}>• {a}</div>
          ))}
        </div>
      )}

      {/* Erreurs */}
      {result.errors?.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontWeight: 700, fontSize: 12, color: "#374151", marginBottom: 6 }}>
            ✗ {result.errors.length} erreur(s) bloquante(s)
          </div>
          {result.errors.map((e, i) => (
            <div key={i} style={{
              background: "#fff5f5", borderRadius: 8, padding: "7px 12px",
              marginBottom: 5, border: "1px solid #fecaca",
              display: "flex", gap: 8, alignItems: "flex-start",
            }}>
              <span style={{
                background: "#fee2e2", color: "#dc2626",
                fontSize: 10, fontWeight: 700,
                padding: "1px 7px", borderRadius: 10, whiteSpace: "nowrap",
              }}>C{e.couche}</span>
              <div>
                <span style={{ fontSize: 11, fontWeight: 700, color: "#dc2626" }}>
                  {e.code}
                </span>
                <span style={{ fontSize: 11, color: "#4b5563", marginLeft: 6 }}>
                  {e.message}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Warnings */}
      {result.warnings?.length > 0 && (
        <div>
          <div style={{ fontWeight: 700, fontSize: 12, color: "#374151", marginBottom: 6 }}>
            ⚠ {result.warnings.length} avertissement(s)
          </div>
          {result.warnings.map((w, i) => (
            <div key={i} style={{
              background: "#fffbeb", borderRadius: 8, padding: "6px 12px",
              marginBottom: 4, fontSize: 11, color: "#92400e",
              border: "1px solid #fde68a",
            }}>
              {w}
            </div>
          ))}
        </div>
      )}

      {/* Rapport logique C5 */}
      {result.logic_report && (
        <LogicReportPanel report={result.logic_report} />
      )}
    </div>
  );
};


// ─── Panneau rapport logique C5 ──────────────────────────────────

const CONFLICT_COLORS: Record<string, string> = {
  CRITIQUE: "#dc2626",
  MAJEUR:   "#b45309",
  MINEUR:   "#6b7280",
};

const LogicReportPanel: React.FC<{ report: LogicReport }> = ({ report }) => {
  const reco = report.recommandation;
  const recoCfg = {
    AUTORISER: { bg: "#dcfce7", color: "#15803d", icon: "✓", label: "AUTORISER" },
    CORRIGER:  { bg: "#fef3c7", color: "#b45309", icon: "⚠", label: "CORRIGER" },
    REFUSER:   { bg: "#fee2e2", color: "#dc2626", icon: "✗", label: "REFUSER" },
  }[reco] ?? { bg: "#f1f5f9", color: "#6b7280", icon: "?", label: reco };

  return (
    <div style={{
      background: "#fff", borderRadius: 12, padding: 16,
      border: `2px solid ${recoCfg.color}33`,
      marginTop: 8,
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
        <div style={{
          background: "linear-gradient(135deg, #dc2626, #7c3aed)",
          color: "#fff", borderRadius: 8,
          padding: "3px 10px", fontSize: 11, fontWeight: 800,
        }}>🧠 C5 — Validation logique</div>
        <span style={{
          background: recoCfg.bg, color: recoCfg.color,
          padding: "3px 12px", borderRadius: 20,
          fontSize: 13, fontWeight: 800,
          border: `1px solid ${recoCfg.color}44`,
        }}>
          {recoCfg.icon} {recoCfg.label}
        </span>
        <span style={{ fontSize: 12, color: "#94a3b8", marginLeft: "auto" }}>
          {report.regles_analysees} règles analysées · {report.duree_ms} ms
        </span>
      </div>

      {/* Compteurs */}
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        {[
          { label: "CRITIQUE", nb: report.nb_critique, color: "#dc2626" },
          { label: "MAJEUR",   nb: report.nb_majeur,   color: "#b45309" },
          { label: "MINEUR",   nb: report.nb_mineur,   color: "#6b7280" },
        ].map(({ label, nb, color }) => (
          <div key={label} style={{
            flex: 1, background: "#f8fafc", borderRadius: 8, padding: "8px 12px",
            border: `1px solid ${nb > 0 ? color + "44" : "#e2e8f0"}`,
            textAlign: "center",
          }}>
            <div style={{ fontSize: 20, fontWeight: 800, color: nb > 0 ? color : "#94a3b8" }}>
              {nb}
            </div>
            <div style={{ fontSize: 10, color: "#94a3b8", fontWeight: 600 }}>{label}</div>
          </div>
        ))}
      </div>

      {/* Règles impactées */}
      {report.regles_impactees.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, marginBottom: 4 }}>
            Règles impactées
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {report.regles_impactees.map((code) => (
              <span key={code} style={{
                background: "#fef3c7", color: "#92400e",
                padding: "2px 8px", borderRadius: 6, fontSize: 11, fontWeight: 600,
              }}>{code}</span>
            ))}
          </div>
        </div>
      )}

      {/* Conflits détaillés */}
      {report.conflits.length > 0 && (
        <div>
          <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, marginBottom: 6 }}>
            {report.conflits.length} conflit(s) détecté(s)
          </div>
          {report.conflits.map((c, i) => (
            <div key={i} style={{
              background: "#fafafa", borderRadius: 8, padding: "10px 12px",
              marginBottom: 6,
              borderLeft: `4px solid ${CONFLICT_COLORS[c.niveau] || "#94a3b8"}`,
              border: `1px solid ${CONFLICT_COLORS[c.niveau]}22`,
              borderLeftWidth: 4,
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                <span style={{
                  background: CONFLICT_COLORS[c.niveau] + "22",
                  color: CONFLICT_COLORS[c.niveau],
                  padding: "1px 8px", borderRadius: 10,
                  fontSize: 10, fontWeight: 800,
                }}>{c.niveau}</span>
                <span style={{
                  background: "#f1f5f9", color: "#64748b",
                  padding: "1px 7px", borderRadius: 8,
                  fontSize: 10,
                }}>{c.heuristique}</span>
                {c.regle_cible && (
                  <span style={{
                    background: "#fef3c7", color: "#92400e",
                    padding: "1px 7px", borderRadius: 8, fontSize: 10, fontWeight: 600,
                  }}>→ {c.regle_cible}</span>
                )}
              </div>
              <div style={{ fontSize: 12, fontWeight: 600, color: "#1e293b", marginBottom: 3 }}>
                {c.message}
              </div>
              {c.details && (
                <div style={{ fontSize: 11, color: "#64748b", marginBottom: 3 }}>{c.details}</div>
              )}
              {c.suggestion && (
                <div style={{
                  fontSize: 11, color: "#059669",
                  background: "#f0fdf4", borderRadius: 6,
                  padding: "4px 8px", marginTop: 4,
                }}>
                  💡 {c.suggestion}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {report.conflits.length === 0 && (
        <div style={{
          background: "#f0fdf4", borderRadius: 8, padding: "10px 14px",
          fontSize: 13, color: "#15803d",
        }}>
          ✓ Aucun conflit logique détecté — cohérence inter-règles confirmée
        </div>
      )}
    </div>
  );
};

// ─── Dashboard santé ──────────────────────────────────────────────

const DashboardSante: React.FC = () => {
  const [data,    setData]    = useState<SandboxSante | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/v1/admin/sandbox/rapport")
      .then(r => r.json())
      .then(d => { setData(d.sante || null); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  if (loading) return (
    <div style={{ padding: 20, textAlign: "center", color: "#94a3b8", fontSize: 13 }}>
      Chargement du tableau de bord…
    </div>
  );
  if (!data) return null;

  const cards = [
    { label: "Validées (24h)",   value: data.validations_ok_24h,      color: "#15803d", icon: "✓" },
    { label: "Invalides (24h)",  value: data.validations_ko_24h,      color: "#b45309", icon: "⚠" },
    { label: "Refusées (24h)",   value: data.validations_refuse_24h,  color: "#dc2626", icon: "✗" },
    { label: "Durée moy. (ms)",  value: data.duree_moy_ms,            color: "#7c3aed", icon: "⏱" },
    { label: "Non validées",     value: data.nb_non_validees,
      color: data.nb_non_validees > 0 ? "#b45309" : "#6b7280", icon: "⏳" },
    { label: "Alertes bypass",   value: data.nb_bypass_24h,
      color: data.nb_bypass_24h > 0   ? "#dc2626" : "#6b7280", icon: "🚨" },
    { label: "Nouvelles (7j)",   value: data.regles_validees_7j,      color: "#2563eb", icon: "🆕" },
  ];

  return (
    <div style={{
      background: "#fff", borderRadius: 12,
      border: "1px solid #e2e8f0", padding: 20,
    }}>
      <div style={{
        fontSize: 14, fontWeight: 700, color: "#1e293b", marginBottom: 16,
      }}>
        📊 Santé du sandbox
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
        {cards.map(c => (
          <div key={c.label} style={{
            flex: 1, minWidth: 100,
            background: "#f8fafc", borderRadius: 10,
            padding: "12px 16px",
            border: "1px solid #e2e8f0",
          }}>
            <div style={{ fontSize: 11, color: "#64748b", fontWeight: 600, marginBottom: 4 }}>
              {c.icon} {c.label}
            </div>
            <div style={{ fontSize: 26, fontWeight: 800, color: c.color }}>
              {c.value}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

// ─── Page principale ──────────────────────────────────────────────

type Tab = "pipeline" | "dashboard";

export default function SqlSandboxPage() {
  const [tab,          setTab]          = useState<Tab>("pipeline");
  const [result,       setResult]       = useState<ValidationResult | null>(null);
  const [layerStates,  setLayerStates]  = useState<Record<number, LayerStatut>>({});
  const [validating,   setValidating]   = useState(false);
  const animRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  // Nettoyer les timers précédents
  const clearTimers = () => {
    animRef.current.forEach(t => clearTimeout(t));
    animRef.current = [];
  };

  // Animer le pipeline couche par couche
  const animatePipeline = useCallback((traces: LayerTrace[], status: ValidationStatus) => {
    clearTimers();
    setLayerStates({});
    setValidating(true);

    // Déclencher RUNNING sur chaque couche séquentiellement
    let delay = 0;
    traces.forEach((trace, idx) => {
      const id = trace.couche;

      // Marquer RUNNING
      const t1 = setTimeout(() => {
        setLayerStates(prev => ({ ...prev, [id]: "RUNNING" }));
      }, delay);
      animRef.current.push(t1);

      delay += Math.max(trace.duree_ms, 120);

      // Marquer le résultat final de cette couche
      const finalState = trace.statut === "PASS" ? "PASS"
                       : trace.statut === "FAIL" ? "FAIL"
                       : trace.statut === "SKIP" ? "SKIP"
                       : "WARN";
      const t2 = setTimeout(() => {
        setLayerStates(prev => ({ ...prev, [id]: finalState }));
        if (idx === traces.length - 1) setValidating(false);
      }, delay);
      animRef.current.push(t2);

      delay += 200;
    });

    // Si moins de 4 traces (pipeline arrêté), marquer les suivantes SKIP
    const executedIds = new Set(traces.map(t => t.couche));
    LAYER_DEFS.forEach(def => {
      if (!executedIds.has(def.id)) {
        const t = setTimeout(() => {
          setLayerStates(prev => ({ ...prev, [def.id]: "IDLE" }));
        }, delay);
        animRef.current.push(t);
      }
    });
  }, []);

  const handleResult = useCallback((r: ValidationResult | null, traces: LayerTrace[]) => {
    setResult(r);
    if (r && traces.length > 0) {
      animatePipeline(traces, r.status);
    } else {
      setLayerStates({});
      setValidating(false);
    }
  }, [animatePipeline]);

  return (
    <div style={{
      padding: "24px 28px", background: "#f8fafc", minHeight: "100vh",
      fontFamily: "system-ui, sans-serif",
    }}>
      {/* En-tête */}
      <div style={{ marginBottom: 24 }}>
        <div style={{
          display: "flex", alignItems: "center", gap: 14, marginBottom: 8,
        }}>
          <div style={{
            width: 44, height: 44, borderRadius: 12,
            background: "linear-gradient(135deg, #1e40af, #7c3aed)",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 22,
          }}>
            🔒
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: "#0f172a" }}>
              SQL Sandbox — Validation 4 couches
            </h1>
            <p style={{ margin: 0, fontSize: 13, color: "#64748b" }}>
              Système de validation sécurisée des règles métier SQL · FONCIER+ Niger
            </p>
          </div>
        </div>

        {/* Breadcrumb couches */}
        <div style={{
          display: "flex", alignItems: "center", gap: 0, flexWrap: "wrap",
        }}>
          {LAYER_DEFS.map((def, idx) => (
            <React.Fragment key={def.id}>
              <div style={{
                background: def.bg, border: `1px solid ${def.border}`,
                borderRadius: 8, padding: "4px 12px",
                fontSize: 11, fontWeight: 700, color: def.couleur,
              }}>
                {def.icon} C{def.id} — {def.titre}
              </div>
              {idx < LAYER_DEFS.length - 1 && (
                <span style={{ color: "#cbd5e1", margin: "0 4px", fontSize: 16 }}>›</span>
              )}
            </React.Fragment>
          ))}
        </div>
      </div>

      {/* Tabs */}
      <div style={{
        display: "flex", gap: 4, marginBottom: 20,
        background: "#f1f5f9", borderRadius: 10,
        padding: 4, width: "fit-content",
      }}>
        {([
          { id: "pipeline", label: "Pipeline interactif", icon: "⚡" },
          { id: "dashboard", label: "Tableau de bord",   icon: "📊" },
        ] as const).map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              padding: "8px 18px", borderRadius: 8, border: "none",
              background: tab === t.id ? "#fff" : "transparent",
              fontWeight: tab === t.id ? 700 : 400,
              color: tab === t.id ? "#2563eb" : "#64748b",
              fontSize: 13, cursor: "pointer",
              boxShadow: tab === t.id ? "0 1px 4px rgba(0,0,0,.08)" : "none",
            }}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {tab === "pipeline" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>

          {/* Colonne gauche : éditeur */}
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <SqlEditor onResult={handleResult} />
            {result && <ResultSummary result={result} />}
          </div>

          {/* Colonne droite : pipeline animé */}
          <div style={{
            background: "#fff", borderRadius: 12,
            border: "1px solid #e2e8f0", padding: 20,
          }}>
            <div style={{
              fontSize: 13, fontWeight: 700, color: "#1e293b",
              marginBottom: 16,
            }}>
              ⚡ Pipeline de validation
            </div>
            <PipelineDiagram
              layerStates={layerStates}
              layerTraces={result?.layers || []}
              finalStatus={validating ? null : (result?.status ?? null)}
              running={validating}
            />
          </div>
        </div>
      )}

      {tab === "dashboard" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <DashboardSante />
        </div>
      )}
    </div>
  );
}
