export function hiColor(v: number) {
  return v >= 85 ? "var(--good)" : v >= 55 ? "var(--warn)" : "var(--bad)";
}

export function Kpi({ label, value, sub, tone }: { label: string; value: string | number; sub?: string; tone?: string }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value" style={tone ? { color: tone } : undefined}>
        {value}
      </div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

export function Spark({ values, bad, height = 46 }: { values: number[]; bad?: boolean; height?: number }) {
  const W = 260;
  const H = height;
  const pad = 4;
  if (!values.length) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (i: number) => (i / Math.max(1, values.length - 1)) * W;
  const y = (v: number) => H - pad - ((v - min) / span) * (H - 2 * pad);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none">
      <path d={d} fill="none" stroke={bad ? "var(--bad)" : "var(--accent)"} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function Trust({ grade, title }: { grade: string; title?: string }) {
  return (
    <span className={`trust ${grade}`} title={title}>
      {grade.replace("_", " ")}
    </span>
  );
}

export function HealthBar({ value }: { value: number }) {
  return (
    <div className="hi">
      <div className="hi-bar">
        <div className="hi-fill" style={{ width: `${value}%`, background: hiColor(value) }} />
      </div>
      <span className="mono">{value}</span>
    </div>
  );
}
