"""OrgPolicy (spec 10.5) on StubTwin with a scripted fake LLM client.

`FakeLLM` is a test double for `llm.client.LLM` (same `.complete` signature): it returns
canned pydantic objects keyed by (schema name, role), or the caller's `fallback` when nothing
is scripted. It is not a product mock: the product always runs on Crusoe.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

import contracts as c
from engine.store import RunStore
from engine.stub_twin import StubTwin
from llm.client import CallMeta
from org.scoring import load_profile
from org.standup import Accept, MaybeProposal, Note, OrgPolicy, QualityReply, SafetyReply, Thresholds

RUN = "r1"
DAY = 66
MTG = f"{RUN}-m{DAY}"


def opt(n: int) -> str:
    return f"{MTG}-o{n}"


def ev(n: int) -> str:
    return f"ev-{RUN}-{DAY}-{n}"


REF = ev(0)


class FakeLLM:
    def __init__(self, script: dict | None = None):
        self.script = script or {}
        self.calls: list[tuple[str, str, str, bool]] = []
        self._lock = threading.Lock()

    def complete(self, tier, messages, schema=None, *, role, run_id, reasoning=False, max_tokens=800,
                 temperature=0.0, fallback=None):
        name = schema.__name__ if schema else "text"
        with self._lock:
            n = sum(1 for s, r, _, _ in self.calls if s == name and r == role)
            self.calls.append((name, role, tier, reasoning))
        ans = self.script.get((name, role))
        if callable(ans):
            ans = ans(messages, n)
        if isinstance(ans, list):
            ans = ans[min(n, len(ans) - 1)]
        if ans is None:
            ans = fallback
        return ans, CallMeta(model="fake", prompt_hash="x", usd=0.001, latency_ms=1)

    def count(self, schema: str, role: str | None = None) -> int:
        return sum(1 for s, r, _, _ in self.calls if s == schema and (role is None or r == role))


def pos(option: str, evid: list[str], stance: str = "support", amendment=None) -> c.Position:
    return c.Position(option_id=option, stance=stance, amendment=amendment, rationale="because", evidence_ids=evid)


def proposal(label: str, *levers) -> c.Proposal:
    return c.Proposal(option_label=label, levers=list(levers), rationale=f"{label} rationale")


def rack(day: int = DAY) -> c.Event:
    return c.Event(event_id="rk1", kind="rack_damage", day=day, args={"aisle": 5, "days": 3},
                   announced_day=day, source="scripted")


def base_script(over: dict | None = None) -> dict:
    s = {
        ("Proposal", "reslotting_engineer"): proposal("reslot 150 by value", c.ReslotTonight(n_moves=150)),
        ("MaybeProposal", "ops_manager"): MaybeProposal(proposal=proposal("reslot 100 by zone",
                                                                          c.ReslotTonight(n_moves=100, order="zone"))),
        ("MaybeProposal", "safety"): MaybeProposal(proposal=None),
        ("Position", "ops_manager"): pos(opt(2), [ev(2)]),
        ("Position", "reslotting_engineer"): pos(opt(1), [ev(1)]),
        ("SafetyReply", "safety"): SafetyReply(position=pos(opt(1), [ev(1), REF])),
        ("ChairDecision", "program_manager"): c.ChairDecision(option_id=opt(1), escalate=False,
                                                              rationale="progress with low risk", evidence_ids=[ev(1)]),
    }
    s.update(over or {})
    return s


@pytest.fixture
def store(tmp_path):
    s = RunStore(f"sqlite:///{tmp_path / 'runs.db'}")
    s.create_schema()
    return s


def setup(twin=None, day=DAY):
    twin = twin or StubTwin()
    state = twin.initial_state(c.Scenario(id="t", seed=1, days=(day, day + 5)))
    return twin, state, twin.view(state)


def run_day(store, script, events, twin=None, day=DAY):
    llm = FakeLLM(script)
    policy = OrgPolicy(RUN, load_profile("customer_first"), llm, store)
    twin, state, view = setup(twin, day)
    d = policy.decide(day, view, events, twin, state)
    return policy, llm, d


def rows(store, table):
    return store.table(table, RUN)


# --------------------------------------------------------------------------- quiet day


def test_quiet_day_one_call_standing_plan(store):
    policy, llm, d = run_day(store, {("Note", "program_manager"): Note(text="All calm, 200 moves tonight.")},
                             [], day=62)
    assert len(llm.calls) == 1 and llm.calls[0][0] == "Note" and llm.calls[0][2] == "nano"
    assert [lv.kind for lv in d.levers] == ["reslot_tonight"] and d.levers[0].n_moves == 200
    assert all(lv.decision_id == f"{RUN}-d62" for lv in d.levers)
    assert d.decided_by == "program_manager" and d.rationale == "All calm, 200 moves tonight."
    mt = rows(store, "meetings")
    assert len(mt) == 1 and mt[0]["kind"] == "quiet"
    assert len(rows(store, "utterances")) == 1
    assert isinstance(getattr(d, "predicted", None), c.Kpis)
    assert policy.llm_calls == 1


# --------------------------------------------------------------------------- rack damage day


def test_rack_damage_meeting(store):
    policy, llm, d = run_day(store, base_script(), [rack()])
    hires = rows(store, "hires")
    assert any(h["role"] == "safety" and h["trigger"] == "rack_damage" for h in hires)
    options = {o["option_id"]: o for o in rows(store, "options")}
    assert len(options) >= 2
    evid = {e["evidence_id"] for e in rows(store, "evidence")}
    assert REF in evid and all(o["evidence_id"] in evid for o in options.values())
    utt = rows(store, "utterances")
    positions = [u for u in utt if u["kind"] == "position"]
    assert {u["role"] for u in positions} == {"ops_manager", "reslotting_engineer", "safety"}
    assert all(u["evidence_ids"] and set(u["evidence_ids"]) <= evid for u in positions)
    assert d.option_id in options and options[d.option_id]["feasible"]
    assert d.meeting_id == MTG and d.decision_id == f"{RUN}-d{DAY}"
    assert all(lv.decision_id == d.decision_id for lv in d.levers)
    assert any(u["kind"] == "decision" for u in utt)
    mt = rows(store, "meetings")[0]
    assert mt["kind"] == "standup" and "safety" in mt["attendees"] and mt["outcome"]
    assert getattr(d, "predicted", None) is not None and isinstance(d.predicted, c.Kpis)
    assert policy.last_spec_hashes and len(policy.last_spec_hashes) == len(evid)
    # tiers: positions on Super/Nano per role, chair decision with reasoning on
    assert ("ChairDecision", "program_manager") in [(s, r) for s, r, _, reasoning in llm.calls if reasoning]
    # cascade: the meeting is caused by the rack event
    edges = rows(store, "cascade_edges")
    assert any(e["from_id"] == "evt:rk1" and e["to_id"] == f"mtg:{MTG}" for e in edges)


def test_unauthorized_lever_rejected(store):
    script = base_script({("Proposal", "reslotting_engineer"): proposal(
        "reslot with overtime", c.ReslotTonight(n_moves=150), c.Overtime(shift="day", pool="pm", hours=8))})
    _, _, d = run_day(store, script, [rack()])
    q = [u for u in rows(store, "utterances") if u["kind"] == "question"]
    assert any(u["role"] == "reslotting_engineer" and u["text"].startswith("rejected lever overtime") for u in q)
    o1 = next(o for o in rows(store, "options") if o["option_id"] == opt(1))
    assert [lv["kind"] for lv in o1["levers"]] == ["reslot_tonight"]
    assert all(lv.kind != "overtime" for lv in d.levers)


def test_bogus_evidence_reasked_then_fallback(store):
    script = base_script({("Position", "ops_manager"): pos(opt(2), ["ev-bogus"])})
    _, llm, _ = run_day(store, script, [rack()])
    assert llm.count("Position", "ops_manager") == 2
    ops = [u for u in rows(store, "utterances") if u["role"] == "ops_manager" and u["kind"] == "position"]
    assert len(ops) == 1 and "(fallback)" in ops[0]["text"]
    assert ops[0]["option_id"] == opt(1) and ops[0]["evidence_ids"] == [ev(1)]
    assert any(r["kind"] == "llm_fallback" and r["role"] == "ops_manager" for r in rows(store, "llm_calls"))


class RiskyTwin(StubTwin):
    """Big reslots push the what-if's safety risk over the hard limit."""

    def whatif(self, state, levers, days, known_events):
        w = super().whatif(state, levers, days, known_events)
        if any(lv.kind == "reslot_tonight" and lv.n_moves >= 250 for lv in levers):
            return w.model_copy(update={"safety_risk_max": 1.3})
        return w


