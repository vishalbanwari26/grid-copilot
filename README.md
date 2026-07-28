# Grid Copilot

Anomaly detection plus agentic root-cause analysis on industrial/grid telemetry.
It ingests operational-technology (OT) time-series, detects an anomaly, then lets
an agent investigate it, gathering evidence from the telemetry window, from
equipment and protocol documentation, and from memory of prior incidents on the
same asset, and produces a root-cause report where every claim is cited.

Everything here runs on **public and synthetic data**, by design. It is a
rebuild, on inspectable data, of the kind of anomaly-detection and root-cause
work that is otherwise stuck behind a confidentiality clause. Nothing in this
repository is derived from any employer's data or code.

## Demo

A live run on Groq's `gpt-oss-120b`: the detector flags a bearing-temperature
anomaly on `turbine_1`, and the agent investigates it, writing its own tool
queries as it calls `query_telemetry`, `retrieve_docs`, and `recall_incident`,
then concludes an incipient bearing fault that matches the injected ground truth,
with every step streamed.

![Live agentic RCA on Groq: the agent detects a bearing-temperature anomaly on turbine_1, calls query_telemetry, retrieve_docs and recall_incident with its own queries, then concludes a degrading bearing thermal fault matching the injected ground truth](assets/demo-live-rca.gif)

```bash
python -m grid_copilot.cli            # live by default (needs GROQ_API_KEY); --provider mock to run offline
```

## Dashboard

A React dashboard (`web/`) over a FastAPI backend that runs the real pipeline and
streams the actual investigation events over SSE: live telemetry with the anomaly
highlighted, the agent's step-by-step reasoning, the cited incident report, and
the HAI detector benchmark. Setup and details in [web/README.md](web/README.md).

![Grid Copilot dashboard: telemetry sparklines for turbine_1 with the two faulted signals highlighted, the streaming investigation timeline calling query_telemetry, retrieve_docs and recall_incident, a cited incident report concluding an incipient bearing fault that matches ground truth, and the HAI detector benchmark table](assets/dashboard.png)

```bash
pip install -e ".[web]" && uvicorn grid_copilot.server:app --port 8000   # backend
cd web && npm install && npm run dev                                     # frontend
```

## The loop

```mermaid
flowchart LR
    T[Telemetry stream] --> D[Detector]
    D -->|anomaly| I[Investigator agent]
    I -->|pick a tool| TL[Tools:<br/>query_telemetry<br/>retrieve_docs<br/>recall_incident]
    TL -->|evidence| I
    I -->|hypothesis| C[Critic agent]
    C -->|revise| I
    C -->|accept| R[Cited incident report]
    R --> M[(Incident memory<br/>keyed by asset)]
    M -.recall.-> TL
```

The detector is a fixed-baseline z-score with a persistence guard (below). The
agent runs a bounded investigation: each round it either calls one tool to
gather more evidence or concludes with a root cause, then a critic accepts the
hypothesis or asks for a revision. Every step emits an event, so the reasoning is
observable as it happens, not just at the end.

## Built on three repos

The point of the design is that it is not one weekend project, it is three pieces
of infrastructure wired into one system:

- **[Cortex](../cortex)** supplies the orchestration primitives: the
  provider-agnostic `LLMClient` layer (mock, Anthropic, Groq), the `Agent` base
  with tolerant JSON parsing, and the `EventBus` that makes the run observable.
  The investigation loop here is the domain equivalent of Cortex's
  perceive-plan-execute-critique orchestrator, retargeted to root-cause analysis.
- **[Mnemos](../mnemos)** supplies per-asset incident memory. The mapping is the
  trick: an asset id becomes a Mnemos `user_id`, so "what has gone wrong on
  `turbine_1` before" is literally `recall(user_id="turbine_1", ...)`. It sits
  behind an `IncidentStore` interface with a zero-dependency local store, so the
  offline demo needs no database.
- **Grid Copilot** (this repo) is the domain system: ingest, detector, tools,
  the investigation loop, the eval harness.

## Run it

Live by default: it runs on a hosted model, with the key in a local `.env`
(gitignored). No dataset download, no database.

