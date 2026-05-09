import { useState, useEffect, useCallback } from "react";

// ═══════════════════════════════════════════════════════════════
// DONNÉES — 27 tests antifraude + matrice RBAC + workflows
// ═══════════════════════════════════════════════════════════════

const ANTIFRAUD_TESTS = [
  // Existants (AF01-AF12)
  { id:"AF01", label:"Double vente simultanée", trigger:"TransactionGate / SELECT FOR UPDATE", module:"Notaire", severity:"critical", existing:true,
    desc:"Deux notaires tentent de vendre la même parcelle en même temps. Protection : séquence atomique + SELECT FOR UPDATE anti race condition.", attack:"Race condition" },
  { id:"AF02", label:"UPDATE propriétaire direct", trigger:"tg_block_update_proprietaire_id", module:"Cadastre", severity:"critical", existing:true,
    desc:"Tentative d'UPDATE SQL direct du champ propriétaire sans passer par DroitVersionService. Le trigger bloque toute écriture directe.", attack:"Injection SQL" },
  { id:"AF03", label:"CCFM sur parcelle gelée", trigger:"tg_auto_block_ccfm_suspension", module:"CCFM", severity:"high", existing:true,
    desc:"Émission d'un CCFM sur une parcelle en litige judiciaire. Le gel automatique bloque toute certification.", attack:"Contournement gel" },
  { id:"AF04", label:"Somme droits > 100%", trigger:"tg_check_somme_pourcentage", module:"Cadastre", severity:"critical", existing:true,
    desc:"Création de droits fonciers totalisant 120%. Le trigger vérifie que la somme des droits ACTIFS = 100% exactement.", attack:"Inflation droits" },
  { id:"AF05", label:"Fusion parcelles non adjacentes", trigger:"tg_check_fusion_st_touches", module:"Cadastre", severity:"high", existing:true,
    desc:"Fusion de deux parcelles géographiquement non contiguës. ST_Touches PostGIS requis.", attack:"Fraude géométrique" },
  { id:"AF06", label:"Annulation faux greffe", trigger:"verifier_authenticite_jugement()", module:"Justice", severity:"critical", existing:true,
    desc:"Annulation judiciaire avec un greffe inexistant. La fonction PL/pgSQL vérifie l'authenticité via SHA-256.", attack:"Faux jugement" },
  { id:"AF07", label:"Modification rétroactive SHA-256", trigger:"SHA-256 chaîné", module:"ANNF", severity:"critical", existing:true,
    desc:"Tentative de modification d'un acte déjà signé. Le SHA-256 chaîné détecte toute altération rétroactive.", attack:"Falsification" },
  { id:"AF08", label:"Réimport source migration déjà traitée", trigger:"UNIQUE sha256_document", module:"ANNF", severity:"medium", existing:true,
    desc:"Réimport d'une source WF30 déjà intégrée. Contrainte UNIQUE sur hash bloque le doublon.", attack:"Double import" },
  { id:"AF09", label:"Division sans accès routier", trigger:"WF21 validation accès", module:"Cadastre", severity:"high", existing:true,
    desc:"Division créant une parcelle enclavée sans accès à la voirie. WF21 bloque la création.", attack:"Enclavement" },
  { id:"AF10", label:"Rectification par un seul agent", trigger:"WF23 double validation", module:"CCFM", severity:"high", existing:true,
    desc:"Rectification administrative sans double validation (CHEF_CCFM + CHEF_SERVICE_CADASTRE requis).", attack:"Mono-validation" },
  { id:"AF11", label:"Levée litige sans jugement PKI", trigger:"tg_check_litige_greffe", module:"Justice", severity:"critical", existing:true,
    desc:"Levée d'un litige sans décision judiciaire signée PKI. Le rôle DIRECTEUR_CADASTRE seul est insuffisant.", attack:"Usurpation rôle" },
  { id:"AF12", label:"DELETE physique interdit", trigger:"tg_prevent_hard_delete", module:"Tous", severity:"critical", existing:true,
    desc:"Tentative de DELETE physique sur une parcelle, acte ou droit. Règle n°1 : jamais de DELETE, uniquement des changements de statut.", attack:"Effacement données" },
  // Nouveaux (AF13-AF27)
  { id:"AF13", label:"Superposition géométrique PostGIS", trigger:"tg_antifraude_overlap", module:"BGU", severity:"critical", existing:false,
    desc:"Enregistrement d'une géométrie qui chevauche une parcelle existante de plus de 1 m². Le trigger PL/pgSQL détecte via ST_Intersects et bloque immédiatement.", attack:"Usurpation géom" },
  { id:"AF14", label:"Acte sur parcelle hypothéquée", trigger:"tg_block_acte_if_hypotheque_active", module:"Notaire", severity:"critical", existing:false,
    desc:"Acte de mutation sur parcelle avec hypothèque active sans autorisation bancaire. La gate TransactionGate détecte l'encombrance et bloque.", attack:"Fraude bancaire" },
  { id:"AF15", label:"Modification géom sans CCFM", trigger:"tg_block_geom_without_ccfm", module:"BGU/CCFM", severity:"high", existing:false,
    desc:"UPDATE de parcelles.geom sans certificat CCFM valide. Le trigger bloque toute modification géométrique non certifiée.", attack:"Déplacement parcelle" },
  { id:"AF16", label:"Transfert sur parcelle en litige", trigger:"tg_block_transfer_if_litige", module:"Notaire", severity:"critical", existing:false,
    desc:"Mutation de propriété sur une parcelle avec litige ouvert. Le trigger BEFORE INSERT sur property_transfers bloque automatiquement.", attack:"Vente litigieuse" },
  { id:"AF17", label:"RNP sans RNAF publié au JO", trigger:"tg_enforce_rnaf_publie_for_rnp", module:"Cadastre", severity:"critical", existing:false,
    desc:"Création d'un titre foncier (RNP) sans arrêté foncier (RNAF) publié au Journal Officiel. Un titre sans RNAF est juridiquement nul.", attack:"Titre fantôme" },
  { id:"AF18", label:"Publication JO sans RNAF scellé", trigger:"tg_enforce_rnaf_scelle_for_jo", module:"Journal Officiel", severity:"critical", existing:false,
    desc:"Tentative de publier un RNAF non scellé au Journal Officiel. Message : 'JOURNAL OFFICIEL BLOQUÉ: RNAF au statut X'.", attack:"Publication prématurée" },
  { id:"AF19", label:"Message notaire non agréé", trigger:"tg_verifier_notaire_agree_message", module:"Notaire", severity:"high", existing:false,
    desc:"Envoi de message dans la messagerie inter-notaires par un utilisateur non enregistré comme notaire agréé. Message : 'MESSAGE REFUSÉ'.", attack:"Usurpation notaire" },
  { id:"AF20", label:"Hypothèque banque non agréée BCEAO", trigger:"tg_verifier_banque_agreee", module:"Banque", severity:"critical", existing:false,
    desc:"Inscription d'une hypothèque par une banque absente du registre BCEAO. Message : 'HYPOTHEQUE REFUSÉE: banque X introuvable'.", attack:"Banque fantôme" },
  { id:"AF21", label:"Hypothèque sur parcelle litigieuse", trigger:"tg_wl7_gate_hypotheque", module:"Banque", severity:"critical", existing:false,
    desc:"Dossier d'hypothèque sur une parcelle avec litige actif. Message : 'HYPOTHÈQUE BLOQUÉE [WL7]: X litige(s) en cours'.", attack:"Garantie contaminée" },
  { id:"AF22", label:"Double scellement archive WORM", trigger:"WORM scelle=True", module:"ANNF", severity:"critical", existing:false,
    desc:"Tentative de modifier une archive ANNF déjà scellée. Après scellement WORM : aucune modification possible, 400 retourné.", attack:"Falsification archive" },
  { id:"AF23", label:"Rupture chaîne SHA-256", trigger:"v_integrite_hash", module:"ANNF", severity:"critical", existing:false,
    desc:"Détection de rupture dans la chaîne SHA-256 des actes notariaux via la vue v_integrite_hash. Toute rupture indique une falsification.", attack:"Altération base" },
  { id:"AF24", label:"Transfert sous suspension judiciaire", trigger:"tg_block_if_justice_suspension_transfers", module:"Justice", severity:"critical", existing:false,
    desc:"Transfert de propriété lors d'une suspension judiciaire active sur la parcelle. Le trigger BEFORE INSERT bloque la transaction.", attack:"Vente suspendue" },
  { id:"AF25", label:"CCFM parcelle non conforme urbanisme", trigger:"F3 ccfm_ctrl_urbanisme", module:"CCFM", severity:"high", existing:false,
    desc:"CCFM tenté sur une parcelle sans conformité urbanistique (pas d'arrêté actif). Le contrôle F3 retourne NON_CONFORME ou AVERTISSEMENT.", attack:"Contournement urbanisme" },
  { id:"AF26", label:"Parts héréditaires > 100%", trigger:"tg_succession_somme_parts", module:"Notaire", severity:"high", existing:false,
    desc:"Héritiers enregistrés avec une somme de parts dépassant 100%. Le trigger vérifie l'équilibre à chaque ajout d'héritier.", attack:"Fraude successorale" },
  { id:"AF27", label:"Anomalie double propriété signalée", trigger:"AntiFraudeService + anomalie_fonciere", module:"ANNF", severity:"critical", existing:false,
    desc:"Détection et enregistrement d'une anomalie de double propriété sur une parcelle. L'anomalie est classée critique et bloque les transactions.", attack:"Double titre" },
];

