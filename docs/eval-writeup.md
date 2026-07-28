# Benchmarking agentic root-cause analysis on ICS telemetry: what breaks

Most write-ups about LLM agents for industrial monitoring show one clean run and
stop. This one reports what an eval harness found when the same pipeline was run
against labeled faults, including the parts that failed. The system is Grid
Copilot: an anomaly detector feeds an agent that investigates with three tools
(query the telemetry window, retrieve documentation, recall prior incidents on
the asset) and writes a cited root-cause report. Everything below runs on public
and synthetic data.

## What was measured

Two settings, one pipeline:

1. **Synthetic, fully labeled.** A generator injects three faults (a bearing
   overheating, a pump cavitation, a grid frequency excursion) into otherwise
   nominal multivariate telemetry. Because the injected cause is known, both
   detection and the agent's stated root cause can be scored.
2. **Real, HAI dataset.** The HIL-based Augmented ICS Security dataset (a
   steam-turbine and pumped-storage testbed) ships per-process attack labels. A
   6000-sample slice with one labeled attack interval was scored against those
   labels, and the agent was run on the first anomaly inside the attack.

Detector: a fixed-baseline z-score. It learns each signal's normal range over an
attack-free warmup, freezes it, and scores later samples against that reference,
with a persistence guard so a single noise spike is ignored. Agent brain: an
open-weight model (GPT-OSS-120B via Groq) behind a provider-agnostic interface.

## Results

Synthetic (deterministic):

| fault            | detected | latency | RCA (live)                                   |
|------------------|----------|---------|----------------------------------------------|
| bearing overheat | yes      | +27     | "thermal degradation of the bearing" (93%)   |
| pump cavitation  | yes      | +30     | "pump cavitation from loss of pressure"      |
| freq excursion   | yes      | +25     | "sustained deviation in frequency"           |

Detection recall 3/3. Latency is samples after fault onset.

**Grading the root cause: keyword matching lies.** A cheap keyword grader marks
all three "correct" because the stated cause contains the fault's key word. But
for the frequency case the agent said "sustained deviation in frequency", which
names the affected signal and not the mechanism (a load-generation imbalance). An
LLM-as-judge that reads the stated cause against the true cause catches this: it
scores that answer 0.2 to 0.3 (partial or incorrect) while keyword matching gives
it a full pass. The judge runs on the same provider-agnostic interface and grades
the strong open-weight agent's answers as correct while flagging the weaker
scripted agent's signal-only answers, so it discriminates rather than
rubber-stamps. One caveat worth stating: a model grading its own outputs trends
lenient (self-judging gave a clean sweep), so the honest configuration judges one
model with a different one; the harness lets the judge provider be chosen
separately for exactly this reason.

Real HAI, first pass (univariate z-score, "normal" fit on a short prefix of the
test file, event-level scoring): every attack was caught, but only about **13% of
the fired alarms fell inside an attack**. That number is the starting point, not
the conclusion; the two fixes below take the point-adjusted F1 from 0.57 to 0.95.
A live agent run on a real in-attack anomaly (a sharp drop on a boiler pressure
control valve) produced a grounded hypothesis at 70% confidence, tying the
pressure drop to a cavitation-like signature and citing the telemetry.

## The failure modes (the useful part)

**1. A single fixed-baseline detector has high recall but poor precision on real
ICS data.** In the first pass, a univariate fixed-baseline z-score caught the
attack but fired far more alarms outside it than inside. The cause is that
"normal" on a real plant is multi-modal: legitimate setpoint changes and mode
transitions produce sustained deviations that a single frozen baseline cannot
distinguish from an attack. This is not a tuning problem, it is the ceiling of a
univariate baseline. It motivated two fixes below.

### Fixing it: correlations, and a proper train set

Two changes moved the numbers a lot, measured with point-adjusted
precision/recall/F1 (the SWaT/WADI/HAI standard) on five labeled attack intervals
in HAI test1:

**(a) A multivariate autoencoder instead of a univariate z-score.** Trained per
process on normal data, it learns the joint structure of the signals. A coherent
change that preserves the usual correlations reconstructs well; an attack that
pushes one signal out of step reconstructs badly. **(b) The correct protocol:
fit on HAI's dedicated attack-free train file, not on a short prefix of the test
file**, so legitimate operating modes are represented in "normal."