```bash
python -m venv .venv && source .venv/bin/activate
pip install ../cortex           # Cortex has no dependencies of its own
pip install -e ".[groq]"        # or ".[live]" for Anthropic
echo "GROQ_API_KEY=..." > .env   # or ANTHROPIC_API_KEY=...
python -m grid_copilot.cli       # add --provider mock for a keyless offline run
```

Sample live run (a bearing overheating on `turbine_1`, injected into
otherwise-nominal data), the agent writing its own tool queries:

```
 [!] bearing_temp_c on turbine_1 (score 5.68)
 [>>] query_telemetry('') — to see how bearing_temp_c and other signals behaved
   -> bearing_temp_c rose +10.38 (65.1->75.5); vibration_mm_s rose +2.18 (2.5->4.6).
      Stable: rotor_speed_rpm, output_mw. Onset: bearing_temp_c at +197, vibration at +204.
 [>>] retrieve_docs('bearing temperature vibration turbine fault') — link the co-rise to a signature
   -> Closest reference 'Bearing thermal-fault signature': a degrading bearing shows a
      slow rise in temperature that tracks together with rising vibration.
 [>>] recall_incident('turbine_1 bearing temperature vibration spike') — has this happened before?
   -> No prior incidents recorded for turbine_1.
 [=] Degrading rolling-element bearing causing thermal fault
 [?] accept
 [done] INC-0001: turbine_1: Degrading rolling-element bearing causing thermal fault
```

The report that follows cites the document it relied on, and because the demo
data is labeled, the last line confirms the conclusion against the injected
ground truth. No key handy? `--provider mock` runs the whole loop offline on a
deterministic mock brain, no key, no download, no database.

Live Groq runs are unscripted: on a real HAI boiler anomaly (with
`--retriever vector`), it correctly reads a coordinated drop in a
pressure-control-valve's command and position as a valve/control fault that moved
the boiler pressure transmitter, rather than a generic guess (see the writeup for
how the first version got this wrong and what fixed it).

To persist incidents across runs with Mnemos (embedded, no server), add
`--memory mnemos`: investigate the same asset twice and the second run recalls
the first from memory.

## Evaluation

The differentiator is that root-cause quality is measured, not asserted. The
harness injects each known fault into nominal telemetry, runs the full pipeline,
and scores detection, cost, and the stated cause with two graders side by side: a
cheap keyword match and an LLM-as-judge (`--judge` picks its provider).

```bash
python -m eval.harness --provider groq --judge groq   # real agent, real judge
```

```
fault             detected  latency  rounds  keyword  judge          hypothesis
----------------------------------------------------------------------------------
bearing_overheat  yes       +33      3       hit      0.20 incorrect sustained deviation...
freq_excursion    yes       +29      3       hit      0.20 incorrect (mock agent, groq judge)
```

The contrast is the point: keyword matching marks every scenario "hit", but the
judge catches that a "sustained deviation in frequency" answer names the *signal*,
not the *mechanism* (a load-generation imbalance), and scores it low. The strong
live agent's answers, which do identify the mechanism, the judge scores as
correct. `latency` is samples after onset; `rounds` proxies token/latency cost.
The harness already paid for itself twice over: it caught a detector ranking bug
(the agent blaming a phase-angle sensor), and the judge caught keyword matching
over-crediting signal-only answers.

### On real data (HAI)

The same pipeline runs against the real HAI dataset, scored against its own
per-process attack labels:

```bash
# one-time download (test ~6 MB, train ~29 MB, official repo)
curl -L -o data/test1.csv.gz  https://raw.githubusercontent.com/icsdataset/hai/master/hai-21.03/test1.csv.gz
curl -L -o data/train1.csv.gz https://raw.githubusercontent.com/icsdataset/hai/master/hai-21.03/train1.csv.gz
python -m eval.hai_eval --train data/train1.csv.gz    # compare detectors
```

The eval reports point-adjusted precision/recall/F1 (the SWaT/WADI/HAI standard),
fitting "normal" on the attack-free train file and detecting on test (5 labeled
attacks):

| detector         | precision | recall | F1 (point-adjusted) |
|------------------|-----------|--------|---------------------|
| z-score baseline | 39%       | 100%   | 0.57                |
| autoencoder      | 90%       | 100%   | 0.95                |

