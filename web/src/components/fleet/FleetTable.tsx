import type { Fleet } from "../../fleetApi";

const SUB_LABEL: Record<string, string> = { dga: "DGA", thermal: "Thermal", oltc: "OLTC", pd: "PD" };

function hiColor(v: number) {
  return v >= 85 ? "var(--good)" : v >= 55 ? "var(--warn)" : "var(--bad)";
}

export default function FleetTable({
  fleet,
  selected,
  onSelect,
}: {
  fleet: Fleet | null;
  selected: string | null;
  onSelect: (asset: string) => void;
}) {
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Fleet risk ranking</h2>
        <span className="hint">{fleet ? `${fleet.assets.length} transformers · load: ${fleet.load_source}` : ""}</span>
      </div>
      {!fleet ? (
        <div className="tl-empty">Simulating the fleet…</div>
      ) : (
        <div className="table-wrap">
          <table className="fleet-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Transformer</th>
                <th>Health index</th>
                <th>Risk</th>
                <th>Weakest</th>
                <th>First alarm</th>
                <th>Data</th>
                <th>Decision</th>
              </tr>
            </thead>
            <tbody>
              {fleet.assets.map((a, i) => (
                <tr
                  key={a.asset}
                  className={a.asset === selected ? "sel" : ""}
                  onClick={() => onSelect(a.asset)}
                  title={a.action}
                >
                  <td className="mono faint">{i + 1}</td>
                  <td>
                    <div className="asset-name">{a.asset}</div>
                    <div className="asset-sub">
                      {a.substation} · {a.mva} MVA · {a.kv} · {a.year}
                    </div>
                  </td>
                  <td>
                    <div className="hi">
                      <div className="hi-bar">
                        <div className="hi-fill" style={{ width: `${a.health_index}%`, background: hiColor(a.health_index) }} />
                      </div>
                      <span className="mono">{a.health_index}</span>
                    </div>
                  </td>
                  <td className="mono">{a.risk}</td>
                  <td>
                    {a.worst ? (
                      <span className="tag" title={a.subscores[a.worst]?.reasons.join("; ")}>
                        {SUB_LABEL[a.worst]} {a.subscores[a.worst]?.score}
                      </span>
                    ) : (
                      <span className="faint">none</span>
                    )}
                  </td>
                  <td>
                    {a.alarm ? (
                      <span className="mono alarm-code" title={a.alarm.text}>
                        {a.alarm.code}
                      </span>
                    ) : (
                      <span className="faint">none</span>
                    )}
                  </td>
                  <td>
                    <span
                      className={`trust ${a.data_trust}`}
                      title={a.flagged_channels.length ? `flagged: ${a.flagged_channels.join(", ")}` : "all checked channels trusted"}
                    >
                      {a.data_trust}
                    </span>
                  </td>
                  <td>
                    {a.decision ? (
                      <span className={`tag ${a.decision.verdict}`} title={a.decision.reason}>
                        {a.decision.verdict} · {a.decision.reviewer}
                      </span>
                    ) : a.alarm ? (
                      <span className="faint">awaiting review</span>
                    ) : (
                      <span className="faint">n/a</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