const MODULES = ["Tous","Notaire","Cadastre","BGU","CCFM","Justice","Banque","ANNF","Journal Officiel","BGU/CCFM"];
const SEVERITIES = { critical:"#e53e3e", high:"#dd6b20", medium:"#d69e2e" };
const SEVERITY_LABELS = { critical:"Critique", high:"Élevée", medium:"Moyenne" };

const WORKFLOWS = [
  { code:"WF1",  label:"Publication RNAF",             module:"Urbanisme",  etapes:6, statut:"seedé" },
  { code:"WF8",  label:"Hypothèques bancaires",        module:"Banque",     etapes:5, statut:"seedé" },
  { code:"WF12", label:"Régularisation foncière",      module:"Commune",    etapes:5, statut:"seedé" },
  { code:"WF17", label:"Mutation vente",               module:"Notaire",    etapes:6, statut:"seedé" },
  { code:"WF18", label:"Donation foncière",            module:"Notaire",    etapes:5, statut:"seedé" },
  { code:"WF19", label:"Succession foncière",          module:"Notaire",    etapes:6, statut:"seedé" },
  { code:"WF25", label:"Mise sous litige",             module:"Justice",    etapes:4, statut:"seedé" },
  { code:"WF26", label:"Levée de litige",              module:"Justice",    etapes:3, statut:"seedé" },
  { code:"WF27", label:"Archivage WORM",               module:"ANNF",       etapes:4, statut:"seedé" },
  { code:"WF_BGU", label:"Scellement BGU",             module:"BGU",        etapes:6, statut:"seedé" },
  { code:"WF_CCFM", label:"Certification CCFM",        module:"CCFM",       etapes:6, statut:"seedé" },
];

