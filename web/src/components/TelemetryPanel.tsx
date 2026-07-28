import type { Signal, Telemetry } from "../api";

function Sparkline({
  values,
  window,
  anomaly,
  faulted,
}: {
  values: number[];
  window: [number, number];
  anomaly: number;
  faulted: boolean;
}) {
  const W = 240;
  const H = 54;
  const pad = 4;
  const n = values.length;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (i: number) => (i / (n - 1)) * W;
  const y = (v: number) => H - pad - ((v - min) / span) * (H - 2 * pad);
  const path = values.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const stroke = faulted ? "var(--warn)" : "var(--accent)";

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none">
      <rect
        x={x(window[0])}
        y={0}
        width={x(window[1]) - x(window[0])}
        height={H}
        fill="var(--warn)"
        opacity={0.07}
      />
      <path d={path} fill="none" stroke={stroke} strokeWidth={1.6} vectorEffect="non-scaling-stroke" />
      {anomaly >= 0 && (
        <>
          <line x1={x(anomaly)} y1={0} x2={x(anomaly)} y2={H} stroke="var(--warn)" strokeWidth={1} strokeDasharray="3 3" opacity={0.7} />
          <circle cx={x(anomaly)} cy={y(values[anomaly])} r={2.6} fill="var(--warn)" />
        </>
      )}
    </svg>
  );
}

function SignalCard({ signal, telemetry }: { signal: Signal; telemetry: Telemetry }) {
  const v = signal.values;
  const last = v[v.length - 1];
  const first = v[0];
  const delta = last - first;
  return (
    <div className={`sig-card${signal.faulted ? " faulted" : ""}`}>
      <div className="sig-top">
        <span className="sig-name">{signal.name}</span>
        {signal.faulted ? (
          <span className="sig-tag">fault</span>
        ) : (
          <span className="sig-val">
            {delta >= 0 ? "+" : ""}
            {delta.toFixed(2)}
          </span>
        )}
      </div>
      <Sparkline
        values={v}
        window={telemetry.fault_window}
        anomaly={telemetry.anomaly_index}
        faulted={signal.faulted}
      />
    </div>
  );
}

export default function TelemetryPanel({ telemetry }: { telemetry: Telemetry | null }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Telemetry — {telemetry?.asset ?? "…"}</h2>
        <span className="hint">
          {telemetry ? `${telemetry.sample_count} samples · anomaly @ ${telemetry.anomaly_index}` : ""}
        </span>
      </div>
      <div className="panel-body">
        {telemetry ? (
          <div className="signals">
            {telemetry.signals.map((s) => (
              <SignalCard key={s.name} signal={s} telemetry={telemetry} />
            ))}
          </div>
        ) : (
          <div className="tl-empty">Loading telemetry…</div>
        )}
      </div>
    </div>
  );
}
