// How the dashboard reaches the backend.
//
// Normally every call goes to the FastAPI server. For the static GitHub Pages
// demo (VITE_STATIC=1) the same calls read a recording of that server
// (scripts/record_demo.py), investigation streams are replayed event by event,
// and decisions and pattern reviews are kept in this browser with the same
// rules as grid_copilot/transformer/decisions.py and patterns.py.

export const STATIC_DEMO = import.meta.env.VITE_STATIC === "1";
const BASE = import.meta.env.BASE_URL;

/** Same mapping as scripts/record_demo.py:file_for. */
function recorded(path: string): string {
  const [p, q = ""] = path.replace(/^\/api\//, "").split("?");
  const keep = q.split("&").filter((kv) => kv && !kv.startsWith("provider=") && !kv.startsWith("pace="));
  return `${BASE}demo-data/${p || "index"}${keep.map((kv) => "__" + kv.replace("=", "-")).join("")}.json`;
}

async function readRecording<T>(path: string): Promise<T> {
  const r = await fetch(recorded(decodeURIComponent(path)));
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json() as Promise<T>;
}

export async function apiGet<T>(path: string): Promise<T> {
  if (!STATIC_DEMO) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json() as Promise<T>;
  }
  if (path.startsWith("/api/fleet/decisions")) return local.decisionLog() as T;
  if (path.startsWith("/api/fleet/patterns")) return local.patternList() as T;
  if (/^\/api\/fleet\/decision\/[^/]+\/submodel/.test(path)) return local.submodel(path.split("/")[4]) as T;
  const data = await readRecording<T>(path);
  if (path === "/api/fleet") return local.overlayFleet(data) as T;
  return data;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  if (!STATIC_DEMO) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail ?? `${r.status}`);
    return data as T;
  }
  if (path === "/api/fleet/decision") return (await local.decide(body as DecisionIn)) as T;
  if (path === "/api/fleet/patterns/review") return (await local.reviewPattern(body as ReviewIn)) as T;
  throw new Error("not available in the static demo");
}

/** The part of EventSource the dashboard uses. */
export interface Stream {
  addEventListener(type: string, cb: (m: MessageEvent) => void): void;
  close(): void;
}

/** A live SSE connection, or a replay of the recorded one at the mock agent's pace. */
export function openStream(path: string, pace = 0.35): Stream {
  if (!STATIC_DEMO) return new EventSource(path);
  const handlers: Record<string, ((m: MessageEvent) => void)[]> = {};
  let closed = false;
  const fire = (type: string, data: unknown) =>
    (handlers[type] ?? []).forEach((cb) => cb(new MessageEvent(type, { data: JSON.stringify(data) })));
  (async () => {
    let events: { event: string; data: { proposal_id?: string } }[];
    try {
      events = await readRecording(path);
    } catch {
      return fire("error", { message: "This investigation is not in the recorded demo." });
    }
    for (const e of events) {
      if (closed) return;
      if (e.event === "proposal") local.rememberProposal(e.data as Proposal);
      fire(e.event, e.data);
      if (e.event === "event") await new Promise((r) => setTimeout(r, Math.max(pace, 0.12) * 1000));
    }
  })();
  return {
    addEventListener: (type, cb) => { (handlers[type] ??= []).push(cb); },
    close: () => { closed = true; },
  };
}

// ---------- decisions and pattern reviews kept in the browser ----------

const GENESIS = "0".repeat(64);
const VERDICTS = ["accept", "override", "defer"];
const REVIEW_VERDICTS = ["approve_change", "reject", "keep_watching"];
const MIN_ASSETS = 3;

interface Proposal { proposal_id: string; asset: string; alarm: string; fault_class: string; recommended_action: string;
  model: string; prompt_version: string; evidence: { tool: string }[]; [k: string]: unknown }
interface DecisionIn { proposal_id: string; reviewer: string; verdict: string; reason?: string; final_fault_class?: string; final_action?: string }
interface ReviewIn { pattern_id: string; reviewer: string; verdict: string; reason?: string }
interface Decision { decision_id: string; proposal: Proposal; reviewer: string; verdict: string; final_fault_class: string;
  final_action: string; reason: string; decided_at: string; prev_hash: string; hash: string }
