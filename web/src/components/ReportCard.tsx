import type { Report } from "../api";

export default function ReportCard({ report, running }: { report: Report | null; running: boolean }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Incident report</h2>
        {report && <span className="hint">trigger {report.trigger_signal}</span>}
      </div>
      {!report ? (
        <div className="report-empty">
          {running ? "The agent is investigating…" : "The cited root-cause report appears here once the agent concludes."}
        </div>
      ) : (
        <div className="report">
          <div className="verdict-row">
            <span className={`badge ${report.verdict === "accept" ? "good" : "warn"}`}>
              critic: {report.verdict}
            </span>
            <span className="badge info">score {report.score.toFixed(1)}σ</span>
            {report.ground_truth != null && (
              <span className={`badge ${report.correct ? "good" : "warn"}`}>
                {report.correct ? "✓ matches ground truth" : "≠ ground truth"}
              </span>
            )}
          </div>

          <div className="root-cause">{report.root_cause}</div>

          <div className="conf">
            <div className="conf-bar">
              <div className="conf-fill" style={{ width: `${Math.round(report.confidence * 100)}%` }} />
            </div>
            <div className="conf-val">{Math.round(report.confidence * 100)}%</div>
          </div>

          <div className="section-label">Reasoning</div>
          <div className="reasoning">{report.reasoning}</div>

          <div className="section-label">Evidence</div>
          {report.evidence.map((e, i) => (
            <div className="evidence-item" key={i}>
              <div className="evidence-src">{e.source}</div>
              <div className="evidence-txt">
                {e.summary}
                {e.citations.map((c) => (
                  <span className="chip" key={c} style={{ marginLeft: 6 }}>
                    {c}
                  </span>
                ))}
              </div>
            </div>
          ))}

          {report.ground_truth != null && (
            <div className="gt">
              <div className="gt-label">Ground truth (injected)</div>
              {report.ground_truth}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
