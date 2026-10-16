import { apiGet, apiPost, openStream, type Stream } from "./transport";

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

export interface OverridePattern {
  pattern_id: string;
  proposed: string;
  final: string;
  assets: string[];
  n_assets: number;
  reasons: string[];
  tools_cited: string[];
  prompt_versions: string[];
  change_request: string;
  first_at: string;
  last_at: string;
  status: string;
  reviewer: string | null;
  reason: string | null;
  decided_at: string | null;
  assets_at_review: number | null;
  new_since_review: number;
}

export interface PatternList {
  min_assets: number;
  chain_ok: boolean;
  chain: string;
  patterns: OverridePattern[];
}

export const getPatterns = () => apiGet<PatternList>("/api/fleet/patterns");

export const postPatternReview = (body: { pattern_id: string; reviewer: string; verdict: string; reason: string }) =>
  apiPost<{ review: unknown }>("/api/fleet/patterns/review", body);

export const getFleet = () => apiGet<Fleet>("/api/fleet");
export const getAsset = (a: string) => apiGet<AssetDetail>(`/api/fleet/asset/${a}`);
export const getClasses = () => apiGet<string[]>("/api/fleet/classes");
export const getDecisions = () => apiGet<DecisionLog>("/api/fleet/decisions");
export const getSubmodel = (id: string) => apiGet<unknown>(`/api/fleet/decision/${id}/submodel`);

export async function postDecision(body: {
  proposal_id: string;
  reviewer: string;
  verdict: string;
  reason: string;
  final_fault_class?: string;
}): Promise<{ record: { decision_id: string; hash: string; prev_hash: string }; submodel: unknown }> {
  return apiPost("/api/fleet/decision", body);
}

export function streamFleetInvestigation(
  asset: string,
  provider: string,
  onEvent: (e: RCAEvent) => void,
  onProposal: (p: Proposal) => void,
  onDone: (err?: string) => void,
  pace?: number,
): Stream {
  const paceQ = pace === undefined ? "" : `&pace=${pace}`;
  const es = openStream(`/api/fleet/investigate?asset=${asset}&provider=${provider}${paceQ}`, pace);
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
