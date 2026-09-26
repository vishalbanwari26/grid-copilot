export interface UnitSummary {
  asset: string;
  substation: string;
  mva: number;
  kv: string;
  year: number;
  customers: number;
  health_index: number;
  risk: number;
  worst: string;
  action: string;
  alarm: { code: string; ts: string; text: string } | null;
  data_trust: string;
  decision: { verdict: string; reviewer: string; fault_class: string; reason: string; decided_at: string } | null;
  load_now_pu: number;
  load_peak_7d_pu: number;
  top_oil_now_c: number;
  mrid: string;
}

export interface SubstationNode {
  id: string;
  x: number;
  y: number;
  kv: string;
  role: string;
  transformers: string[];
  worst_hi: number;
  alarms: number;
  awaiting_review: number;
  customers: number;
  data_trust: string;
}

export interface GridView {
  id: string;
  name: string;
  operator: string;
  as_of: string;
  load_source: string;
  substations: SubstationNode[];
  lines: { a: string; b: string }[];
  kpis: Record<string, number>;
  ranking: UnitSummary[];
}

export interface SubstationView {
  id: string;
  kv: string;
  role: string;
  grid: string;
  neighbours: string[];
  transformers: UnitSummary[];
  combined_mva: number;
  mrid: string;
}

export interface ComponentNode {
  id: string;
  name: string;
  condition: number;
  reasons: string[];
  iec61850_ln: string;
  cim: string;
  mrid: string;
  signals: string[];
}

export interface SensorNode {
  id: string;
  name: string;
  iec61850_ln: string;
  channels: string[];
  grade: string;
  reasons: string[];
  last_calibration: string;
  calibration_due: string;
  calibration_overdue: boolean;
  installed: string;
  mrid: string;
}

export interface Lifecycle {
  age_years: number;
  design_life_years: number;
  insulation_life_used_pct: number;
  insulation_life_used_in_window_h: number;
  ageing_rate_30d: number;
  remaining_life_years_at_current_rate: number;
  remaining_life_capped: boolean;
  oltc_ops_since_service: number;
  oltc_service_interval_ops: number;
  oltc_last_service: string;
  oltc_ops_per_day: number;
  oltc_service_due: string;
  oltc_service_overdue: boolean;
  history: { date: string; text: string; kind: string }[];
  assumptions: string;
}

export interface TransformerView extends UnitSummary {
  lifecycle: Lifecycle;
  components: ComponentNode[];
  sensors: SensorNode[];
  subscores: Record<string, { score: number; reasons: string[] }>;
}

export interface ComponentSeries {
  values: number[];
  label: string;
  unit: string;
  ln: string;
  kind: string;
  grade: string;
  reasons: string[];
}

export interface ComponentView {
  asset: string;
  substation: string;
  kind: "component" | "sensor";
  id: string;
  name: string;
  iec61850_ln: string;
  mrid: string;
  condition?: number;
  grade?: string;
  reasons: string[];
  cim?: string;
  last_calibration?: string;
  calibration_due?: string;
  calibration_overdue?: boolean;
  series: Record<string, ComponentSeries>;
  ts: string[];
  alarms: { code: string; ts: string; text: string }[];
  oltc_ops_since_service?: number;
  oltc_service_interval_ops?: number;
  oltc_last_service?: string;
  oltc_ops_per_day?: number;
  oltc_service_due?: string;
  oltc_service_overdue?: boolean;
  recent_operations?: { ts: string; from: number; to: number; duration_s: number; motor_a: number; completed: boolean }[];
}

const j = <T,>(u: string) =>
  fetch(u).then((r) => {
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json() as Promise<T>;
  });

export const getGrid = () => j<GridView>("/api/twin/grid");
export const getSubstation = (id: string) => j<SubstationView>(`/api/twin/substation/${encodeURIComponent(id)}`);
export const getTransformer = (id: string) => j<TransformerView>(`/api/twin/transformer/${id}`);
export const getComponent = (a: string, c: string) => j<ComponentView>(`/api/twin/component/${a}/${c}`);
