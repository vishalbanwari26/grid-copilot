import { useEffect, useState } from "react";
import { getSubstation, type SubstationView, type UnitSummary } from "../../twinApi";
import { HealthBar, Trust, hiColor } from "./common";

function SingleLine({ view, onTransformer }: { view: SubstationView; onTransformer: (id: string) => void }) {
  const units = view.transformers;
  const W = 760;
  const H = 280;
  const hvY = 70;
  const lvY = 210;
  const xs = units.map((_, i) => (W / (units.length + 1)) * (i + 1));
  const [hv, lv] = view.kv.replace(" kV", "").split("/");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" className="grid-map">
      {view.neighbours.map((n, i) => {
        const x = 120 + i * 180;
        return (
          <g key={n}>
            <line x1={x + 260} y1={10} x2={x + 260} y2={hvY} stroke="var(--border-strong)" strokeWidth={2} />
            <text x={x + 266} y={24} fontSize={10} fill="var(--faint)">
              line to {n}
            </text>
          </g>
        );
      })}
      <line x1={60} y1={hvY} x2={W - 60} y2={hvY} stroke="var(--muted)" strokeWidth={4} />
      <text x={60} y={hvY - 8} fontSize={11} fill="var(--muted)">
        {hv} kV busbar
      </text>
      <line x1={60} y1={lvY} x2={W - 60} y2={lvY} stroke="var(--muted)" strokeWidth={4} />
      <text x={60} y={lvY - 8} fontSize={11} fill="var(--muted)">
        {lv} kV busbar
      </text>
      {[0.2, 0.4, 0.6, 0.8].map((f) => (
        <g key={f}>
          <line x1={W * f} y1={lvY} x2={W * f} y2={H - 12} stroke="var(--border-strong)" strokeWidth={2} />
          <polygon points={`${W * f - 5},${H - 16} ${W * f + 5},${H - 16} ${W * f},${H - 6}`} fill="var(--border-strong)" />
        </g>
      ))}
      {units.map((u, i) => {
        const x = xs[i];
        const c = hiColor(u.health_index);
        return (
          <g key={u.asset} className="map-node" onClick={() => onTransformer(u.asset)}>
            <line x1={x} y1={hvY} x2={x} y2={115} stroke="var(--text)" strokeWidth={2} />
            <line x1={x} y1={165} x2={x} y2={lvY} stroke="var(--text)" strokeWidth={2} />
            <circle cx={x} cy={127} r={17} fill="var(--panel)" stroke={c} strokeWidth={2.5} />
            <circle cx={x} cy={152} r={17} fill="var(--panel)" stroke={c} strokeWidth={2.5} />
            <text x={x + 28} y={134} fontSize={13} fontWeight={650} fill="var(--text)">
              {u.asset}
            </text>
            <text x={x + 28} y={150} fontSize={10.5} fill="var(--muted)">
              {u.mva} MVA · HI {u.health_index}
            </text>
            {u.alarm && (
              <text x={x + 28} y={165} fontSize={10} fill="var(--warn)" fontFamily="var(--mono)">
                {u.alarm.code}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function UnitCard({ u, onOpen }: { u: UnitSummary; onOpen: () => void }) {
  return (
    <div className="unit-card" onClick={onOpen}>
      <div className="unit-top">
        <div>
          <div className="asset-name">{u.asset}</div>
          <div className="asset-sub">
            {u.mva} MVA · {u.kv} · built {u.year}
          </div>
        </div>
        <Trust grade={u.data_trust} />
      </div>
      <HealthBar value={u.health_index} />
      <div className="unit-grid">
        <div>
          <span className="k">Load now / 7-day peak</span>
          <span className="mono">
            {u.load_now_pu} / {u.load_peak_7d_pu} pu
          </span>
        </div>
        <div>
          <span className="k">Top oil now</span>
          <span className="mono">{u.top_oil_now_c} °C</span>
        </div>
        <div>
          <span className="k">Risk</span>
          <span className="mono">{u.risk}</span>
        </div>
        <div>
          <span className="k">Alarm</span>
          <span className={u.alarm ? "mono alarm-code" : "faint"}>{u.alarm?.code ?? "none"}</span>
        </div>
      </div>
      <div className="unit-foot">
        {u.decision ? (
          <span className={`tag ${u.decision.verdict}`}>
            {u.decision.verdict} · {u.decision.reviewer}
          </span>
        ) : u.alarm ? (
          <span className="faint">awaiting review</span>
        ) : (
          <span className="faint">{u.action}</span>
        )}
      </div>
    </div>
  );
}

export default function SubstationDashboard({ id, onTransformer }: { id: string; onTransformer: (id: string) => void }) {
  const [view, setView] = useState<SubstationView | null>(null);
  useEffect(() => {
    setView(null);
    getSubstation(id).then(setView);
  }, [id]);
  if (!view) return <div className="panel"><div className="tl-empty">Loading {id}…</div></div>;
  const customers = Math.max(...view.transformers.map((t) => t.customers));
  return (
    <div className="stack">
      <div className="panel">
        <div className="panel-head">
          <h2>
            Substation {view.id} · {view.kv}
          </h2>
          <span className="hint">
            {view.role} · {view.combined_mva} MVA installed · {customers.toLocaleString()} customers · id {view.mrid.slice(0, 8)}
          </span>
        </div>
        <div className="panel-body">
          <SingleLine view={view} onTransformer={onTransformer} />
        </div>
      </div>
      <div className="units">
        {view.transformers.map((u) => (
          <UnitCard key={u.asset} u={u} onOpen={() => onTransformer(u.asset)} />
        ))}
      </div>
    </div>
  );
}