def test_safety_veto_honoured(store):
    script = base_script({
        ("Proposal", "reslotting_engineer"): proposal("big push", c.ReslotTonight(n_moves=300)),
        ("SafetyReply", "safety"): SafetyReply(position=pos(opt(1), [ev(1)], "oppose"),
                                               veto=c.Veto(option_id=opt(1), rule="safety_risk_max > 1.0",
                                                           evidence_ids=[ev(1)])),
    })
    _, _, d = run_day(store, script, [rack()], twin=RiskyTwin())
    o1 = next(o for o in rows(store, "options") if o["option_id"] == opt(1))
    assert o1["vetoed_by"] == "safety" and o1["feasible"] is False
    assert d.option_id != opt(1)
    assert any(u["kind"] == "veto" and u["text"].startswith(f"veto {opt(1)}") for u in rows(store, "utterances"))


def test_safety_veto_without_breach_overruled(store):
    script = base_script({
        ("SafetyReply", "safety"): SafetyReply(position=pos(opt(2), [ev(2)], "oppose"),
                                               veto=c.Veto(option_id=opt(2), rule="I dislike it", evidence_ids=[ev(2)])),
    })
    run_day(store, script, [rack()])
    o2 = next(o for o in rows(store, "options") if o["option_id"] == opt(2))
    assert o2["vetoed_by"] is None
    assert any(u["kind"] == "veto" and "overruled: no evidence of breach" in u["text"] for u in rows(store, "utterances"))


