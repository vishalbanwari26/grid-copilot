import { useEffect, useRef, useState } from "react";
import {
  getEval,
  getFaults,
  getTelemetry,
  streamInvestigation,
  type EvalData,
  type Fault,
  type RCAEvent,
  type Report,
  type Telemetry,
} from "./api";
import TelemetryPanel from "./components/TelemetryPanel";
import InvestigationTimeline from "./components/InvestigationTimeline";
import ReportCard from "./components/ReportCard";
import EvalPanel from "./components/EvalPanel";

export default function App() {
  const [faults, setFaults] = useState<Fault[]>([]);
  const [selected, setSelected] = useState<string>("bearing_overheat");
  const [telemetry, setTelemetry] = useState<Telemetry | null>(null);
  const [evalData, setEvalData] = useState<EvalData | null>(null);
  const [events, setEvents] = useState<RCAEvent[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [running, setRunning] = useState(false);
  const [provider, setProvider] = useState("groq");
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const params = new URLSearchParams(window.location.search);
  const paceParam = params.has("pace") ? Number(params.get("pace")) : undefined;

  useEffect(() => {
    getFaults().then(setFaults);
    getEval().then(setEvalData);
  }, []);

  // Deep-link: /?autorun=1 runs the investigation as soon as telemetry is ready.
  useEffect(() => {
    if (params.get("autorun") && telemetry && !running && events.length === 0) {
      run();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [telemetry]);

  useEffect(() => {
    setTelemetry(null);
    setEvents([]);
    setReport(null);
    getTelemetry(selected).then(setTelemetry);
  }, [selected]);

  const run = () => {
    esRef.current?.close();
    setEvents([]);
    setReport(null);
    setError(null);
    setRunning(true);
    esRef.current = streamInvestigation(
      selected,
      provider,
      (e) => setEvents((prev) => [...prev, e]),
      (r) => setReport(r),
      (err) => {
        setRunning(false);
        if (err) setError(err);
      },
      paceParam,
    );
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
            <p>Anomaly detection + agentic root-cause analysis on grid / OT telemetry</p>
          </div>
        </div>
        <div className="header-meta">
          <span className="pill">public + synthetic data</span>
          <span className="pill">cortex · mnemos</span>
        </div>
      </header>

      <div className="controls">
        <div className="fault-tabs">
          {faults.map((f) => (
            <button
              key={f.name}
              className={`fault-tab${f.name === selected ? " active" : ""}`}
              onClick={() => setSelected(f.name)}
              disabled={running}
              title={f.blurb}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="spacer" />
        <select className="select" value={provider} onChange={(e) => setProvider(e.target.value)} disabled={running}>
          <option value="groq">live (groq)</option>
          <option value="mock">offline (mock)</option>
        </select>
        <button className="btn" onClick={run} disabled={running || !telemetry}>
          {running ? (
            <>
              <span className="spin" /> Investigating…
            </>
          ) : (
            "▶ Run investigation"
          )}
        </button>
      </div>

      {error && (
        <div className="error-banner">
          Live run failed: {error}. Set <code>GROQ_API_KEY</code> in the repo's <code>.env</code>, or switch to <b>offline (mock)</b>.
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        <TelemetryPanel telemetry={telemetry} />
        <div className="grid">
          <div className="col">
            <InvestigationTimeline events={events} running={running} />
          </div>
          <div className="col">
            <ReportCard report={report} running={running} />
          </div>
        </div>
        <EvalPanel data={evalData} />
      </div>

      <div className="foot">
        Reuses the <code>cortex</code> orchestration primitives and <code>mnemos</code> memory · every claim in the report is cited
      </div>
    </div>
  );
}