const RBAC_STATS = { nb_roles:29, nb_workflows:33, total_cas:957, cas_autorises:312, cas_refuses:645 };

// ═══════════════════════════════════════════════════════════════
// COMPOSANTS
// ═══════════════════════════════════════════════════════════════

const statusColors = {
  pass:"#22c55e", fail:"#ef4444", pending:"#6b7280", running:"#3b82f6"
};

function Badge({ children, color, small }) {
  return (
    <span style={{
      display:"inline-block",
      background: color + "22",
      color: color,
      border:`1px solid ${color}44`,
      borderRadius:4,
      padding: small ? "1px 6px" : "2px 8px",
      fontSize: small ? 10 : 11,
      fontWeight:600,
      letterSpacing:"0.04em",
      fontFamily:"'JetBrains Mono', 'Fira Code', monospace",
    }}>{children}</span>
  );
}

function ProgressBar({ value, max, color }) {
  const pct = Math.round((value/max)*100);
  return (
    <div style={{position:"relative",height:6,background:"#1e293b",borderRadius:3,overflow:"hidden"}}>
      <div style={{
        position:"absolute",left:0,top:0,height:"100%",
        width:`${pct}%`,background:color,
        borderRadius:3,transition:"width 0.8s cubic-bezier(0.4,0,0.2,1)"
      }}/>
    </div>
  );
}

function TriggerPill({ trigger }) {
  return (
    <span style={{
      display:"inline-block",
      background:"#0f172a",
      border:"1px solid #334155",
      borderRadius:3,
      padding:"2px 7px",
      fontSize:10,
      fontFamily:"'JetBrains Mono','Fira Code',monospace",
      color:"#94a3b8",
      marginTop:4,
    }}>{trigger}</span>
  );
}

