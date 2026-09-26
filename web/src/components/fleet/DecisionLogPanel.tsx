import type { DecisionLog } from "../../fleetApi";

export default function DecisionLogPanel({ log }: { log: DecisionLog | null }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Decision log</h2>
        {log && (
          <span className={`trust ${log.chain_ok ? "trusted" : "untrusted"}`} title={log.chain}>
            hash chain {log.chain_ok ? "intact" : "broken"}
          </span>
        )}
      </div>
      {!log || log.records.length === 0 ? (
        <div className="tl-empty">No decisions yet. Proposals only become asset history once someone decides on them.</div>
      ) : (
        <div className="table-wrap">
          <table className="fleet-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Asset</th>
                <th>Alarm</th>
                <th>Proposed</th>
                <th>Decided</th>
                <th>Reviewer</th>
                <th>Reason</th>
                <th>Hash</th>
              </tr>
            </thead>
            <tbody>
              {log.records.map((r) => (
                <tr key={r.decision_id}>
                  <td className="mono">{r.decided_at.slice(0, 16).replace("T", " ")}</td>
                  <td>{r.asset}</td>
                  <td className="mono">{r.alarm}</td>
                  <td className="mono" title={r.model}>
                    {r.proposed}
                  </td>
                  <td>
                    <span className={`tag ${r.verdict}`}>{r.verdict === "override" ? `override: ${r.final}` : r.verdict}</span>
                  </td>
                  <td>{r.reviewer}</td>
                  <td className="small">{r.reason || <span className="faint">none</span>}</td>
                  <td className="mono faint">{r.hash}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