class AllBadTwin(StubTwin):
    """Every what-if (REF included) breaches safety; bigger reslots breach more."""

    def whatif(self, state, levers, days, known_events):
        w = super().whatif(state, levers, days, known_events)
        n = sum(lv.n_moves for lv in levers if lv.kind == "reslot_tonight")
        return w.model_copy(update={"safety_risk_max": 1.2 + n / 10000})


def test_all_infeasible_least_bad_and_escalate(store):
    script = base_script({
        ("Proposal", "reslotting_engineer"): proposal("reslot 200", c.ReslotTonight(n_moves=200)),
        ("DirectorRuling", "plant_director"): c.DirectorRuling(option_id=None, set_profile=None, override_lock=None,
                                                               rationale="Take the least-bad option and keep watch."),
    })
    _, llm, d = run_day(store, script, [rack()], twin=AllBadTwin())
    options = {o["option_id"]: o for o in rows(store, "options")}
    assert not any(o["feasible"] for o in options.values())
    assert d.option_id == opt(2)  # reslot 100 breaches least
    assert d.decided_by == "plant_director"
    assert llm.count("DirectorRuling", "plant_director") == 1
    assert any(h["role"] == "plant_director" for h in rows(store, "hires"))
    utt = rows(store, "utterances")
    assert any(u["kind"] == "question" and "escalating" in u["text"] for u in utt)
    assert any(u["role"] == "plant_director" and u["kind"] == "decision" for u in utt)


def test_quality_lock_becomes_protect_lever(store):
    week = c.Event(event_id="kc1", kind="key_customer_week", day=DAY, args={"customer": "KEY1", "days": 5},
                   announced_day=DAY - 3, source="scripted")
    script = base_script({
        ("MaybeProposal", "quality"): MaybeProposal(proposal=None),
        ("QualityReply", "quality"): QualityReply(position=pos(opt(1), [ev(1)]),
                                                  lock=c.Lock(target="customer", ids=["KEY1"], days=3,
                                                              reason="key customer week")),
    })
    _, _, d = run_day(store, script, [week])
    assert any(h["role"] == "quality" for h in rows(store, "hires"))
    prot = [lv for lv in d.levers if lv.kind == "protect"]
    assert prot and prot[0].ids == ["KEY1"] and prot[0].days == 3 and prot[0].decision_id == d.decision_id
    assert any(u["kind"] == "lock" and u["role"] == "quality" for u in rows(store, "utterances"))
    assert all(any(lv["kind"] == "protect" for lv in o["levers"]) for o in rows(store, "options"))


def test_amendment_creates_option_with_evidence(store):
    script = base_script({("Position", "ops_manager"): pos(
        opt(1), [ev(1)], "amend", amendment=[c.ReslotTonight(n_moves=120, order="zone")])})
    run_day(store, script, [rack()])
    options = rows(store, "options")
    amended = [o for o in options if o["proposed_by"] == "ops_manager" and o["levers"][0]["n_moves"] == 120]
    assert len(options) <= 3 and amended and amended[0]["evidence_id"]


