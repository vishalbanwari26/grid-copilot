import type { RCAEvent } from "./api";

export interface SubScore {
  score: number;
  reasons: string[];
}

export interface FleetAsset {
  asset: string;
  substation: string;
  health_index: number;
  risk: number;
  worst: string;
  action: string;
  subscores: Record<string, SubScore>;
  mva: number;
  kv: string;
  year: number;
  customers: number;
  alarm: { code: string; ts: string; text: string } | null;
  data_trust: "trusted" | "suspect" | "untrusted";
  flagged_channels: string[];
  decision: { verdict: string; reviewer: string; fault_class: string; reason: string; decided_at: string } | null;
}

export interface Fleet {
  load_source: string;
  start: string;
  hours: number;
  assets: FleetAsset[];
}

export interface SeriesInfo {
  values: number[];
  label: string;
  unit: string;
  ref: string;
  grade: string;
  reasons: string[];
}

export interface AssetDetail {
  asset: string;
  plate: { asset: string; substation: string; mva: number; kv: string; year: number; customers: number };
  ts: string[];
  series: Record<string, SeriesInfo>;
  expected_top_oil: number[];
  alarms: { code: string; ts: string; text: string; value: number; limit: number; index: number }[];
  events: { ts: string; code: string; text: string; severity: string }[];
  duval: { zone: string; pct_ch4: number; pct_c2h4: number; pct_c2h2: number; meaning: string } | null;
  prpd: number[];
  first_alarm: string | null;
  tap_ops_total: number;
}

export interface Proposal {
  proposal_id: string;
  asset: string;
  alarm: string;
  alarm_ts: string;
  fault_class: string;
  subsystem: string;
  urgency: string;
  root_cause: string;
  recommended_action: string;
  would_change_conclusion: string;
  confidence: number;
  reasoning: string;
  evidence: { tool: string; summary: string; citations: string[] }[];
  data_quality: string;
  model: string;
  prompt_version: string;
  validation: string;
  critic: string;
}

export interface DecisionRow {
  decision_id: string;
  asset: string;
  alarm: string;
  proposed: string;
  final: string;
  verdict: string;
  reviewer: string;
  reason: string;
  decided_at: string;
  model: string;
  hash: string;
}

export interface DecisionLog {
  chain_ok: boolean;
  chain: string;
  records: DecisionRow[];
}

export const getFleet = () => fetch("/api/fleet").then((r) => r.json() as Promise<Fleet>);
export const getAsset = (a: string) => fetch(`/api/fleet/asset/${a}`).then((r) => r.json() as Promise<AssetDetail>);
export const getClasses = () => fetch("/api/fleet/classes").then((r) => r.json() as Promise<string[]>);
export const getDecisions = () => fetch("/api/fleet/decisions").then((r) => r.json() as Promise<DecisionLog>);
export const getSubmodel = (id: string) => fetch(`/api/fleet/decision/${id}/submodel`).then((r) => r.json());

export async function postDecision(body: {
  proposal_id: string;
  reviewer: string;
  verdict: string;
  reason: string;
  final_fault_class?: string;
}): Promise<{ record: { decision_id: string; hash: string; prev_hash: string }; submodel: unknown }> {
  const r = await fetch("/api/fleet/decision", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail ?? "decision failed");
  return data;
}

export function streamFleetInvestigation(
  asset: string,
  provider: string,
  onEvent: (e: RCAEvent) => void,
  onProposal: (p: Proposal) => void,
  onDone: (err?: string) => void,
  pace?: number,
): EventSource {
  const paceQ = pace === undefined ? "" : `&pace=${pace}`;
  const es = new EventSource(`/api/fleet/investigate?asset=${asset}&provider=${provider}${paceQ}`);
  es.addEventListener("event", (m) => onEvent(JSON.parse((m as MessageEvent).data)));
  es.addEventListener("proposal", (m) => {
    onProposal(JSON.parse((m as MessageEvent).data));
    es.close();
    onDone();
  });
  es.addEventListener("error", (m) => {
    const data = (m as MessageEvent).data;
    es.close();
    onDone(data ? JSON.parse(data).message : undefined);
  });
  return es;
}