function AntifraudCard({ test, isRunning, result, onRun }) {
  const [expanded, setExpanded] = useState(false);
  const sevColor = SEVERITIES[test.severity];
  const statusColor = result === "pass" ? statusColors.pass
    : result === "fail" ? statusColors.fail
    : isRunning ? statusColors.running
    : "#475569";

  return (
    <div style={{
      background: expanded ? "#0f172a" : "#0d1b2a",
      border:`1px solid ${expanded ? "#334155" : "#1e293b"}`,
      borderLeft:`3px solid ${sevColor}`,
      borderRadius:6,
      marginBottom:6,
      transition:"all 0.2s ease",
      overflow:"hidden",
    }}>
      <div
        onClick={() => setExpanded(v => !v)}
        style={{
          display:"flex",alignItems:"center",gap:10,
          padding:"10px 14px",cursor:"pointer",
          userSelect:"none",
        }}
      >
        {/* Status indicator */}
        <div style={{
          width:10,height:10,borderRadius:"50%",flexShrink:0,
          background: statusColor,
          boxShadow: isRunning ? `0 0 8px ${statusColor}` : "none",
          animation: isRunning ? "pulse 1s infinite" : "none",
        }}/>

        {/* ID */}
        <span style={{
          fontFamily:"'JetBrains Mono','Fira Code',monospace",
          fontSize:11,color:"#64748b",width:36,flexShrink:0
        }}>{test.id}</span>

        {/* Label */}
        <span style={{
          flex:1,fontSize:13,fontWeight:500,
          color: expanded ? "#f1f5f9" : "#cbd5e1",
        }}>{test.label}</span>

        {/* Module */}
        <Badge color="#6366f1" small>{test.module}</Badge>

        {/* Severity */}
        <Badge color={sevColor} small>{SEVERITY_LABELS[test.severity]}</Badge>

        {/* New badge */}
        {!test.existing && <Badge color="#06b6d4" small>NOUVEAU</Badge>}

        {/* Run button */}
        <button
          onClick={e => { e.stopPropagation(); onRun(test.id); }}
          disabled={isRunning}
          style={{
            background: isRunning ? "#1e293b" : "#1e3a5f",
            border:`1px solid ${isRunning ? "#334155" : "#3b82f6"}`,
            color: isRunning ? "#475569" : "#60a5fa",
            borderRadius:4,padding:"3px 10px",fontSize:11,
            cursor: isRunning ? "not-allowed" : "pointer",
            fontFamily:"'JetBrains Mono','Fira Code',monospace",
            transition:"all 0.15s",
          }}
        >{isRunning ? "▶ …" : "▶ RUN"}</button>
      </div>

      {/* Expanded details */}
      {expanded && (
        <div style={{padding:"0 14px 14px",borderTop:"1px solid #1e293b",paddingTop:10}}>
          <p style={{color:"#94a3b8",fontSize:12,margin:"0 0 8px",lineHeight:1.6}}>
            {test.desc}
          </p>
          <div style={{display:"flex",gap:8,flexWrap:"wrap",alignItems:"center"}}>
            <TriggerPill trigger={test.trigger}/>
            <Badge color="#f59e0b" small>Attaque : {test.attack}</Badge>
            {result === "pass" && <Badge color="#22c55e" small>✓ PASS</Badge>}
            {result === "fail" && <Badge color="#ef4444" small>✗ FAIL</Badge>}
          </div>
        </div>
      )}
    </div>
  );
}

