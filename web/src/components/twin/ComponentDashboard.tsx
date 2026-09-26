import { useEffect, useState } from "react";
import { getComponent, type ComponentView } from "../../twinApi";
import { Kpi, Spark, Trust, hiColor } from "./common";

function fmt(v: number, unit: string) {
  if (["bool", "count", "step"].includes(unit)) return Math.round(v).toString();
  return v.toFixed(unit === "pu" || unit === "s" || unit === "A" ? 2 : 1);
}

export default function ComponentDashboard({ asset, id }: { asset: string; id: string }) {
  const [view, setView] = useState<ComponentView | null>(null);
  useEffect(() => {
    setView(null);
    getComponent(asset, id).then(setView);
  }, [asset, id]);
  if (!view) return <div className="panel"><div className="tl-empty">Loading…</div></div>;
  const isSensor = view.kind === "sensor";
  return (
    <div className="stack">
      <div className="kpis">
        {isSensor ? (
          <>
            <Kpi label="Data grade" value={view.grade ?? ""} tone={view.grade === "trusted" ? "var(--good)" : "var(--bad)"} />
            <Kpi label="Last calibration" value={view.last_calibration ?? ""} />
            <Kpi
              label="Calibration due"
              value={view.calibration_due ?? ""}
              tone={view.calibration_overdue ? "var(--warn)" : undefined}
              sub={view.calibration_overdue ? "overdue" : undefined}
            />
          </>
        ) : (
          <Kpi label="Condition" value={view.condition ?? 0} tone={hiColor(view.condition ?? 0)} sub="lowest subsystem score" />
        )}
        <Kpi label="IEC 61850 logical node" value={view.iec61850_ln} sub={view.cim ? `CIM ${view.cim}` : "measurement"} />
        <Kpi label="Alarms on its signals" value={view.alarms.length} tone={view.alarms.length ? "var(--warn)" : undefined} />
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>
            {view.name} · {view.asset}
          </h2>
          <span className="hint">id {view.mrid}</span>
        </div>
        <div className="panel-body">
          {view.reasons.length > 0 ? (
            <ul className="reasons">
              {view.reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          ) : (
            <div className="faint small">
              {isSensor ? "Passed every data-quality check over the last 7 days." : "No condition findings."}
            </div>
          )}
          {view.alarms.map((a) => (
            <div className="ev alarm" key={a.code + a.ts}>
              <span className="mono">{a.ts.slice(0, 16).replace("T", " ")}</span> <b>{a.code}</b> {a.text}
            </div>
          ))}
          <div className="signals three" style={{ marginTop: 14 }}>
            {Object.entries(view.series).map(([name, s]) => {
              const bad = s.grade !== "trusted" && s.grade !== "not_checked";
              return (
                <div className={`sig-card${bad ? " untrusted" : ""}`} key={name}>
                  <div className="sig-top">
                    <span className="sig-name">{s.label}</span>
                    <span className="sig-val">
                      {fmt(s.values[s.values.length - 1], s.unit)} {s.unit}
                    </span>
                  </div>
                  <Spark values={s.values} bad={bad} />
                  <div className="sig-foot">
                    <span className="mono faint">
                      {s.ln}
                      {s.kind === "calculated" ? " · calculated" : ""}
                    </span>
                    {s.grade !== "not_checked" && <Trust grade={s.grade} title={s.reasons.join("; ")} />}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {view.oltc_ops_since_service !== undefined && (
        <div className="panel">
          <div className="panel-head">
            <h2>Tap-changer operations</h2>
            <span className="hint">
              {view.oltc_ops_since_service.toLocaleString()} since service on {view.oltc_last_service} · service due {view.oltc_service_due}
            </span>
          </div>
          <div className="table-wrap">
            <table className="fleet-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Tap</th>
                  <th>Operating time</th>
                  <th>Motor current</th>
                  <th>Completed</th>
                </tr>
              </thead>
              <tbody>
                {view.recent_operations?.map((o) => (
                  <tr key={o.ts}>
                    <td className="mono">{o.ts.slice(0, 16).replace("T", " ")}</td>
                    <td className="mono">
                      {o.from > 0 ? "+" : ""}
                      {o.from} → {o.to > 0 ? "+" : ""}
                      {o.to}
                    </td>
                    <td className="mono">{o.duration_s.toFixed(2)} s</td>
                    <td className="mono">{o.motor_a.toFixed(2)} A</td>
                    <td>{o.completed ? <span className="faint">yes</span> : <span className="alarm-code">no</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