interface Review { review_id: string; pattern_id: string; pattern: { assets: string[] }; reviewer: string; verdict: string;
  reason: string; decided_at: string; prev_hash: string; hash: string }

const read = <T,>(k: string, d: T): T => { try { return JSON.parse(localStorage.getItem(k) ?? "") ?? d; } catch { return d; } };
const write = (k: string, v: unknown) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } };
const now = () => new Date().toISOString().replace(/\.\d+Z$/, "+00:00");

function canonical(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonical).join(",")}]`;
  if (v && typeof v === "object")
    return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canonical((v as Record<string, unknown>)[k])}`).join(",")}}`;
  return JSON.stringify(v);
}
async function sha256(obj: unknown): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical(obj)));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
async function chainOk<T extends { prev_hash: string; hash: string }>(recs: T[]): Promise<[boolean, string]> {
  let prev = GENESIS;
  for (const [i, r] of recs.entries()) {
    if (r.prev_hash !== prev) return [false, `record ${i} does not follow the record before it`];
    const { hash, ...body } = r;
    if ((await sha256(body)) !== hash) return [false, `record ${i} was changed after it was written`];
    prev = r.hash;
  }
  return [true, "chain intact"];
}

function findPatterns(decisions: Decision[], minAssets = MIN_ASSETS) {
  const groups = new Map<string, Decision[]>();
  for (const d of decisions) {
    if (d.verdict !== "override" || d.proposal.fault_class === d.final_fault_class) continue;
    const k = `${d.proposal.fault_class}->${d.final_fault_class}`;
    groups.set(k, [...(groups.get(k) ?? []), d]);
  }
  const out = [];
  for (const [id, recs] of groups) {
    const assets = [...new Set(recs.map((r) => r.proposal.asset))].sort();
    if (assets.length < minAssets) continue;
    const [proposed, final] = id.split("->");
    const tools = [...new Set(recs.flatMap((r) => r.proposal.evidence.map((e) => e.tool)).filter(Boolean))].sort();
    const prompts = [...new Set(recs.map((r) => r.proposal.prompt_version).filter(Boolean))].sort();
    const times = recs.map((r) => r.decided_at).sort();
    out.push({
      pattern_id: id, proposed, final, assets, n_assets: assets.length, decision_ids: recs.map((r) => r.decision_id),
      reasons: recs.map((r) => r.reason).filter(Boolean), tools_cited: tools, prompt_versions: prompts,
      first_at: times[0], last_at: times[times.length - 1],
      change_request: `On ${assets.length} transformers (${assets.join(", ")}) the agent proposed ${proposed} and reviewers decided ${final}. ` +
        `Review why the shared logic reaches ${proposed} here: the tools it relied on (${tools.join(", ") || "none recorded"}) and the ` +
        `investigator prompt (${prompts.join(", ") || "unknown"}). Read each overridden case before changing anything; a fix ships as a new ` +
        `tool or prompt version and is re-evaluated.`,
    });
  }
  return out.sort((a, b) => b.n_assets - a.n_assets || a.pattern_id.localeCompare(b.pattern_id));
}

const local = {
  rememberProposal(p: Proposal) { const all = read<Record<string, Proposal>>("gc-proposals", {}); all[p.proposal_id] = p; write("gc-proposals", all); },
  decisions: () => read<Decision[]>("gc-decisions", []),
  reviews: () => read<Review[]>("gc-pattern-reviews", []),

  async decide(b: DecisionIn) {
    const proposal = read<Record<string, Proposal>>("gc-proposals", {})[b.proposal_id];
    if (!proposal) throw new Error("unknown or expired proposal; run the investigation again");
    const reviewer = (b.reviewer ?? "").trim(), reason = (b.reason ?? "").trim();
    if (!reviewer) throw new Error("a decision needs a named reviewer");
    if (!VERDICTS.includes(b.verdict)) throw new Error(`verdict must be one of ${VERDICTS.join(", ")}`);
    if ((b.verdict === "override" || b.verdict === "defer") && !reason) throw new Error(`an ${b.verdict} needs a reason`);
    if (b.verdict === "override" && !b.final_fault_class) throw new Error("an override needs the fault class the reviewer decided on");
    const prior = local.decisions();
    const body = { decision_id: crypto.randomUUID(), proposal, reviewer, verdict: b.verdict,
      final_fault_class: b.verdict === "override" ? b.final_fault_class! : proposal.fault_class,
      final_action: b.final_action || proposal.recommended_action, reason, decided_at: now(),
      prev_hash: prior.length ? prior[prior.length - 1].hash : GENESIS };
    const record: Decision = { ...body, hash: await sha256(body) };
    write("gc-decisions", [...prior, record]);
    return { record, submodel: null };
  },

  async decisionLog() {
    const recs = local.decisions(), [ok, why] = await chainOk(recs);
    return { chain_ok: ok, chain: why, records: [...recs].reverse().map((r) => ({
      decision_id: r.decision_id, asset: r.proposal.asset, alarm: r.proposal.alarm, proposed: r.proposal.fault_class,
      final: r.final_fault_class, verdict: r.verdict, reviewer: r.reviewer, reason: r.reason, decided_at: r.decided_at,
      model: r.proposal.model, hash: r.hash.slice(0, 12) })) };
  },

  submodel(id: string) {
    const r = local.decisions().find((d) => d.decision_id === id);
    return r ? { note: "The AAS submodel export runs on the server; this static demo shows the signed record.", record: r } : { error: "unknown decision" };
  },

  status(p: { pattern_id: string; assets: string[]; n_assets: number }) {
    const last = local.reviews().filter((r) => r.pattern_id === p.pattern_id).pop();
    if (!last) return { status: "open", reviewer: null, reason: null, decided_at: null, assets_at_review: null, new_since_review: p.n_assets };
    const seen = new Set(last.pattern.assets);
    return { status: last.verdict, reviewer: last.reviewer, reason: last.reason, decided_at: last.decided_at,
      assets_at_review: seen.size, new_since_review: p.assets.filter((a) => !seen.has(a)).length };
  },

  async patternList() {
    const [ok, why] = await chainOk(local.reviews());
    return { min_assets: MIN_ASSETS, chain_ok: ok, chain: why,
      patterns: findPatterns(local.decisions()).map((p) => ({ ...p, ...local.status(p) })) };
  },

  async reviewPattern(b: ReviewIn) {
    const pattern = findPatterns(local.decisions()).find((p) => p.pattern_id === b.pattern_id);
    if (!pattern) throw new Error("no such pattern in the current decision log");
    const reviewer = (b.reviewer ?? "").trim(), reason = (b.reason ?? "").trim();
    if (!reviewer) throw new Error("a fleet-level review needs a named reviewer");
    if (!REVIEW_VERDICTS.includes(b.verdict)) throw new Error(`verdict must be one of ${REVIEW_VERDICTS.join(", ")}`);
    if (!reason) throw new Error("a fleet-level review needs a reason");
    const prior = local.reviews();
    const body = { review_id: crypto.randomUUID(), pattern_id: pattern.pattern_id, pattern, reviewer, verdict: b.verdict,
      reason, decided_at: now(), prev_hash: prior.length ? prior[prior.length - 1].hash : GENESIS };
    const review: Review = { ...body, hash: await sha256(body) };
    write("gc-pattern-reviews", [...prior, review]);
    return { review };
  },

  /** The fleet table shows each transformer's latest signed decision. */
  overlayFleet<T>(fleet: T): T {
    const latest = new Map<string, Decision>();
    for (const d of local.decisions()) latest.set(d.proposal.asset, d);
    const f = fleet as unknown as { assets: { asset: string; decision: unknown }[] };
    f.assets = f.assets.map((a) => {
      const d = latest.get(a.asset);
      return d ? { ...a, decision: { verdict: d.verdict, reviewer: d.reviewer, fault_class: d.final_fault_class,
        reason: d.reason, decided_at: d.decided_at } } : a;
    });
    return fleet;
  },
};
