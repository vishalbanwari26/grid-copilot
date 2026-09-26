import { useEffect, useState } from "react";
import TwinApp from "./TwinApp";
import OtDemo from "./OtDemo";
import DecisionLogPanel from "./components/fleet/DecisionLogPanel";
import { getDecisions, type DecisionLog } from "./fleetApi";

function DecisionsView() {
  const [log, setLog] = useState<DecisionLog | null>(null);
  useEffect(() => {
    getDecisions().then(setLog);
  }, []);
  return <DecisionLogPanel log={log} />;
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
