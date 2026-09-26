import { useEffect, useState } from "react";
import { postDecision, type Proposal } from "../../fleetApi";

const URGENCY_CLASS: Record<string, string> = { immediate: "bad", weeks: "warn", next_outage: "info", routine: "good" };

function loadReviewer(): string {
  try {
    return localStorage.getItem("gc.reviewer") ?? "";
  } catch {
    return "";
  }
}

export default function ProposalReview({
  proposal,
  running,
  classes,
  onDecided,
}: {
  proposal: Proposal | null;
  running: boolean;
  classes: string[];
  onDecided: () => void;
}) {
  const [reviewer, setReviewer] = useState(loadReviewer);
  const [verdict, setVerdict] = useState("accept");
  const [reason, setReason] = useState("");
  const [finalClass, setFinalClass] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ id: string; hash: string; submodel: unknown } | null>(null);
  const [showSub, setShowSub] = useState(false);

  useEffect(() => {
    setVerdict("accept");
    setReason("");
    setFinalClass("");
    setError(null);
    setDone(null);
    setShowSub(false);
  }, [proposal?.proposal_id]);

  if (!proposal) {
    return (
      <div className="panel">
        <div className="panel-head">
          <h2>Proposal</h2>
        </div>
        <div className="report-empty">
          {running ? "The agent is investigating…" : "Investigate the alarm to get a proposal. A named engineer decides on it."}
        </div>
      </div>
    );
  }

  const submit = async () => {
    setError(null);
    try {
      try {
        localStorage.setItem("gc.reviewer", reviewer);
      } catch {
        /* per-viewer convenience only */
      }
      const res = await postDecision({
        proposal_id: proposal.proposal_id,
        reviewer,
        verdict,
        reason,
        final_fault_class: verdict === "override" ? finalClass : undefined,
      });
      setDone({ id: res.record.decision_id, hash: res.record.hash, submodel: res.submodel });
      onDecided();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const failedValidation = proposal.validation !== "passed";
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Proposal · awaiting a decision</h2>
        <span className="hint">
          {proposal.model} · {proposal.prompt_version}
        </span>
      </div>
      <div className="report">
        <div className="verdict-row">
          <span className="badge info mono">{proposal.fault_class}</span>
          <span className={`badge ${URGENCY_CLASS[proposal.urgency] ?? "info"}`}>urgency: {proposal.urgency.replace("_", " ")}</span>
          <span className={`badge ${failedValidation ? "warn" : "good"}`}>
            {failedValidation ? "validation failed" : "validation passed"}
          </span>
          <span className={`badge ${proposal.critic === "accept" ? "good" : "warn"}`}>critic: {proposal.critic}</span>
        </div>
        <div className="root-cause">{proposal.root_cause}</div>
        <div className="kv">
          <div className="k">Recommended action</div>
          <div className="v">{proposal.recommended_action}</div>
          <div className="k">Would change this conclusion</div>
          <div className="v">{proposal.would_change_conclusion}</div>
          <div className="k">Data quality</div>
          <div className="v small">{proposal.data_quality.replace(/Findings:.*$/, "")}</div>
        </div>
        <div className="section-label">Reasoning</div>
        <div className="reasoning">{proposal.reasoning}</div>
        <details className="evidence-details">
          <summary>Evidence from {proposal.evidence.length} tools</summary>
          {proposal.evidence.map((e, i) => (
            <div className="evidence-item" key={i}>
              <div className="evidence-src">{e.tool}</div>
              <div className="evidence-txt">{e.summary}</div>
            </div>
          ))}
        </details>

        {!done ? (
          <div className="review">
            <div className="section-label">Decision (required before anything is acted on)</div>
            <div className="review-row">
              <input className="select" placeholder="Your name" value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
              <div className="seg">
                {["accept", "override", "defer"].map((v) => (
                  <button key={v} className={verdict === v ? "on" : ""} onClick={() => setVerdict(v)}>
                    {v}
                  </button>
                ))}
              </div>
              {verdict === "override" && (
                <select className="select" value={finalClass} onChange={(e) => setFinalClass(e.target.value)}>
                  <option value="">decided fault class…</option>
                  {classes.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              )}
            </div>
            <textarea
              className="reason"
              placeholder={verdict === "accept" ? "Note (optional)" : `Reason for the ${verdict} (required)`}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            {error && <div className="error-banner">{error}</div>}
            <button className="btn" onClick={submit}>
              Record decision
            </button>
          </div>
        ) : (
          <div className="gt">
            <div className="gt-label">Decision recorded</div>
            Signed by {reviewer}, verdict {verdict}. Record hash <span className="mono">{done.hash.slice(0, 16)}…</span>, chained to the
            previous record. Only reviewed decisions become this asset's history.
            <div>
              <button className="linkish" onClick={() => setShowSub(!showSub)}>
                {showSub ? "Hide" : "Show"} AAS-style submodel export
              </button>
            </div>
            {showSub && <pre className="json">{JSON.stringify(done.submodel, null, 2)}</pre>}
          </div>
        )}
      </div>
    </div>
  );
}
