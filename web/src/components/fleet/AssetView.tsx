import type { AssetDetail, SeriesInfo } from "../../fleetApi";

function Trend({
  info,
  alarmIdx,
  overlay,
}: {
  info: SeriesInfo;
  alarmIdx: number;
  overlay?: number[];
}) {
  const W = 260;
  const H = 52;
  const pad = 4;
  const vals = info.values;
  const all = overlay ? vals.concat(overlay) : vals;
  const min = Math.min(...all);
  const max = Math.max(...all);
  const span = max - min || 1;
  const n = vals.length;
  const x = (i: number) => (i / (n - 1)) * W;
  const y = (v: number) => H - pad - ((v - min) / span) * (H - 2 * pad);
  const path = (s: number[]) => s.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = vals[vals.length - 1];
  const bad = info.grade !== "trusted" && info.grade !== "not_checked";
  return (
    <div className={`sig-card${bad ? " untrusted" : ""}`}>
      <div className="sig-top">
        <span className="sig-name" title={info.ref}>
          {info.label}
        </span>
        <span className="sig-val">
          {last.toFixed(info.unit === "pu" ? 2 : 1)} {info.unit}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none">
        {overlay && <path d={path(overlay)} fill="none" stroke="var(--faint)" strokeWidth={1.2} strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />}
        <path d={path(vals)} fill="none" stroke={bad ? "var(--bad)" : "var(--accent)"} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
        {alarmIdx >= 0 && alarmIdx < n && (
          <line x1={x(alarmIdx)} y1={0} x2={x(alarmIdx)} y2={H} stroke="var(--warn)" strokeDasharray="3 3" strokeWidth={1} opacity={0.8} />
        )}
      </svg>
      <div className="sig-foot">
        <span className="mono faint">{info.ref.split(" ")[0]}</span>
        {bad && (
          <span className="trust untrusted" title={info.reasons.join("; ")}>
            {info.grade}
          </span>
        )}
        {overlay && <span className="faint">dashed: healthy-cooling model</span>}
      </div>
    </div>
  );
}

// Duval triangle 1, drawn in barycentric coordinates (CH4 top, C2H4 right, C2H2 left).
const ZONES: { zone: string; pts: [number, number, number][]; color: string }[] = [
  { zone: "PD", pts: [[100, 0, 0], [98, 2, 0], [98, 0, 2]], color: "#a78bfa" },
  { zone: "T1", pts: [[98, 2, 0], [80, 20, 0], [76, 20, 4], [96, 0, 4], [98, 0, 2]], color: "#fde68a" },
  { zone: "T2", pts: [[80, 20, 0], [50, 50, 0], [46, 50, 4], [76, 20, 4]], color: "#fbbf24" },
  { zone: "T3", pts: [[50, 50, 0], [0, 100, 0], [0, 85, 15], [35, 50, 15]], color: "#f97316" },
  { zone: "D1", pts: [[87, 0, 13], [0, 0, 100], [0, 23, 77], [64, 23, 13]], color: "#38bdf8" },
  { zone: "D2", pts: [[64, 23, 13], [0, 23, 77], [0, 71, 29], [31, 40, 29], [47, 40, 13]], color: "#f87171" },
];

function Duval({ d }: { d: AssetDetail["duval"] }) {
  const W = 240;
  const H = 214;
  const T = [W / 2, 18];
  const R = [W - 18, H - 22];
  const L = [18, H - 22];
  const xy = ([m, e, a]: [number, number, number]) => [
    (m * T[0] + e * R[0] + a * L[0]) / 100,
    (m * T[1] + e * R[1] + a * L[1]) / 100,
  ];
  const poly = (pts: [number, number, number][]) => pts.map((p) => xy(p).join(",")).join(" ");
  const point = d ? xy([d.pct_ch4, d.pct_c2h4, d.pct_c2h2]) : null;
  return (
    <div className="duval">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H}>
        <polygon points={poly([[100, 0, 0], [0, 100, 0], [0, 0, 100]])} fill="#94a3b8" opacity={0.18} stroke="var(--border-strong)" />
        {ZONES.map((z) => (
          <g key={z.zone}>
            <polygon points={poly(z.pts)} fill={z.color} opacity={0.28} stroke="var(--bg)" strokeWidth={0.8} />
          </g>
        ))}
        {ZONES.map((z) => {
          const c = z.pts.map(xy).reduce((s, p) => [s[0] + p[0] / z.pts.length, s[1] + p[1] / z.pts.length], [0, 0]);
          return (
            <text key={z.zone} x={c[0]} y={c[1] + 3} fontSize={9} textAnchor="middle" fill="var(--muted)">
              {z.zone === "PD" ? "" : z.zone}
            </text>
          );
        })}
        <text x={T[0]} y={T[1] - 6} fontSize={9} textAnchor="middle" fill="var(--muted)">CH4 · PD</text>
        <text x={R[0]} y={R[1] + 15} fontSize={9} textAnchor="middle" fill="var(--muted)">C2H4</text>
        <text x={L[0]} y={L[1] + 15} fontSize={9} textAnchor="middle" fill="var(--muted)">C2H2</text>
        {point && (
          <>
            <circle cx={point[0]} cy={point[1]} r={7} fill="none" stroke="var(--text)" strokeWidth={1} opacity={0.5} />
            <circle cx={point[0]} cy={point[1]} r={3.5} fill="var(--text)" />
          </>
        )}
      </svg>
      <div className="duval-cap">
        {d ? (
          <>
            Gas added since baseline: zone <b>{d.zone}</b> ({d.meaning}) · CH4 {d.pct_ch4}% · C2H4 {d.pct_c2h4}% · C2H2 {d.pct_c2h2}%
          </>
        ) : (
          "No significant gas increase to classify."
        )}
      </div>
    </div>
  );
}

