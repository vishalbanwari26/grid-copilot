"""Transformer fleet domain: asset performance management on top of the RCA loop.

A fleet of oil-immersed power transformers, each with an on-load tap changer
(OLTC), online dissolved gas analysis (DGA), partial discharge (PD) sensing and
top-oil temperature, is simulated hour by hour on real load profiles. Faults are
injected with a known answer, the monitoring layer raises alarms and events, and
the investigator explains an alarm with deterministic diagnostic tools (Duval
triangle, IEC ratio method, the thermal model, OLTC and PD checks).

Modules:

- `thermal`     hot-spot temperature and insulation ageing (the loading-guide
                difference equations), plus cooling stages.
- `sim`         the fleet simulator and the fault catalogue (the ground truth).
- `ett`         real load profiles from the public ETT dataset, with a synthetic
                fallback so tests and the offline demo need no download.
- `monitor`     alarm rules and the SCADA-style event log.
- `diagnostics` pure functions: Duval triangle 1, IEC ratios, gas rates, PD
                pattern classification, OLTC signature checks.
- `health`      health index per subsystem and the fleet risk ranking.
- `tools`       the investigation tools the agent calls.
- `domain`      prompts, the conclusion schema and its validator.
"""
