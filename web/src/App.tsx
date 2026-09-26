import { useState } from "react";
import FleetApp from "./FleetApp";
import OtDemo from "./OtDemo";

export default function App() {
  const params = new URLSearchParams(window.location.search);
  const [view, setView] = useState(params.get("view") === "ot" ? "ot" : "fleet");
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
            <p>
              {view === "fleet"
                ? "Transformer fleet: condition, risk, and alarm investigations a named engineer signs off"
                : "Anomaly detection + agentic root-cause analysis on grid / OT telemetry"}
            </p>
          </div>
        </div>
        <div className="fault-tabs">
          <button className={`fault-tab${view === "fleet" ? " active" : ""}`} onClick={() => setView("fleet")}>
            Transformer fleet
          </button>
          <button className={`fault-tab${view === "ot" ? " active" : ""}`} onClick={() => setView("ot")}>
            OT anomaly demo
          </button>
        </div>
      </header>
      {view === "fleet" ? <FleetApp /> : <OtDemo />}
    </div>
  );
}
