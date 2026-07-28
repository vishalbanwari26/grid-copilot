import type { RCAEvent } from "../api";

const MAP: Record<string, { cls: string; icon: string; kind: string }> = {
  anomaly_detected: { cls: "anomaly", icon: "!", kind: "Anomaly detected" },
  tool_called: { cls: "tool", icon: "→", kind: "Tool call" },
  tool_result: { cls: "result", icon: "·", kind: "Evidence" },
  hypothesis: { cls: "hypothesis", icon: "=", kind: "Hypothesis" },
  critique: { cls: "", icon: "?", kind: "Critic review" },
  report_ready: { cls: "report", icon: "✓", kind: "Report ready" },
  aborted: { cls: "", icon: "×", kind: "Aborted" },
};

export default function InvestigationTimeline({
  events,
  running,
}: {
  events: RCAEvent[];
  running: boolean;
}) {
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Investigation</h2>
        <span className="hint">{running ? "streaming…" : events.length ? `${events.length} steps` : ""}</span>
      </div>
      {events.length === 0 ? (
        <div className="tl-empty">
          {running ? "Starting investigation…" : "Run an investigation to watch the agent reason step by step."}
        </div>
      ) : (
        <div className="timeline">
          {events.map((e, i) => {
            const m = MAP[e.type] ?? { cls: "", icon: "•", kind: e.type };
            const citations = (e.payload?.citations as string[]) ?? [];
            const last = i === events.length - 1;
            return (
              <div className={`tl-item ${m.cls}`} key={i}>
                <div className="tl-rail">
                  <div className="tl-dot">{m.icon}</div>
                  {!last && <div className="tl-line" />}
                </div>
                <div className="tl-body">
                  <div className="tl-kind">
                    {m.kind}
                    {e.type === "tool_called" && e.payload?.tool ? ` · ${e.payload.tool}` : ""}
                  </div>
                  <div className="tl-msg">{e.message}</div>
                  {citations.length > 0 && (
                    <div>
                      {citations.map((c) => (
                        <span className="chip" key={c}>
                          {c}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