| detector          | precision | recall | F1 (point-adjusted) |
|-------------------|-----------|--------|---------------------|
| z-score baseline  | 39%       | 100%   | 0.57                |
| autoencoder       | 90%       | 100%   | 0.95                |

The autoencoder reaches 90% precision at full interval recall, catching all five
attacks with about a 23-sample detection latency.

**A caveat, stated up front, because it matters.** Point-adjusted F1 is generous:
it credits a whole attack segment when any single point in it is detected. Under
a stricter un-adjusted, per-timestep metric, both detectors score much lower
(F1 around 0.24 to 0.26) and the autoencoder is not clearly ahead. What the
autoencoder actually buys is *event-level* quality: it detects each attack early
and with very few false alarms, but it does not label every individual attack
timestep. For a system that fires one agent investigation per attack event, that
event-level precision is the operationally relevant property, which is why the
point-adjusted view is the right lens here, but the strict number is reported
too so the headline is not oversold.

**2. A moving-average detector silently hides slow faults.** An earlier version
used a rolling window. It adapted its mean to a slow ramp (a bearing heating over
minutes) and never tripped, while random noise did. The fixed baseline was chosen
specifically to accumulate score on sustained drifts instead of adapting them
away. The eval caught this; a single demo would not have.

**3. A generic knowledge base makes the agent loop instead of concluding.** On
real HAI signals with no asset-specific documentation, keyword retrieval returned
a weak, loosely-related match. The agent, unsure, kept re-gathering evidence and
burned its entire round budget without deciding. Two fixes: force a conclusion
once tools are exhausted or the budget is nearly spent, and give the critic teeth
by feeding a rejected hypothesis back for a bounded revision (the domain analogue
of replanning). Retrieval was also moved from keyword matching to embeddings with
a similarity floor, so an off-topic query returns nothing instead of a false
top-k match.

**4. Grounded is not the same as correct, and retrieving a doc is not the same as
using it.** This one is worth dwelling on. On a real boiler anomaly the agent
first proposed a *cavitation* cause and misread the evidence: it called
``P1_PCV01Z`` and ``P1_PCV01D`` "pressure signals" when they are the command and
position of a pressure-control *valve*. Adding an embedding retriever and writing
domain notes (the HAI process layout, ISA tag conventions) was not enough: the
model retrieved the tag-convention note and still ignored it. What fixed it was
decoding the tags directly in the *primary evidence*, so ``query_telemetry`` now
reports "``P1_PCV01Z`` (boiler pressure-control-valve position) fell -10.0"
instead of a bare tag. With the meaning in front of it rather than in an optional
document, the agent produced the correct reading: a coordinated drop in the valve
command and its position altered boiler pressure and moved the pressure
transmitter, a control or valve fault rather than cavitation. The lesson: for a
tool-using agent, putting the decoded fact in the evidence it must read beats
hoping it retrieves and applies a reference.

**5. Judging real-data root cause is itself a source of findings.** HAI ships no
per-interval textual cause, so to score the real-data hypothesis automatically I
derive a reference from what the labels do give: the affected process, and the
signals that measurably deviated over the attack interval (interval mean versus
the preceding normal, in baseline stds). An LLM judge then grades the hypothesis
against that reference. Building this surfaced three things, in order. First, the
reference wording leaked into the grade: an early version called it "a labeled
attack" and the judge marked a correct physical diagnosis wrong for "missing the
attack", even though a telemetry-only agent cannot infer malicious intent;
reframing the reference as a physical anomaly fixed that. Second, with fair
framing the judge still scored the run low, and its stated reason was correct: the
agent identified the right process (boiler) and the right mechanism class (a
control-valve fault) but the wrong specific valve, blaming the pressure-control
valve when the level-control valve dominated the interval. Third, and this is the
real system finding, the mismatch was not the agent being wrong so much as the
agent reasoning over the *detection-window snapshot*, where the pressure valve was
moving, while the reference spans the *whole interval*, where the level valve
dominated.

