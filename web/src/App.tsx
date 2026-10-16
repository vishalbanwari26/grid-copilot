import { useEffect, useState } from "react";
import TwinApp from "./TwinApp";
import OtDemo from "./OtDemo";
import DecisionLogPanel from "./components/fleet/DecisionLogPanel";
import PatternsPanel from "./components/fleet/PatternsPanel";
import { getDecisions, getPatterns, type DecisionLog, type PatternList } from "./fleetApi";
import { STATIC_DEMO } from "./transport";

function DecisionsView() {
  const [log, setLog] = useState<DecisionLog | null>(null);
  const [patterns, setPatterns] = useState<PatternList | null>(null);
  const loadPatterns = () => getPatterns().then(setPatterns);
  useEffect(() => {
    getDecisions().then(setLog);
    loadPatterns();
  }, []);
  return (
    <>
      <PatternsPanel list={patterns} onReviewed={loadPatterns} />
      <DecisionLogPanel log={log} />
    </>
  );
}

const SUBTITLE: Record<string, string> = {
  twin: "Digital twin of a transformer fleet: condition, lifecycle, and alarm investigations a named engineer signs off",
  decisions: "Every decision, who made it and why, in a tamper-evident log",
  ot: "Anomaly detection + agentic root-cause analysis on grid / OT telemetry",
};

export default function App() {
  const params = new URLSearchParams(window.location.search);
  const initial = params.get("view") ?? "twin";
  const [view, setView] = useState(initial in SUBTITLE ? initial : "twin");
  const pick = (v: string) => {
    setView(v);
    const p = new URLSearchParams(window.location.search);
    if (v === "twin") p.delete("view");
    else p.set("view", v);
    const q = p.toString();
    window.history.replaceState(null, "", q ? `?${q}` : window.location.pathname);
  };
  return (
    <div className="app">
      {STATIC_DEMO && (
        <div className="demo-banner">
          Recorded demo: investigations replay a real run of the offline mock agent, and the decisions you sign stay
          in this browser. To run it live with a real model, see{" "}
          <a href="https://github.com/vishalbanwari26/grid-copilot">github.com/vishalbanwari26/grid-copilot</a>.
        </div>
      )}
      <header className="header">
        <div className="brand">
          <div className="logo">
            <svg viewBox="0 0 24 24" fill="none" stroke="#04121a" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 12h4l3 8 4-16 3 8h4" />
            </svg>
          </div>
          <div>
            <h1>Grid Copilot</h1>
            <p>{SUBTITLE[view]}</p>
          </div>
        </div>
        <div className="fault-tabs">
          {[
            ["twin", "Grid twin"],
            ["decisions", "Decision log"],
            ["ot", "OT anomaly demo"],
          ].map(([k, label]) => (
            <button key={k} className={`fault-tab${view === k ? " active" : ""}`} onClick={() => pick(k)}>
              {label}
            </button>
          ))}
        </div>
      </header>
      {view === "twin" && <TwinApp />}
      {view === "decisions" && <DecisionsView />}
      {view === "ot" && <OtDemo />}
    </div>
  );
}
