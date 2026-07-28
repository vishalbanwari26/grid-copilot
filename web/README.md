# Grid Copilot dashboard

A React dashboard for the Grid Copilot pipeline. It talks to a small FastAPI
backend (`grid_copilot/server.py`) that runs the **real** detector and
`Investigator` and streams the actual `EventBus` events over Server-Sent Events,
so the UI shows the agent's reasoning unfold live: telemetry with the anomaly
highlighted, the streaming investigation timeline, the cited incident report, and
the HAI detector benchmark.

## Run it

Two processes. From the repository root, start the backend:

```bash
pip install -e ".[web]"                       # fastapi + uvicorn
uvicorn grid_copilot.server:app --port 8000
```

Then, in `web/`, start the frontend (Vite proxies `/api` to the backend):

```bash
npm install
npm run dev            # http://localhost:5173
```

Pick a fault, hit **Run investigation**, and watch the agent work. Switch the
provider to `live (groq)` to run it on a real model (needs `GROQ_API_KEY` in the
repo's `.env`).

## Handy URL params

- `?autorun=1` runs the investigation immediately on load (a shareable deep link).
- `?pace=0` streams the offline events with no artificial delay (used to capture
  the README screenshot).

## Build

```bash
npm run build         # static assets in dist/
```
