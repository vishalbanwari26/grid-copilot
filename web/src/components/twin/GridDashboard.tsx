import { useEffect, useState } from "react";
import { getFleet, type Fleet } from "../../fleetApi";
import { getGrid, type GridView } from "../../twinApi";
import FleetTable from "../fleet/FleetTable";
import { Kpi, hiColor } from "./common";

function GridMap({ grid, onSubstation }: { grid: GridView; onSubstation: (id: string) => void }) {
  const W = 760;
  const H = 360;
  const pos = (id: string) => {
    const s = grid.substations.find((n) => n.id === id)!;
    return [40 + (s.x / 100) * (W - 80), 30 + (s.y / 100) * (H - 70)];
  };
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" className="grid-map">
      <defs>
        <pattern id="gp" width="24" height="24" patternUnits="userSpaceOnUse">
          <path d="M24 0H0V24" fill="none" stroke="var(--border)" strokeWidth="0.6" />
        </pattern>
      </defs>
      <rect width={W} height={H} fill="url(#gp)" opacity={0.6} />
      {grid.lines.map((l) => {
        const [x1, y1] = pos(l.a);
        const [x2, y2] = pos(l.b);
        return <line key={l.a + l.b} x1={x1} y1={y1} x2={x2} y2={y2} stroke="var(--border-strong)" strokeWidth={3} />;
      })}
      {grid.lines.map((l) => {
        const [x1, y1] = pos(l.a);
        const [x2, y2] = pos(l.b);
        return (
          <text key={"t" + l.a + l.b} x={(x1 + x2) / 2 + 6} y={(y1 + y2) / 2 - 6} fontSize={10} fill="var(--faint)">
            110 kV
          </text>
        );
      })}
      {grid.substations.map((s) => {
        const [x, y] = pos(s.id);
        const c = hiColor(s.worst_hi);
        return (
          <g key={s.id} className="map-node" onClick={() => onSubstation(s.id)}>
            <circle cx={x} cy={y} r={30} fill="var(--panel)" stroke={c} strokeWidth={2.5} />
            <circle cx={x} cy={y} r={24} fill={c} opacity={0.12} />
            <text x={x} y={y - 2} textAnchor="middle" fontSize={15} fontWeight={650} fill="var(--text)">
              {s.transformers.length}×
            </text>
            <text x={x} y={y + 13} textAnchor="middle" fontSize={9} fill="var(--muted)">
              {s.kv}
            </text>
            <text x={x} y={y + 48} textAnchor="middle" fontSize={13} fontWeight={600} fill="var(--text)">
              {s.id}
            </text>
            {s.awaiting_review > 0 && (
              <g>
                <circle cx={x + 24} cy={y - 24} r={10} fill="var(--warn)" />
                <text x={x + 24} y={y - 20} textAnchor="middle" fontSize={11} fontWeight={700} fill="#1a1204">
                  {s.awaiting_review}
                </text>
              </g>
            )}
            {s.data_trust !== "trusted" && (
              <text x={x} y={y + 62} textAnchor="middle" fontSize={9.5} fill="var(--bad)">
                data {s.data_trust}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export default function GridDashboard({
  onSubstation,
  onTransformer,
}: {
  onSubstation: (id: string) => void;
  onTransformer: (id: string) => void;
}) {
  const [grid, setGrid] = useState<GridView | null>(null);
  const [fleet, setFleet] = useState<Fleet | null>(null);
  useEffect(() => {
    getGrid().then(setGrid);
    getFleet().then(setFleet);
  }, []);
  if (!grid) return <div className="panel"><div className="tl-empty">Building the twin…</div></div>;
  const k = grid.kpis;
  return (
    <div className="stack">
      <div className="kpis">
        <Kpi label="Transformers" value={k.transformers} sub={`${k.substations} substations`} />
        <Kpi label="Mean health index" value={k.mean_health_index} tone={hiColor(k.mean_health_index)} />
        <Kpi label="Alarms awaiting review" value={k.awaiting_review} sub={`${k.alarms} alarms in total`} tone={k.awaiting_review ? "var(--warn)" : undefined} />
        <Kpi label="Customers behind poor units" value={k.customers_behind_poor_units.toLocaleString()} sub="substations with a unit below HI 50" />
        <Kpi label="Units with untrusted data" value={k.data_not_trusted} tone={k.data_not_trusted ? "var(--bad)" : undefined} />
      </div>
      <div className="panel">
        <div className="panel-head">
          <h2>{grid.name}</h2>
          <span className="hint">
            {grid.operator} · as of {grid.as_of.slice(0, 16).replace("T", " ")} · schematic, not geographic
          </span>
        </div>
        <div className="panel-body">
          <GridMap grid={grid} onSubstation={onSubstation} />
          <div className="legend">
            Ring colour: worst transformer health in the substation · badge: alarms awaiting a decision · click a
            substation to open it
          </div>
        </div>
      </div>
      <FleetTable fleet={fleet} selected={null} onSelect={onTransformer} />
    </div>
  );
}