The first pass (univariate z-score, normal fit on a short test prefix, event
scoring) caught every attack but only ~13% of its alarms were real: a single
fixed-baseline detector over-alarms on multi-modal ICS data. Two fixes close that
gap: a small per-asset **autoencoder** (`grid_copilot/detect/autoencoder.py`,
`pip install -e ".[detect]"`) that learns cross-signal correlations, and the
correct train-on-normal protocol. It reaches 90% precision at full recall,
catching all five attacks (~23-sample latency). The eval also prints a stricter
per-timestep F1 so the point-adjusted headline is not oversold.

Add `--judge groq` to also grade the live agent's real-data root cause against a
reference derived from the labels (affected process plus the signals that
deviated). That grading drove a sequence of real fixes, each raising the score:
widening the investigation window past the detection snapshot (0.2), reporting
signal onset order so the agent reasons about causal direction rather than
guessing it (0.3), and always including the detector's trigger signal (0.6). The
last step also exposed an honest limit: onset-based causal direction is sensitive
to a small, noisy trigger signal, so the true direction is not always settleable
from telemetry alone. Full analysis and all the agent failure modes:
[docs/eval-writeup.md](docs/eval-writeup.md).

## Architecture (where things live)

- `grid_copilot/ingest/` — `synthetic.py` (labeled fault injector, the eval's
  ground truth) and `replay.py` (stream readings; the seam where a real feed
  slots in).
- `grid_copilot/detect/` — `base.py` (the `Detector` interface), `statistical.py`
  (the fixed-baseline z-score detector), and `autoencoder.py` (a small per-asset
  torch autoencoder for correlated multivariate faults, behind the same interface).
- `grid_copilot/rag/` — `corpus.py` (domain notes, written from scratch, incl.
  HAI process layout and ISA tag conventions), `retriever.py` (keyword retrieval),
  `vector.py` (embedding retrieval via sentence-transformers, with a similarity
  floor), and `loader.py` (chunk real spec/manual text files into the corpus).
  Both retrievers implement one `Retriever` interface; pick with
  `--retriever keyword|vector`, and add real documentation with
  `--docs <path>` (see [data/specs/README.md](data/specs/README.md), e.g. the
  Modbus spec).
- `grid_copilot/memory/` — `store.py` (the `IncidentStore` interface, the local
  store, and the Mnemos adapter).
- `grid_copilot/agent/` — `tools.py` (investigation tools + registry),
  `investigator.py` (the loop and its two agents), `mock_llm.py` (the offline
  brain).
- `grid_copilot/events.py`, `grid_copilot/cli.py`, `grid_copilot/types.py`.
- `eval/harness.py` — the evaluation harness.
- `tests/` — end-to-end tests, all offline.

## What is real, and what is next

Honest status, because the difference matters:

- **Working now:** the full pipeline on synthetic and real HAI data (stream to
  cited report), the fixed-baseline detector, the three investigation tools, the
  event stream, and both eval harnesses, all covered by tests.
- **Working, verified end to end:** the live LLM providers (a real Groq run is
  shown above) and the Mnemos-backed incident store (embedded qdrant + local
  embeddings), with cross-run recall demonstrated via `--memory mnemos` and an
  integration test.
- **Next increments:** replace the keyword scorer in the eval with an
  LLM-as-judge that scores the stated root cause; retrieve over full public
  protocol specs (Modbus is openly published) beyond the hand-written notes; tune
  the autoencoder's per-timestep recall (the stricter metric is where it still has
  room).

## Data

The synthetic generator models a small power domain (a steam turbine, a pump, a
grid bus) so the project runs with no download. The real dataset, wired via
`grid_copilot/ingest/hai.py`, is **HAI** (HIL-based Augmented ICS Security
Dataset, [github.com/icsdataset/hai](https://github.com/icsdataset/hai)), a
steam-turbine and pumped-storage testbed with labeled attack intervals; the
loader treats each of its four processes as an asset. Other options include SWaT
and WADI (access request required) and the Oak Ridge / Mississippi State
power-system datasets.

## License

[PolyForm Noncommercial License 1.0.0](LICENSE). The source is open to read, use,
modify, and share for any **noncommercial** purpose (personal, research,
education, evaluation). Commercial use is not granted by this license; contact the
author for a commercial arrangement.
