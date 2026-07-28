export interface Fault {
  name: string;
  asset: string;
  label: string;
  blurb: string;
}

export interface Signal {
  name: string;
  faulted: boolean;
  values: number[];
}

export interface Telemetry {
  asset: string;
  fault_window: [number, number];
  anomaly_index: number;
  sample_count: number;
  signals: Signal[];
}

export interface RCAEvent {
  type: string;
  message: string;
  payload: Record<string, unknown>;
}

export interface Evidence {
  source: string;
  summary: string;
  citations: string[];
}

export interface Report {
  asset: string;
  trigger_signal: string;
  score: number;
  root_cause: string;
  confidence: number;
  reasoning: string;
  verdict: string;
  evidence: Evidence[];
  references: string[];
  narrative: string;
  ground_truth: string | null;
  correct: boolean;
}

export interface Detector {
  name: string;
  precision: number;
  recall: number;
  f1_adj: number;
  f1_raw: number;
}

export interface EvalData {
  dataset: string;
  detectors: Detector[];
  note: string;
}

export const getFaults = () => fetch("/api/faults").then((r) => r.json() as Promise<Fault[]>);
export const getEval = () => fetch("/api/eval").then((r) => r.json() as Promise<EvalData>);
export const getTelemetry = (fault: string) =>
  fetch(`/api/telemetry?fault=${fault}`).then((r) => r.json() as Promise<Telemetry>);

/** Open the investigation SSE stream. Returns the EventSource so the caller can close it. */
export function streamInvestigation(
  fault: string,
  provider: string,
  onEvent: (e: RCAEvent) => void,
  onReport: (r: Report) => void,
  onDone: (err?: string) => void,
  pace?: number,
): EventSource {
  const paceQ = pace === undefined ? "" : `&pace=${pace}`;
  const es = new EventSource(`/api/investigate?fault=${fault}&provider=${provider}${paceQ}`);
  es.addEventListener("event", (m) => onEvent(JSON.parse((m as MessageEvent).data)));
  es.addEventListener("report", (m) => {
    onReport(JSON.parse((m as MessageEvent).data));
    es.close();
    onDone();
  });
  es.addEventListener("error", (m) => {
    // A normal end-of-stream also fires 'error' on EventSource; only surface a
    // real backend error (which carries a data payload).
    const data = (m as MessageEvent).data;
    es.close();
    onDone(data ? JSON.parse(data).message : undefined);
  });
  return es;
}
