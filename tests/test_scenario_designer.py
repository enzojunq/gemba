"""Scenario designer (META_HARNESS plan B) with a scripted fake LLM; one live test (GEMBA_LIVE=1)."""
from __future__ import annotations

import os

import pytest
import yaml

import contracts as c
from llm.client import CallMeta
from org.scenario_designer import EventDraft, ScenarioDraft, clean_draft, describe, design_scenario
from scenarios.loader import validate_scenario

TEXT = "seasonal surge x2 from day 65 for 3 days, rack damage aisle 7 on day 66, 20% fewer PM pickers day 63"
LIVE_TEXT = ("a peak week: demand surge x2 on the seasonal family from day 69 for 5 days, with a forklift "
             "breakdown on day 72 and 20% fewer PM pickers on day 70")


class FakeLLM:
    """Returns scripted answers in order; an Exception in the script is raised."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[tuple[str, list[dict], str]] = []

    def complete(self, tier, messages, schema=None, *, role, run_id, reasoning=False, max_tokens=800,
                 temperature=0.0, fallback=None):
        self.calls.append((tier, messages, role))
        ans = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        if isinstance(ans, Exception):
            raise ans
        return ans, CallMeta(model="fake", prompt_hash="x")


def draft(events, days=(61, 70), slug="peak_week", **kw) -> ScenarioDraft:
    return ScenarioDraft(slug=slug, summary="A test week.", days=days, confidence=0.9,
                         events=[EventDraft(**e) for e in events], **kw)


CLEAN = [
    {"kind": "demand_surge", "day": 65, "args": {"family": "seasonal", "factor": 2, "days": 3}},
    {"kind": "rack_damage", "day": 66, "args": {"aisle": 7}},
    {"kind": "labor_shortage", "day": 63, "hour": 14, "args": {"pool": "pm", "pct": 20, "days": 1, "why": "x"}},
]


def test_clean_draft_validates_and_yields_yaml():
    llm = FakeLLM(draft(CLEAN))
    r = describe(TEXT, llm=llm, existing=set())
    assert r["ok"] and r["errors"] == [] and len(llm.calls) == 1
    assert llm.calls[0][0] == "super" and llm.calls[0][2] == "scenario_designer"
    raw = yaml.safe_load(r["yaml"])
    assert raw == r["scenario"] and validate_scenario(raw) == []
    c.Scenario.model_validate(raw)
    assert raw["id"] == "peak_week" and 1000 <= raw["seed"] < 10000 and raw["days"] == [61, 70]
    assert [(e["kind"], e["day"]) for e in raw["events"]] == [("demand_surge", 65), ("rack_damage", 66),
                                                              ("labor_shortage", 63)]
    assert raw["events"][1]["args"] == {"aisle": 7, "days": 1}  # days defaulted
    assert raw["events"][2]["hour"] == 14 and "why" not in raw["events"][2]["args"]
    assert any("why" in d for d in r["dropped"])
    # deterministic: same text -> same seed and id
    assert describe(TEXT, llm=FakeLLM(draft(CLEAN)), existing=set())["scenario"] == raw


def test_bad_customer_event_dropped_with_reason_and_still_validates():
    events = CLEAN[:2] + [{"kind": "key_customer_week", "day": 67, "args": {"customer": "ACME", "days": 3}}]
    llm = FakeLLM(draft(events))
    raw, errors, d = design_scenario(llm, TEXT, existing=set())
    assert errors == [] and len(llm.calls) == 1 and isinstance(d, ScenarioDraft)
    assert [e["kind"] for e in raw["events"]] == ["demand_surge", "rack_damage"]
    r = describe(TEXT, llm=FakeLLM(draft(events)), existing=set())
    assert r["ok"] and any("key_customer_week" in x and "dropped" in x for x in r["dropped"])


def test_day_outside_window_triggers_one_repair_round():
    bad = [{"kind": "rack_damage", "day": 75, "args": {"aisle": 7, "days": 2}}]
    good = [{"kind": "rack_damage", "day": 68, "args": {"aisle": 7, "days": 2}}]
    llm = FakeLLM(draft(bad), draft(good))
    r = describe(TEXT, llm=llm, existing=set())
    assert len(llm.calls) == 2 and r["ok"] and r["scenario"]["events"][0]["day"] == 68
    feedback = llm.calls[1][1][-1]["content"]
    assert "day 75 outside the window [61, 70]" in feedback
    # still bad after the repair: no third call, errors returned
    llm2 = FakeLLM(draft(bad), draft(bad))
    r2 = describe(TEXT, llm=llm2, existing=set())
    assert len(llm2.calls) == 2 and not r2["ok"] and "day 75" in r2["errors"][0]


def test_cleaning_rules_without_llm():
    raw, errors, _ = clean_draft(draft([{"kind": "aisle_closure", "day": 64, "args": {"aisle": 3}}]), TEXT,
                                 existing=set())
    assert errors and "consequence" in errors[0]
    _, errors, _ = clean_draft(draft(CLEAN, days=(55, 70)), TEXT, existing=set())
    assert errors and "61 <= start <= end <= 90" in errors[0]
    raw, errors, _ = clean_draft(draft(CLEAN, slug="train_1"), TEXT, existing=set())
    assert errors == [] and raw["id"].startswith("train_1-") and raw["id"] != "train_1"
    low = draft(CLEAN).model_copy(update={"confidence": 0.2})
    assert "not a scenario request" in clean_draft(low, TEXT, existing=set())[1][0]
    bad_ovr = draft(CLEAN, overrides={"headcounts": {"night": {"pm": -1}}})
    assert any("headcount" in e for e in clean_draft(bad_ovr, TEXT, existing=set())[1])


def test_llm_failure_returns_not_ok():
    r = describe(TEXT, llm=FakeLLM(RuntimeError("crusoe down")))
    assert not r["ok"] and "crusoe down" in r["errors"][0] and r["scenario"] == {}
    raw, errors, d = design_scenario(FakeLLM(RuntimeError("crusoe down")), TEXT)
    assert raw == {} and errors and d is None


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("GEMBA_LIVE") != "1", reason="set GEMBA_LIVE=1 to call Crusoe")
def test_live_peak_week(tmp_path):
    from llm.cache import ResponseCache
    from llm.client import LLM

    r = describe(LIVE_TEXT, llm=LLM(cache=ResponseCache(tmp_path / "cache.jsonl")))
    print(f"\nLIVE ok={r['ok']} calls={r['calls']} confidence={r['confidence']}\nsummary: {r['summary']}"
          f"\nerrors: {r['errors']}\ndropped: {r['dropped']}\n{r['yaml']}")
    assert r["ok"], r["errors"]
    kinds = {e["kind"] for e in r["scenario"]["events"]}
    assert {"demand_surge", "forklift_breakdown", "labor_shortage"} <= kinds
    assert validate_scenario(yaml.safe_load(r["yaml"])) == []