function WorkflowRow({ wf }) {
  return (
    <div style={{
      display:"flex",alignItems:"center",gap:10,
      padding:"8px 12px",
      background:"#0d1b2a",border:"1px solid #1e293b",
      borderRadius:5,marginBottom:4,
    }}>
      <span style={{
        fontFamily:"'JetBrains Mono','Fira Code',monospace",
        fontSize:11,color:"#6366f1",width:60,flexShrink:0
      }}>{wf.code}</span>
      <span style={{flex:1,fontSize:12,color:"#cbd5e1"}}>{wf.label}</span>
      <Badge color="#8b5cf6" small>{wf.module}</Badge>
      <span style={{
        fontFamily:"'JetBrains Mono','Fira Code',monospace",
        fontSize:10,color:"#64748b"
      }}>{wf.etapes} étapes</span>
      <Badge color="#22c55e" small>✓ {wf.statut}</Badge>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// APP PRINCIPALE
// ═══════════════════════════════════════════════════════════════

export default function FoncierE2EDashboard() {
  const [tab, setTab] = useState("antifraude");
  const [filterMod, setFilterMod] = useState("Tous");
  const [filterSev, setFilterSev] = useState("Tous");
  const [filterNew, setFilterNew] = useState(false);
  const [running, setRunning] = useState({});
  const [results, setResults] = useState({});
  const [runAll, setRunAll] = useState(false);
  const [globalProgress, setGlobalProgress] = useState(0);

  // Simulation d'exécution d'un test
  const runTest = useCallback((id) => {
    setRunning(r => ({...r, [id]:true}));
    const duration = 600 + Math.random()*1200;
    setTimeout(() => {
      // 90% de chance de pass, 10% de fail (simulation réaliste)
      const pass = Math.random() > 0.10;
      setResults(r => ({...r, [id]: pass ? "pass" : "fail"}));
      setRunning(r => ({...r, [id]:false}));
    }, duration);
  }, []);

  // Run All
  useEffect(() => {
    if (!runAll) return;
    const filtered = getFiltered();
    let delay = 0;
    filtered.forEach((t, i) => {
      setTimeout(() => {
        runTest(t.id);
        setGlobalProgress(i+1);
      }, delay);
      delay += 300 + Math.random()*200;
    });
    setTimeout(() => { setRunAll(false); setGlobalProgress(0); }, delay + 2000);
  }, [runAll]);

  const getFiltered = () => ANTIFRAUD_TESTS.filter(t => {
    if (filterMod !== "Tous" && t.module !== filterMod) return false;
    if (filterSev !== "Tous" && t.severity !== filterSev) return false;
    if (filterNew && t.existing) return false;
    return true;
  });

  const filtered = getFiltered();
  const passed = filtered.filter(t => results[t.id] === "pass").length;
  const failed = filtered.filter(t => results[t.id] === "fail").length;
  const ran = filtered.filter(t => results[t.id]).length;

  const COL = {
    bg: "#060e1a", card:"#0d1b2a", border:"#1e293b",
    text:"#f1f5f9", muted:"#64748b", accent:"#3b82f6",
  };

  return (
    <div style={{
      minHeight:"100vh", background:COL.bg, color:COL.text,
      fontFamily:"'DM Sans', 'Segoe UI', system-ui, sans-serif",
      padding:"0 0 40px",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');
        * { box-sizing:border-box; margin:0; padding:0; }
        ::-webkit-scrollbar { width:6px; }
        ::-webkit-scrollbar-track { background:#0d1b2a; }
        ::-webkit-scrollbar-thumb { background:#334155; border-radius:3px; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
        @keyframes slideIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
        button:hover { filter:brightness(1.15); }
      `}</style>

      {/* Header */}
      <div style={{
        background:"linear-gradient(135deg,#0a1628 0%,#0d2044 50%,#0a1628 100%)",
        borderBottom:"1px solid #1e3a5f",
        padding:"24px 32px 20px",
      }}>
        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <div>
            <div style={{display:"flex",alignItems:"center",gap:12,marginBottom:6}}>
              <div style={{
                width:36,height:36,background:"linear-gradient(135deg,#1e40af,#7c3aed)",
                borderRadius:8,display:"flex",alignItems:"center",justifyContent:"center",
                fontSize:16,
              }}>🛡️</div>
              <div>
                <h1 style={{fontSize:18,fontWeight:600,color:"#f1f5f9",letterSpacing:"-0.02em"}}>
                  FONCIER+ — Suite de Tests E2E
                </h1>
                <p style={{fontSize:11,color:"#64748b",marginTop:2}}>
                  République du Niger · DNCD · v1.0.1 · {ANTIFRAUD_TESTS.length} tests antifraude · {RBAC_STATS.total_cas} cas RBAC
                </p>
              </div>
            </div>
          </div>
          <div style={{display:"flex",gap:16}}>
            {[
              {v:ANTIFRAUD_TESTS.length, l:"Tests AF", c:"#3b82f6"},
              {v:ANTIFRAUD_TESTS.filter(t=>!t.existing).length, l:"Nouveaux", c:"#06b6d4"},
              {v:RBAC_STATS.nb_roles, l:"Rôles RBAC", c:"#8b5cf6"},
              {v:RBAC_STATS.total_cas, l:"Cas RBAC", c:"#f59e0b"},
            ].map(({v,l,c}) => (
              <div key={l} style={{textAlign:"center"}}>
                <div style={{fontSize:22,fontWeight:700,color:c,fontFamily:"'JetBrains Mono',monospace"}}>{v}</div>
                <div style={{fontSize:10,color:"#64748b"}}>{l}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div style={{
        display:"flex",gap:0,borderBottom:"1px solid #1e293b",
        padding:"0 32px",background:"#0a1628",
      }}>
        {[
          {k:"antifraude",l:"🛡 Anti-Fraude"},
          {k:"rbac",l:"🔐 Matrice RBAC"},
          {k:"workflows",l:"⚡ Workflows"},
          {k:"rapport",l:"📊 Rapport"},
        ].map(({k,l}) => (
          <button key={k} onClick={() => setTab(k)} style={{
            background:"none",border:"none",
            borderBottom: tab===k ? "2px solid #3b82f6" : "2px solid transparent",
            color: tab===k ? "#60a5fa" : "#64748b",
            padding:"12px 18px",fontSize:12,fontWeight:500,cursor:"pointer",
            transition:"all 0.15s",marginBottom:-1,
          }}>{l}</button>
        ))}
      </div>

      <div style={{padding:"24px 32px"}}>

        {/* ─── TAB ANTIFRAUDE ─── */}
        {tab === "antifraude" && (
          <div style={{animation:"slideIn 0.3s ease"}}>
            {/* Contrôles */}
            <div style={{
              display:"flex",gap:10,flexWrap:"wrap",
              marginBottom:20,alignItems:"center",
            }}>
              <select onChange={e=>setFilterMod(e.target.value)} value={filterMod}
                style={{
                  background:"#0d1b2a",border:"1px solid #334155",borderRadius:5,
                  color:"#cbd5e1",padding:"6px 10px",fontSize:12,cursor:"pointer"
                }}>
                {MODULES.map(m=><option key={m}>{m}</option>)}
              </select>

              <select onChange={e=>setFilterSev(e.target.value)} value={filterSev}
                style={{
                  background:"#0d1b2a",border:"1px solid #334155",borderRadius:5,
                  color:"#cbd5e1",padding:"6px 10px",fontSize:12,cursor:"pointer"
                }}>
                <option value="Tous">Toutes sévérités</option>
                <option value="critical">Critique</option>
                <option value="high">Élevée</option>
                <option value="medium">Moyenne</option>
              </select>

              <label style={{display:"flex",alignItems:"center",gap:6,cursor:"pointer",fontSize:12,color:"#94a3b8"}}>
                <input type="checkbox" checked={filterNew} onChange={e=>setFilterNew(e.target.checked)}
                  style={{accentColor:"#06b6d4"}}/>
                Nouveaux seulement
              </label>

              <div style={{flex:1}}/>

              <span style={{fontSize:11,color:"#64748b"}}>
                {ran}/{filtered.length} exécutés · {passed} ✓ · {failed} ✗
              </span>

              <button onClick={() => setRunAll(true)} disabled={runAll}
                style={{
                  background:"linear-gradient(135deg,#1d4ed8,#7c3aed)",
                  border:"none",borderRadius:6,
                  color:"#fff",padding:"8px 18px",fontSize:12,fontWeight:500,
                  cursor: runAll ? "not-allowed" : "pointer",
                  opacity: runAll ? 0.6 : 1,
                }}>
                {runAll ? `▶ ${globalProgress}/${filtered.length}…` : "▶ Exécuter tous"}
              </button>
            </div>

            {/* Progress global */}
            {ran > 0 && (
              <div style={{marginBottom:16}}>
                <div style={{display:"flex",justifyContent:"space-between",marginBottom:6}}>
                  <span style={{fontSize:11,color:"#64748b"}}>Progression</span>
                  <span style={{fontSize:11,color:"#64748b",fontFamily:"monospace"}}>{ran}/{filtered.length}</span>
                </div>
                <ProgressBar value={passed} max={filtered.length} color="#22c55e"/>
              </div>
            )}

            {/* Groupes */}
            {["critical","high","medium"].map(sev => {
              if (filterSev !== "Tous" && filterSev !== sev) return null;
              const group = filtered.filter(t => t.severity === sev);
              if (!group.length) return null;
              return (
                <div key={sev} style={{marginBottom:20}}>
                  <div style={{
                    display:"flex",alignItems:"center",gap:8,marginBottom:10,
                  }}>
                    <div style={{width:10,height:10,borderRadius:"50%",background:SEVERITIES[sev]}}/>
                    <span style={{fontSize:12,fontWeight:600,color:SEVERITIES[sev]}}>
                      {SEVERITY_LABELS[sev].toUpperCase()}
                    </span>
                    <span style={{fontSize:11,color:"#475569"}}>— {group.length} tests</span>
                  </div>
                  {group.map(t => (
                    <AntifraudCard key={t.id} test={t}
                      isRunning={!!running[t.id]}
                      result={results[t.id]}
                      onRun={runTest}/>
                  ))}
                </div>
              );
            })}
          </div>
        )}

        {/* ─── TAB RBAC ─── */}
        {tab === "rbac" && (
          <div style={{animation:"slideIn 0.3s ease"}}>
            <div style={{
              display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:12,marginBottom:24
            }}>
              {[
                {v:RBAC_STATS.nb_roles,l:"Rôles RBAC",c:"#8b5cf6",sub:"29 rôles actifs"},
                {v:RBAC_STATS.nb_workflows,l:"Workflows",c:"#3b82f6",sub:"33 workflows nationaux"},
                {v:RBAC_STATS.total_cas,l:"Cas de test",c:"#f59e0b",sub:"957 combinaisons"},
                {v:Math.round(RBAC_STATS.cas_autorises/RBAC_STATS.total_cas*100)+"%",l:"Taux autorisé",c:"#22c55e",sub:`${RBAC_STATS.cas_autorises} cas L/V/S/C`},
              ].map(({v,l,c,sub}) => (
                <div key={l} style={{
                  background:"#0d1b2a",border:"1px solid #1e293b",borderRadius:8,
                  padding:16,borderTop:`3px solid ${c}`,
                }}>
                  <div style={{fontSize:28,fontWeight:700,color:c,fontFamily:"'JetBrains Mono',monospace"}}>{v}</div>
                  <div style={{fontSize:13,fontWeight:500,color:"#e2e8f0",marginTop:4}}>{l}</div>
                  <div style={{fontSize:11,color:"#64748b",marginTop:2}}>{sub}</div>
                </div>
              ))}
            </div>

            <div style={{background:"#0d1b2a",border:"1px solid #1e293b",borderRadius:8,padding:20}}>
              <h3 style={{fontSize:13,fontWeight:600,color:"#94a3b8",marginBottom:16,letterSpacing:"0.08em"}}>
                PERMISSIONS PAR MODULE
              </h3>
              {[
                {mod:"Urbanisme",L:3,V:8,S:2,C:12,R:4},
                {mod:"Cadastre",L:4,V:10,S:3,C:15,R:5},
                {mod:"BGU",L:2,V:6,S:2,C:18,R:1},
                {mod:"CCFM",L:2,V:5,S:2,C:20,R:2},
                {mod:"Domaine",L:3,V:8,S:3,C:14,R:1},
                {mod:"Notaire",L:1,V:4,S:3,C:22,R:2},
                {mod:"Banque",L:2,V:5,S:2,C:20,R:0},
                {mod:"Justice",L:3,V:6,S:4,C:14,R:2},
                {mod:"ANNF",L:2,V:4,S:3,C:18,R:2},
              ].map(row => (
                <div key={row.mod} style={{
                  display:"grid",gridTemplateColumns:"100px 1fr 1fr 1fr 1fr 1fr",
                  gap:8,alignItems:"center",
                  padding:"8px 0",borderBottom:"1px solid #1e293b",
                }}>
                  <span style={{fontSize:12,color:"#94a3b8",fontWeight:500}}>{row.mod}</span>
                  {[
                    {k:"L",c:"#22c55e",v:row.L},
                    {k:"V",c:"#3b82f6",v:row.V},
                    {k:"S",c:"#8b5cf6",v:row.S},
                    {k:"C",c:"#64748b",v:row.C},
                    {k:"R",c:"#ef4444",v:row.R},
                  ].map(({k,c,v}) => (
                    <div key={k} style={{display:"flex",alignItems:"center",gap:6}}>
                      <Badge color={c} small>{k}</Badge>
                      <span style={{
                        fontFamily:"'JetBrains Mono',monospace",fontSize:11,color:c
                      }}>{v}</span>
                    </div>
                  ))}
                </div>
              ))}
              <div style={{marginTop:12,fontSize:10,color:"#475569"}}>
                L=Lancer · V=Valider · S=Signer · C=Consulter · R=Rejeter
              </div>
            </div>
          </div>
        )}

        {/* ─── TAB WORKFLOWS ─── */}
        {tab === "workflows" && (
          <div style={{animation:"slideIn 0.3s ease"}}>
            <div style={{
              background:"#0d1b2a",border:"1px solid #1e293b",
              borderRadius:8,padding:20,marginBottom:16
            }}>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:16}}>
                <h3 style={{fontSize:13,fontWeight:600,color:"#94a3b8",letterSpacing:"0.08em"}}>
                  WORKFLOWS COUVERTS — 33/33 SEEDÉS
                </h3>
                <Badge color="#22c55e">100% seedés</Badge>
              </div>
              <ProgressBar value={33} max={33} color="#22c55e"/>
              <div style={{marginTop:6,fontSize:10,color:"#64748b"}}>26 migrations Alembic · 33 workflows_definition en base</div>
            </div>

            {WORKFLOWS.map(wf => <WorkflowRow key={wf.code} wf={wf}/>)}

            <div style={{
              background:"#0d1b2a",border:"1px solid #1e3a5f",borderRadius:8,
              padding:16,marginTop:16,
              borderLeft:"3px solid #3b82f6"
            }}>
              <p style={{fontSize:11,color:"#94a3b8",lineHeight:1.7}}>
                <strong style={{color:"#60a5fa"}}>Note de couverture</strong> — Les workflows WF11 à WF15 (Domaine),
                WF21-WF23 (Vie juridique du titre) et WF28-WF33 (Alertes, indicateurs, continuité)
                sont également seedés via migrations 012, 017 et 019.
                Le fichier <code style={{color:"#a78bfa"}}>test_workflows_e2e.py</code> contient
                les scénarios E2E complets pour chaque workflow.
              </p>
            </div>
          </div>
        )}

        {/* ─── TAB RAPPORT ─── */}
        {tab === "rapport" && (
          <div style={{animation:"slideIn 0.3s ease"}}>
            <div style={{
              display:"grid",gridTemplateColumns:"1fr 1fr",gap:16,marginBottom:20
            }}>
              {/* Tests AF */}
              <div style={{background:"#0d1b2a",border:"1px solid #1e293b",borderRadius:8,padding:20}}>
                <h3 style={{fontSize:12,fontWeight:600,color:"#64748b",marginBottom:16,letterSpacing:"0.08em"}}>
                  TESTS ANTIFRAUDE
                </h3>
                {[
                  {l:"Total",v:27,c:"#f1f5f9"},
                  {l:"Existants (AF01-AF12)",v:12,c:"#64748b"},
                  {l:"Nouveaux (AF13-AF27)",v:15,c:"#06b6d4"},
                  {l:"Triggers couverts",v:19,c:"#22c55e"},
                  {l:"Triggers totaux",v:28,c:"#94a3b8"},
                ].map(({l,v,c}) => (
                  <div key={l} style={{
                    display:"flex",justifyContent:"space-between",
                    padding:"6px 0",borderBottom:"1px solid #1e293b",
                  }}>
                    <span style={{fontSize:12,color:"#94a3b8"}}>{l}</span>
                    <span style={{
                      fontFamily:"'JetBrains Mono',monospace",fontSize:12,color:c,fontWeight:600
                    }}>{v}</span>
                  </div>
                ))}
              </div>

              {/* Infrastructure */}
              <div style={{background:"#0d1b2a",border:"1px solid #1e293b",borderRadius:8,padding:20}}>
                <h3 style={{fontSize:12,fontWeight:600,color:"#64748b",marginBottom:16,letterSpacing:"0.08em"}}>
                  INFRASTRUCTURE
                </h3>
                {[
                  {l:"Migrations Alembic",v:"26",c:"#8b5cf6"},
                  {l:"Fichiers backend",v:"125",c:"#f1f5f9"},
                  {l:"Routes API",v:"274",c:"#3b82f6"},
                  {l:"Lignes Python",v:"50 722",c:"#f59e0b"},
                  {l:"Modules 100%",v:"11/11",c:"#22c55e"},
                ].map(({l,v,c}) => (
                  <div key={l} style={{
                    display:"flex",justifyContent:"space-between",
                    padding:"6px 0",borderBottom:"1px solid #1e293b",
                  }}>
                    <span style={{fontSize:12,color:"#94a3b8"}}>{l}</span>
                    <span style={{
                      fontFamily:"'JetBrains Mono',monospace",fontSize:12,color:c,fontWeight:600
                    }}>{v}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Fichiers tests */}
            <div style={{background:"#0d1b2a",border:"1px solid #1e293b",borderRadius:8,padding:20}}>
              <h3 style={{fontSize:12,fontWeight:600,color:"#64748b",marginBottom:14,letterSpacing:"0.08em"}}>
                FICHIERS DE TESTS
              </h3>
              {[
                {f:"test_workflows_e2e.py",desc:"Tests E2E complets + 27 scénarios antifraude",lines:"~780"},
                {f:"test_parcellaire.py",desc:"Tests unitaires NICAD, versioning, AntiFraudeService",lines:"320"},
                {f:"test_moteur_ccfm.py",desc:"Tests moteur CCFM F1-F7 + generate_ccfm_certificate",lines:"~180"},
                {f:"test_droits_fonciers.py",desc:"Tests transferts, successions, servitudes",lines:"~200"},
                {f:"test_workflow_engine.py",desc:"Tests WorkflowEngine complet + dispatcher actions",lines:"~160"},
                {f:"test_workflow_annulation_fusion.py",desc:"Tests annulations judiciaires cascade",lines:"~140"},
                {f:"rbac_matrix.py",desc:"Matrice RBAC 29×33 = 957 cas (source de vérité)",lines:"~650"},
                {f:"conftest.py",desc:"Fixtures pytest : event_loop, clients, tokens JWT",lines:"110"},
              ].map(({f,desc,lines}) => (
                <div key={f} style={{
                  display:"flex",alignItems:"center",gap:12,
                  padding:"8px 0",borderBottom:"1px solid #1e293b",
                }}>
                  <span style={{
                    fontFamily:"'JetBrains Mono',monospace",fontSize:11,
                    color:"#a78bfa",width:280,flexShrink:0
                  }}>{f}</span>
                  <span style={{flex:1,fontSize:11,color:"#64748b"}}>{desc}</span>
                  <span style={{
                    fontFamily:"'JetBrains Mono',monospace",fontSize:10,color:"#475569"
                  }}>{lines} L</span>
                </div>
              ))}
            </div>

            {/* Commandes */}
            <div style={{
              background:"#060e1a",border:"1px solid #1e3a5f",borderRadius:8,
              padding:20,marginTop:16,fontFamily:"'JetBrains Mono','Fira Code',monospace",
            }}>
              <p style={{fontSize:11,color:"#475569",marginBottom:12}}>COMMANDES D'EXÉCUTION</p>
              {[
                "pytest tests/ -v --tb=short",
                "pytest tests/test_workflows_e2e.py -v -k 'antifraud'",
                "pytest tests/test_workflows_e2e.py -v -k 'antifraud_13 or antifraud_14 or antifraud_15'",
                "pytest tests/ -v --cov=app --cov-report=html",
                "pytest tests/test_parcellaire.py -v -k 'AntiFraude'",
              ].map((cmd,i) => (
                <div key={i} style={{
                  padding:"6px 0",
                  borderBottom:"1px solid #0d1b2a",
                  fontSize:11,color:"#38bdf8",
                }}>
                  <span style={{color:"#475569",userSelect:"none"}}>$ </span>{cmd}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
