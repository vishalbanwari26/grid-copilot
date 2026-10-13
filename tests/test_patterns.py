"""Overrides stay with their asset; repeats across assets become a reviewed
fleet-level request, never an automatic change."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace

import pytest

from grid_copilot.transformer import fleet as F
from grid_copilot.transformer.decisions import DecisionLog, Proposal, ReviewedHistory
from grid_copilot.transformer.patterns import PatternReviewLog, find_patterns
from grid_copilot.transformer.rules import TransformerMockClient
from grid_copilot.transformer.run import investigate_alarm


@pytest.fixture(scope="module")
def base_proposal() -> Proposal:
    sc = F.single("thermal_t3", seed=1, prefer_real=False)
    asset = next(iter(sc.fleet.assets))
    report = investigate_alarm(sc, asset, TransformerMockClient())
    return Proposal.from_report(report, "DGA_C2H4_HIGH", "mock")


def _for(p: Proposal, asset: str) -> Proposal:
    return replace(p, proposal_id=str(uuid.uuid4()), asset=asset)


def _override(log: DecisionLog, p: Proposal, asset: str, final: str = "sensor_fault") -> None:
    log.record(_for(p, asset), "A. Engineer", "override", reason=f"lab sample normal on {asset}",
               final_fault_class=final)


def test_an_override_only_changes_its_own_asset(tmp_path, base_proposal):
    log = DecisionLog(tmp_path / "d.jsonl")
    history = ReviewedHistory(log=log)
    _override(log, base_proposal, "T1")
    assert any("sensor_fault" in r.summary for r in history.recall("T1", "x"))
    assert history.recall("T2", "x") == []


def test_a_pattern_needs_the_same_override_on_several_assets(tmp_path, base_proposal):
    log = DecisionLog(tmp_path / "d.jsonl")
    _override(log, base_proposal, "T1")
    _override(log, base_proposal, "T1")  # same asset twice counts once
    _override(log, base_proposal, "T2")
    log.record(_for(base_proposal, "T3"), "A. Engineer", "accept")  # accepts never count
    _override(log, base_proposal, "T4", final="no_fault")  # a different correction is a different pattern
    assert find_patterns(log.records()) == []

    _override(log, base_proposal, "T5")
    found = find_patterns(log.records())
    assert len(found) == 1
    p = found[0]
    assert p.pattern_id == f"{base_proposal.fault_class}->sensor_fault"
    assert p.assets == ["T1", "T2", "T5"]
    assert p.tools_cited and "data_quality" in p.tools_cited
    assert base_proposal.prompt_version in p.prompt_versions
    assert "re-evaluated" in p.change_request()


def test_a_pattern_changes_nothing_until_a_named_reviewer_decides(tmp_path, base_proposal):
    log = DecisionLog(tmp_path / "d.jsonl")
    for a in ("T1", "T2", "T3"):
        _override(log, base_proposal, a)
    history = ReviewedHistory(log=log)
    reviews = PatternReviewLog(tmp_path / "r.jsonl")
    p = find_patterns(log.records())[0]

    assert reviews.status(p)["status"] == "open"
    assert history.recall("T9", "x") == []  # an asset without decisions is still judged on its own

    with pytest.raises(ValueError):
        reviews.record(p, "", "approve_change", "repeats")
    with pytest.raises(ValueError):
        reviews.record(p, "B. Lead", "apply_everywhere", "repeats")
    with pytest.raises(ValueError):
        reviews.record(p, "B. Lead", "approve_change", " ")

    reviews.record(p, "B. Lead", "approve_change", "three lab samples disagree with the online cell")
    st = reviews.status(p)
    assert st["status"] == "approve_change" and st["new_since_review"] == 0
    assert history.recall("T9", "x") == []  # approval records a request, it rewrites nothing

    _override(log, base_proposal, "T4")
    st = reviews.status(find_patterns(log.records())[0])
    assert st["assets_at_review"] == 3 and st["new_since_review"] == 1


def test_pattern_reviews_are_tamper_evident(tmp_path, base_proposal):
    log = DecisionLog(tmp_path / "d.jsonl")
    for a in ("T1", "T2", "T3"):
        _override(log, base_proposal, a)
    reviews = PatternReviewLog(tmp_path / "r.jsonl")
    p = find_patterns(log.records())[0]
    reviews.record(p, "B. Lead", "keep_watching", "wait for a fourth case")
    reviews.record(p, "C. Lead", "approve_change", "fourth case confirmed it")
    assert reviews.verify_chain() == (True, "chain intact")
    lines = reviews.path.read_text().splitlines()
    first = json.loads(lines[0])
    first["reviewer"] = "Someone Else"
    lines[0] = json.dumps(first, sort_keys=True)
    reviews.path.write_text("\n".join(lines) + "\n")
    ok, why = reviews.verify_chain()
    assert not ok and "changed" in why
