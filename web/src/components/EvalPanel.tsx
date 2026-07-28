import type { EvalData } from "../api";

export default function EvalPanel({ data }: { data: EvalData | null }) {
  if (!data) return null;
  const best = Math.max(...data.detectors.map((d) => d.f1_adj));
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Detector benchmark</h2>
        <span className="hint">{data.dataset}</span>
      </div>
      <div className="panel-body">
        <p className="eval-note">{data.note}</p>
        <table className="eval-table">
          <thead>
            <tr>
              <th>Detector</th>
              <th>Precision</th>
              <th>Recall</th>
              <th>F1 (adj)</th>
              <th>F1 (raw)</th>
            </tr>
          </thead>
          <tbody>
            {data.detectors.map((d) => (
              <tr key={d.name} className={d.f1_adj === best ? "best" : ""}>
                <td className="det-name">{d.name}</td>
                <td>{(d.precision * 100).toFixed(0)}%</td>
                <td>{(d.recall * 100).toFixed(0)}%</td>
                <td>
                  {d.f1_adj.toFixed(2)}
                  <span className="metric-bar" style={{ width: `${d.f1_adj * 46}px` }} />
                </td>
                <td>{d.f1_raw.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