def test_pause_option_added_when_only_one(store):
    script = base_script({("MaybeProposal", "ops_manager"): MaybeProposal(proposal=None)})
    _, _, d = run_day(store, script, [rack()])
    options = rows(store, "options")
    assert len(options) == 2
    assert any(o["levers"] == [{"kind": "pause_reslot", "days": 1, "decision_id": ""}] for o in options)


def test_predicted_readable(store):
    policy, _, d = run_day(store, base_script(), [rack()])
    p = getattr(d, "predicted", None)
    assert isinstance(p, c.Kpis) and policy.last_whatif is not None
    assert p == policy.last_whatif.kpis_by_day[0]
    assert "predicted" not in d.model_dump()


def test_deterministic(tmp_path):
    out = []
    for i in range(2):
        s = RunStore(f"sqlite:///{tmp_path / f'r{i}.db'}")
        s.create_schema()
        _, _, d = run_day(s, base_script(), [rack()])
        out.append((d.model_dump(), d.predicted.model_dump(),
                    [(u["seq"], u["role"], u["kind"], u["text"]) for u in s.table("utterances", RUN)]))
    assert out[0] == out[1]


def test_pick_thresholds_clamped(store):
    from engine import verifier
    prof = load_profile("customer_first")
    checks = verifier.template(prof)
    target = next(k for k in checks if k.max_threshold > k.min_threshold)
    llm = FakeLLM({("Thresholds", "program_manager"): Thresholds(values={target.check_name: 1e9})})
    policy = OrgPolicy(RUN, prof, llm, store)
    out = policy.pick_thresholds(checks)
    assert len(llm.calls) == 1 and llm.calls[0][3] is True
    got = next(k for k in out if k.check_name == target.check_name)
    assert got.threshold == target.max_threshold
    assert [k.threshold for k in out if k.check_name != target.check_name] == \
        [k.threshold for k in checks if k.check_name != target.check_name]


def test_hire_released_after_two_quiet_days(store):
    llm = FakeLLM(base_script())
    policy = OrgPolicy(RUN, load_profile("customer_first"), llm, store)
    twin, state, view = setup()
    policy.decide(DAY, view, [rack()], twin, state)
    assert "safety" in policy.present
    for d in (DAY + 1, DAY + 2):
        policy.decide(d, view, [rack()], twin, state)  # rack event no longer new -> quiet
    assert "safety" not in policy.present
    h = [r for r in rows(store, "hires") if r["role"] == "safety"]
    assert h and h[0]["released_day"] == DAY + 2


# --------------------------------------------------------------------------- live (Crusoe)


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("GEMBA_LIVE") != "1", reason="set GEMBA_LIVE=1 to call Crusoe")
def test_live_rack_damage_standup(tmp_path):
    from llm.cache import ResponseCache
    from llm.client import LLM
    from org.standup import tier_for

    s = RunStore(f"sqlite:///{tmp_path / 'live.db'}")
    s.create_schema()
    llm = LLM(cache=ResponseCache(tmp_path / "cache.jsonl"))
    policy = OrgPolicy("live1", load_profile("customer_first"), llm, s)
    twin, state, view = setup()
    t0 = time.perf_counter()
    d = policy.decide(DAY, view, [rack()], twin, state)
    wall = time.perf_counter() - t0
    calls = s.table("llm_calls", "live1")
    print(f"\nLIVE: decision {d.option_id} by {d.decided_by}: {d.levers}\n  rationale: {d.rationale}"
          f"\n  calls={policy.llm_calls} logged={len(calls)} fallbacks={sum(1 for r in calls if r['kind'] == 'llm_fallback')}"
          f" llm_latency={policy.llm_latency_ms / 1000:.1f}s wall={wall:.1f}s usd={policy.llm_usd:.5f}"
          f" ultra->{tier_for('ultra')}")
    for u in s.table("utterances", "live1"):
        print(f"  [{u['seq']}] {u['role']} {u['kind']}: {u['text'][:140]}")
    assert isinstance(d, c.Decision)
    assert len(s.table("evidence", "live1")) >= 1
    assert len(s.table("utterances", "live1")) >= 3
