import { useEffect, useRef, useState } from "react";
import type { RCAEvent } from "./api";
import {
  getAsset,
  getClasses,
  getDecisions,
  getFleet,
  streamFleetInvestigation,
  type AssetDetail,
  type DecisionLog,
  type Fleet,
  type Proposal,
} from "./fleetApi";
import FleetTable from "./components/fleet/FleetTable";
import AssetView from "./components/fleet/AssetView";
import ProposalReview from "./components/fleet/ProposalReview";
import DecisionLogPanel from "./components/fleet/DecisionLogPanel";
import InvestigationTimeline from "./components/InvestigationTimeline";

export default function FleetApp() {
  const params = new URLSearchParams(window.location.search);
  const [fleet, setFleet] = useState<Fleet | null>(null);
  const [selected, setSelected] = useState<string | null>(params.get("asset"));
  const [detail, setDetail] = useState<AssetDetail | null>(null);
  const [events, setEvents] = useState<RCAEvent[]>([]);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [running, setRunning] = useState(false);
  const [provider, setProvider] = useState(params.get("provider") ?? "groq");
  const [error, setError] = useState<string | null>(null);
  const [classes, setClasses] = useState<string[]>([]);
  const [log, setLog] = useState<DecisionLog | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const pace = params.has("pace") ? Number(params.get("pace")) : undefined;

  const refresh = () => {
    getFleet().then((f) => {
      setFleet(f);
      setSelected((s) => s ?? f.assets[0]?.asset ?? null);
    });
    getDecisions().then(setLog);
  };

  useEffect(() => {
    refresh();
    getClasses().then(setClasses);
  }, []);

  useEffect(() => {
    if (!selected) return;
    esRef.current?.close();
    setRunning(false);
    setDetail(null);
    setEvents([]);
    setProposal(null);
    setError(null);
    getAsset(selected).then(setDetail);
  }, [selected]);

  useEffect(() => {
    if (params.get("autorun") && detail && !running && events.length === 0) run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail]);

  const run = () => {
    if (!selected) return;
    esRef.current?.close();
    setEvents([]);
    setProposal(null);
    setError(null);
    setRunning(true);
    esRef.current = streamFleetInvestigation(
      selected,
      provider,
      (e) => setEvents((p) => [...p, e]),
      (p) => setProposal(p),
      (err) => {
        setRunning(false);
        if (err) setError(err);
      },
      pace,
    );
  };

  const current = fleet?.assets.find((a) => a.asset === selected);
  return (
    <>
      <FleetTable fleet={fleet} selected={selected} onSelect={setSelected} />
      <div className="controls" style={{ marginTop: 18 }}>
        <div className="alarm-line">
          {current?.alarm ? (
            <>
              <span className="mono alarm-code">{current.alarm.code}</span> on <b>{current.asset}</b>, {current.alarm.ts.slice(0, 16).replace("T", " ")}:{" "}
              {current.alarm.text}
            </>
          ) : (
            <span className="faint">{current ? `${current.asset} has no alarm.` : ""}</span>
          )}
        </div>
        <div className="spacer" />
        <select className="select" value={provider} onChange={(e) => setProvider(e.target.value)} disabled={running}>
          <option value="groq">live (groq)</option>
          <option value="mock">offline (rule-based mock)</option>
        </select>
        <button className="btn" onClick={run} disabled={running || !current?.alarm}>
          {running ? (
            <>
              <span className="spin" /> Investigating…
            </>
          ) : (
            "▶ Investigate alarm"
          )}
        </button>
      </div>
      {error && (
        <div className="error-banner">
          Run failed: {error}. For the live model set <code>GROQ_API_KEY</code> in the repo's <code>.env</code>, or switch to the offline mock.
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        <AssetView detail={detail} />
        <div className="grid">
          <div className="col">
            <InvestigationTimeline events={events} running={running} />
          </div>
          <div className="col">
            <ProposalReview proposal={proposal} running={running} classes={classes} onDecided={refresh} />
          </div>
        </div>
        <DecisionLogPanel log={log} />
      </div>
      <div className="foot">
        Simulated fleet on real ETT load profiles · diagnostic tools do the arithmetic, the model weighs the evidence, a named engineer decides
      </div>
    </>
  );
}
