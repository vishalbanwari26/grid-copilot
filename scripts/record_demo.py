"""Record a static copy of the dashboard's API for the GitHub Pages demo.

Start the server with the mock agent, then run this script. It walks every GET
endpoint the dashboard uses (fleet, assets, digital twin, OT demo) and records
each investigation stream as a list of server-sent events, so the static build
can replay them in the browser.

    GRID_COPILOT_PROVIDER=mock GRID_COPILOT_DATA=$(mktemp -d) \
        uvicorn grid_copilot.server:app --port 8799 &
    python scripts/record_demo.py http://127.0.0.1:8799 web/demo-data

Decisions and pattern reviews are not recorded: the static demo keeps those in
the visitor's browser (web/src/transport.ts).
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def file_for(path: str) -> str:
    """/api/fleet/asset/TR-01 -> fleet/asset/TR-01.json; query keys become a
    suffix (provider and pace are ignored, the demo always replays the mock)."""
    p, _, q = path.removeprefix("/api/").partition("?")
    keep = [kv for kv in q.split("&") if kv and not kv.startswith(("provider=", "pace="))]
    suffix = "".join("__" + kv.replace("=", "-") for kv in keep)
    return f"{p or 'index'}{suffix}.json"


class Recorder:
    def __init__(self, base: str, out: Path) -> None:
        self.base, self.out, self.count = base.rstrip("/"), out, 0

    def _save(self, path: str, data) -> None:
        f = self.out / file_for(path)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, separators=(",", ":"), default=str))
        self.count += 1

    def get(self, path: str):
        with urllib.request.urlopen(self.base + path, timeout=120) as r:
            data = json.load(r)
        self._save(path, data)
        return data

    def stream(self, path: str) -> list[dict]:
        events, kind = [], None
        with urllib.request.urlopen(self.base + path, timeout=300) as r:
            for raw in r:
                line = raw.decode().rstrip("\n")
                if line.startswith("event: "):
                    kind = line[7:]
                elif line.startswith("data: ") and kind:
                    events.append({"event": kind, "data": json.loads(line[6:])})
                    kind = None
        self._save(path, events)
        return events


def main(base: str, out_dir: str) -> None:
    rec = Recorder(base, Path(out_dir))
    q = urllib.parse.quote

    # OT demo (the original page): faults, telemetry and one investigation each.
    rec.get("/api/eval")
    for fault in rec.get("/api/faults"):
        name = fault["name"] if isinstance(fault, dict) else fault
        rec.get(f"/api/telemetry?fault={q(name)}")
        rec.stream(f"/api/investigate?fault={q(name)}&provider=mock&pace=0")

    # Transformer fleet: every asset, and an investigation for each one with an alarm.
    rec.get("/api/fleet/classes")
    fleet = rec.get("/api/fleet")
    for a in fleet["assets"]:
        rec.get(f"/api/fleet/asset/{q(a['asset'])}")
        if a.get("alarm"):
            rec.stream(f"/api/fleet/investigate?asset={q(a['asset'])}&provider=mock&pace=0")

    # Digital twin: grid -> substations -> transformers -> components.
    grid = rec.get("/api/twin/grid")
    for s in grid["substations"]:
        rec.get(f"/api/twin/substation/{q(s['id'])}")
    for a in fleet["assets"]:
        tr = rec.get(f"/api/twin/transformer/{q(a['asset'])}")
        for c in tr.get("components", []):
            rec.get(f"/api/twin/component/{q(a['asset'])}/{q(c['id'])}")

    print(f"recorded {rec.count} responses into {out_dir}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
