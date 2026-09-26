import { useEffect, useRef, useState } from "react";
import type { RCAEvent } from "../../api";
import { getClasses, streamFleetInvestigation, type Proposal } from "../../fleetApi";
import InvestigationTimeline from "../InvestigationTimeline";
import ProposalReview from "../fleet/ProposalReview";

export default function InvestigatePanel({
  asset,
  alarm,
  onDecided,
}: {
  asset: string;
  alarm: { code: string; ts: string; text: string } | null;
  onDecided: () => void;
}) {
  const params = new URLSearchParams(window.location.search);
  const pace = params.has("pace") ? Number(params.get("pace")) : undefined;
  const [events, setEvents] = useState<RCAEvent[]>([]);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [running, setRunning] = useState(false);
  const [provider, setProvider] = useState(params.get("provider") ?? "groq");
  const [error, setError] = useState<string | null>(null);
  const [classes, setClasses] = useState<string[]>([]);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    getClasses().then(setClasses);
  }, []);

  useEffect(() => {
    esRef.current?.close();
    setRunning(false);
    setEvents([]);
    setProposal(null);
    setError(null);
    if (params.get("autorun") && alarm) run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [asset]);

  const run = () => {
    esRef.current?.close();
    setEvents([]);
    setProposal(null);
    setError(null);
    setRunning(true);
    esRef.current = streamFleetInvestigation(
      asset,
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

  return (
    <>
      <div className="controls">
        <div className="alarm-line">
          {alarm ? (
            <>
              <span className="mono alarm-code">{alarm.code}</span> on <b>{asset}</b>, {alarm.ts.slice(0, 16).replace("T", " ")}: {alarm.text}
            </>
          ) : (
            <span className="faint">{asset} has no alarm to investigate.</span>
          )}
        </div>
        <div className="spacer" />
        <select className="select" value={provider} onChange={(e) => setProvider(e.target.value)} disabled={running}>
          <option value="groq">live (groq)</option>
          <option value="mock">offline (rule-based mock)</option>
        </select>
        <button className="btn" onClick={run} disabled={running || !alarm}>
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
      <div className="grid">
        <div className="col">
          <InvestigationTimeline events={events} running={running} />
        </div>
        <div className="col">
          <ProposalReview proposal={proposal} running={running} classes={classes} onDecided={onDecided} />
        </div>
      </div>
    </>
  );
}