function Prpd({ hist }: { hist: number[] }) {
  const W = 240;
  const H = 110;
  const max = Math.max(...hist, 1);
  const bw = W / hist.length;
  const sine = Array.from({ length: 61 }, (_, i) => {
    const x = (i / 60) * W;
    return `${i ? "L" : "M"}${x.toFixed(1)},${(H / 2 - Math.sin((i / 60) * 2 * Math.PI) * (H / 2 - 8)).toFixed(1)}`;
  }).join(" ");
  const total = hist.reduce((a, b) => a + b, 0);
  return (
    <div className="duval">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none">
        <path d={sine} fill="none" stroke="var(--faint)" strokeWidth={1} strokeDasharray="2 3" vectorEffect="non-scaling-stroke" />
        {hist.map((v, i) => {
          const h = (v / max) * (H - 10);
          return <rect key={i} x={i * bw + 2} y={H - h} width={bw - 4} height={h} fill="var(--violet)" opacity={0.75} rx={2} />;
        })}
      </svg>
      <div className="duval-cap">
        {total > 0
          ? "PD pulses by phase of the voltage cycle, last 7 days (0° to 360°, dashed: voltage)"
          : "No PD activity above 100 pC in the last 7 days."}
      </div>
    </div>
  );
}

const TRENDS = ["top_oil_c", "hotspot_c", "load_pu", "h2_ppm", "c2h2_ppm", "c2h4_ppm", "pd_pc", "oltc_temp_diff_c"];

export default function AssetView({ detail }: { detail: AssetDetail | null }) {
  if (!detail) return <div className="panel"><div className="tl-empty">Pick a transformer from the ranking.</div></div>;
  const alarmIdx = detail.alarms.length ? detail.alarms[0].index : -1;
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>
          {detail.asset} · {detail.plate.substation}
        </h2>
        <span className="hint">
          {detail.plate.mva} MVA · {detail.plate.kv} · built {detail.plate.year} · {detail.plate.customers.toLocaleString()} customers
        </span>
      </div>
      <div className="panel-body">
        <div className="signals three">
          {TRENDS.map((c) => (
            <Trend key={c} info={detail.series[c]} alarmIdx={alarmIdx} overlay={c === "top_oil_c" ? detail.expected_top_oil : undefined} />
          ))}
        </div>
        <div className="diag-row">
          <div>
            <div className="section-label">Duval triangle 1</div>
            <Duval d={detail.duval} />
          </div>
          <div>
            <div className="section-label">Phase-resolved PD</div>
            <Prpd hist={detail.prpd} />
          </div>
          <div>
            <div className="section-label">Alarms and events</div>
            <div className="events">
              {detail.alarms.map((a) => (
                <div className="ev alarm" key={a.code + a.ts}>
                  <span className="mono">{a.ts.slice(5, 16).replace("T", " ")}</span> <b>{a.code}</b> {a.text}
                </div>
              ))}
              {detail.events
                .filter((e) => e.severity !== "alarm")
                .slice(-6)
                .map((e) => (
                  <div className="ev" key={e.code + e.ts}>
                    <span className="mono">{e.ts.slice(5, 16).replace("T", " ")}</span> {e.code}
                  </div>
                ))}
              {detail.alarms.length === 0 && <div className="faint">No alarms.</div>}
              <div className="faint small">{detail.tap_ops_total.toLocaleString()} tap changes in the record</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
