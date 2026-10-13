import { useState } from "react";
import { postPatternReview, type OverridePattern, type PatternList } from "../../fleetApi";

const VERDICTS: [string, string][] = [
  ["approve_change", "approve change"],
  ["keep_watching", "keep watching"],
  ["reject", "reject"],
];

function PatternRow({ p, onReviewed }: { p: OverridePattern; onReviewed: () => void }) {
  const [open, setOpen] = useState(false);
  const [reviewer, setReviewer] = useState(() => {
    try {
      return localStorage.getItem("gc.reviewer") ?? "";
    } catch {
      return "";
    }
  });
  const [verdict, setVerdict] = useState("keep_watching");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    try {
      await postPatternReview({ pattern_id: p.pattern_id, reviewer, verdict, reason });
      try {
        localStorage.setItem("gc.reviewer", reviewer);
      } catch {
        /* storage unavailable */
      }
      setOpen(false);
      setReason("");
      onReviewed();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const status = p.status === "open" ? "open" : p.status.replace("_", " ");
  return (
    <div className="evidence-item">
      <div className="panel-head">
        <div>
          <span className="mono">{p.proposed}</span> proposed, reviewers decided <span className="mono">{p.final}</span> on{" "}
          <b>{p.n_assets}</b> transformers: {p.assets.join(", ")}
        </div>
        <span className={`tag ${p.status === "approve_change" ? "accept" : p.status === "reject" ? "override" : "defer"}`}>
          {status}
        </span>
      </div>
      <div className="evidence-txt">{p.change_request}</div>
      {p.reasons.length > 0 && (
        <details className="evidence-details">
          <summary>Reviewers' reasons ({p.reasons.length})</summary>
          {p.reasons.map((r, i) => (
            <div className="small" key={i}>
              {r}
            </div>
          ))}
        </details>
      )}
      {p.status !== "open" && (
        <div className="small faint">
          {status} by {p.reviewer}: {p.reason}
          {p.new_since_review > 0 && ` · ${p.new_since_review} more transformer(s) since that review`}
        </div>
      )}
      {!open ? (
        <button className="linkish" onClick={() => setOpen(true)}>
          {p.status === "open" ? "Review this pattern" : "Review again"}
        </button>
      ) : (
        <div className="review">
          <div className="review-row">
            <input className="select" placeholder="Your name" value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
            <div className="seg">
              {VERDICTS.map(([v, label]) => (
                <button key={v} className={verdict === v ? "on" : ""} onClick={() => setVerdict(v)}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <textarea className="reason" placeholder="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
          {error && <div className="error-banner">{error}</div>}
          <button className="btn" onClick={submit}>
            Record review
          </button>
        </div>
      )}
    </div>
  );
}

export default function PatternsPanel({ list, onReviewed }: { list: PatternList | null; onReviewed: () => void }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Repeated overrides</h2>
        {list && (
          <span className={`trust ${list.chain_ok ? "trusted" : "untrusted"}`} title={list.chain}>
            review chain {list.chain_ok ? "intact" : "broken"}
          </span>
        )}
      </div>
      <div className="small faint">
        An override stays with its transformer. When the same correction shows up on {list?.min_assets ?? 3} or more
        transformers, it is listed here as a request to review the shared tools or prompt. Nothing changes until a named
        reviewer approves it, and an approved change ships as a new version.
      </div>
      {!list ? (
        <div className="tl-empty">Loading…</div>
      ) : list.patterns.length === 0 ? (
        <div className="tl-empty">No override repeats across {list.min_assets} or more transformers yet.</div>
      ) : (
        list.patterns.map((p) => <PatternRow key={p.pattern_id} p={p} onReviewed={onReviewed} />)
      )}
    </div>
  );
}