That third finding was actionable, so it was fixed. The investigation now queries
a telemetry log for a window that reaches back before onset (a clean baseline) and
forward past detection (the developed incident), instead of the detector's
snapshot. It worked at the level it was meant to: the agent's evidence now centers
on the same signals as the reference, the level-control valve command and position
and the boiler flow, not the early pressure valve. But the judge score only moved
from 0.0 to 0.2, because the disagreement relocated to a deeper layer. Seeing the
valve command and position rise together while flow fell, the agent inferred that
the valve was opening in *response* to a downstream pump or feed-water shortage,
and named that as the cause, rather than attributing the anomaly to the
manipulated valve itself. That is a coherent physical hypothesis; "valve opened
and flow dropped" genuinely is ambiguous between a supply fault and a spoofed
valve command, and a telemetry-only agent cannot settle it without the
control-loop structure. So the honest state after the windowing fix was: the fix
landed and is verifiable in the evidence, and the remaining gap is causal
attribution, a harder and separate problem, not something to close by loosening
the judge.

Causal attribution was the next fix, and it is instructive in its own right. The
missing evidence was not more signals but their *order*: if a controller command
deviates before the measured process variables, the command is the driver (a
setpoint change or a spoofed command); if a measurement moves first, the controls
are responding to an upstream disturbance. So `query_telemetry` now classifies
each tag as command, position or measurement and reports the onset order, and the
detector's own trigger signal is always included so it cannot be crowded out by
larger movers. With the onset order in front of it, the agent stopped guessing and
started reasoning about direction explicitly, and its hypothesis moved with the
evidence: across the snapshot, window, and causal fixes the judge score climbed
0.0, 0.2, 0.3, 0.6.

The last step is the honest one, and it did not go where I expected. Including the
trigger signal revealed that the pressure measurement crossed its band *before*
the valve command, so the onset rule reported "measurement first, controls
responding to a disturbance", and the agent followed it to a disturbance-driven
cause, the opposite direction from the run just before. My first read was that
this was an artifact: the pressure moved only a fraction of a unit on a tightly
controlled tag, so a bare standard-deviation onset could trip early on noise. So
the onset test was made robust: it now marks onset only when a signal reaches a
real fraction of its *eventual* deviation, a magnitude-fair criterion that a small
noise wobble cannot satisfy and that, if anything, favors a step-like command
onsetting before a drifting measurement. Under that stricter rule the pressure
still led, and the gap widened rather than closed. So the artifact hypothesis was
wrong: the measurement-first ordering is a real feature of this data, the pressure
genuinely starts moving before the command. Which makes the agent's
disturbance-driven conclusion, the best-scoring of the sequence, evidence-based
rather than a fluke, while the deeper question of what the attack actually
manipulated remains unsettleable from telemetry alone. The meta-lesson runs
through all of it: an LLM judge is strict and framing-sensitive, so report its
verdict with its justification, judge with a different model than the one under
test, read a low score for what caused it rather than tuning until it rises, and
when you suspect a heuristic is fragile, build the stronger version and let it tell
you, rather than assuming, which is what turned a guess about noise into a finding.

## Why measure rounds and latency

Each investigation reports its number of tool-calling rounds, a direct proxy for
the tokens and wall-clock a hosted model bills, and detection latency in samples
after onset. An agentic RCA system is only deployable if a maintenance lead can
see both its accuracy and its per-incident cost. Reporting them together, per
fault, is the difference between a demo and a system.

## Takeaway

Both the failures and the fix were only visible because the faults were labeled
and scored. The eval said the univariate baseline was noisy (point-adjusted F1
0.57); moving to a multivariate autoencoder trained with the correct protocol
took that to 0.95 at full recall, and the same eval kept the claim honest by also
reporting the stricter per-timestep number where the gap narrows. For anyone
building agentic diagnostics on OT telemetry: invest in the eval harness first,
report the strict metric next to the generous one, model correlations rather than
single signals, train "normal" on data that actually represents it, and ground
the agent in real equipment documentation before trusting a confident narrative.

Code and reproduction: `eval/harness.py` (synthetic) and `eval/hai_eval.py`
(HAI, with `--train data/train1.csv.gz` for the train/test protocol) produce
every number above.
