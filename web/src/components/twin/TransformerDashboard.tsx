import { useEffect, useState } from "react";
import { getAsset, type AssetDetail } from "../../fleetApi";
import { getTransformer, type TransformerView } from "../../twinApi";
import AssetView from "../fleet/AssetView";
import InvestigatePanel from "./InvestigatePanel";
import { HealthBar, Kpi, Trust, hiColor } from "./common";

const SUB_LABEL: Record<string, string> = { dga: "Dissolved gas", thermal: "Thermal", oltc: "Tap changer", pd: "Partial discharge" };

export default function TransformerDashboard({ id, onComponent }: { id: string; onComponent: (cid: string) => void }) {
  const [view, setView] = useState<TransformerView | null>(null);
  const [detail, setDetail] = useState<AssetDetail | null>(null);
  const load = () => getTransformer(id).then(setView);
  useEffect(() => {
    setView(null);
    setDetail(null);
    load();
    getAsset(id).then(setDetail);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  if (!view) return <div className="panel"><div className="tl-empty">Loading {id}…</div></div>;
  const lc = view.lifecycle;
  const opsPct = Math.min(100, Math.round((100 * lc.oltc_ops_since_service) / lc.oltc_service_interval_ops));
  return (
    <div className="stack">
      <div className="kpis">
        <Kpi label="Health index" value={view.health_index} tone={hiColor(view.health_index)} sub={`risk ${view.risk}`} />
        <Kpi label="Age" value={`${lc.age_years} y`} sub={`design life ${lc.design_life_years} y`} />
        <Kpi
          label="Insulation life used"
          value={`${lc.insulation_life_used_pct}%`}
          sub={`${lc.remaining_life_capped ? "50+" : lc.remaining_life_years_at_current_rate} y left at recent loading`}
        />
        <Kpi
          label="Tap-changer service"
          value={lc.oltc_service_overdue ? "overdue" : lc.oltc_service_due}
          tone={lc.oltc_service_overdue ? "var(--warn)" : undefined}
          sub={`last ${lc.oltc_last_service}`}
        />
        <Kpi label="Data" value={view.data_trust} tone={view.data_trust === "trusted" ? "var(--good)" : "var(--bad)"} sub="worst sensor grade" />
      </div>

      <div className="twin-row">
        <div className="panel">
          <div className="panel-head">
            <h2>Asset model</h2>
            <span className="hint">
              {view.asset} · {view.mva} MVA · {view.kv} · id {view.mrid.slice(0, 8)}
            </span>
          </div>
          <div className="panel-body tree">
            <div className="tree-root">
              <b>{view.asset}</b> <span className="faint">PowerTransformer · YPTR</span>
            </div>
            <div className="tree-group">Components</div>
            {view.components.map((c) => (
              <div className="tree-item" key={c.id} onClick={() => onComponent(c.id)}>
                <span className="dot" style={{ background: hiColor(c.condition) }} />
                <span className="tree-name">{c.name}</span>
                <span className="faint mono">{c.iec61850_ln}</span>
                <span className="mono">{c.condition}</span>
              </div>
            ))}
            <div className="tree-group">Sensors</div>
            {view.sensors.map((s) => (
              <div className="tree-item" key={s.id} onClick={() => onComponent(s.id)}>
                <span className="dot" style={{ background: s.grade === "trusted" ? "var(--good)" : s.grade === "suspect" ? "var(--warn)" : "var(--bad)" }} />
                <span className="tree-name">{s.name}</span>
                <span className="faint mono">{s.iec61850_ln}</span>
                <Trust grade={s.grade} title={s.reasons.join("; ")} />
                {s.calibration_overdue && <span className="tag override">calibration due</span>}
              </div>
            ))}
          </div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <h2>Condition and lifecycle</h2>
          </div>
          <div className="panel-body">
            {Object.entries(view.subscores).map(([k, v]) => (
              <div className="sub-row" key={k} title={v.reasons.join("; ")}>
                <span className="sub-name">{SUB_LABEL[k] ?? k}</span>
                <HealthBar value={v.score} />
              </div>
            ))}
            <div className="section-label">Tap-changer operations since last service</div>
            <div className="hi-bar tall">
              <div className="hi-fill" style={{ width: `${opsPct}%`, background: opsPct > 90 ? "var(--warn)" : "var(--accent)" }} />
            </div>
            <div className="small faint">
              {lc.oltc_ops_since_service.toLocaleString()} of {lc.oltc_service_interval_ops.toLocaleString()} ·{" "}
              {lc.oltc_ops_per_day} per day · due {lc.oltc_service_due}
            </div>
            <div className="section-label">History</div>
            <div className="events">
              {lc.history.length === 0 && <div className="faint">No recorded maintenance or decisions.</div>}
              {lc.history.map((h, i) => (
                <div className={`ev ${h.kind === "decision" ? "alarm" : ""}`} key={i}>
                  <span className="mono">{h.date}</span> {h.kind === "decision" ? <b>decision</b> : null} {h.text}
                </div>
              ))}
            </div>
            <div className="small faint assump">{lc.assumptions}</div>
          </div>
        </div>
      </div>

      <AssetView detail={detail} />
      <InvestigatePanel asset={view.asset} alarm={view.alarm} onDecided={load} />
    </div>
  );
}
