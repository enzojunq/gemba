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


# --------------------------------------------------------------------------- review fixes


class RecordingTwin(StubTwin):
    """Records every what-if call (levers, days)."""

    def __init__(self):
        super().__init__()
        self.whatifs: list[tuple[list, int]] = []

    def whatif(self, state, levers, days, known_events):
        self.whatifs.append((list(levers), days))
        return super().whatif(state, levers, days, known_events)


class CapturingLLM(FakeLLM):
    def __init__(self, script=None):
        super().__init__(script)
        self.texts: list[str] = []

    def complete(self, tier, messages, schema=None, **kw):
        with self._lock:
            self.texts.extend(m["content"] for m in messages)
        return super().complete(tier, messages, schema, **kw)


def key_week(day: int = DAY) -> c.Event:
    return c.Event(event_id="kc1", kind="key_customer_week", day=day, args={"customer": "C001", "days": 5},
                   announced_day=day - 3, source="scripted")


@pytest.mark.parametrize("day,expect", [(DAY, 3), (89, 2), (90, 1)])
def test_horizon_clamped_to_scenario_end(store, day, expect):
    twin = RecordingTwin()
    policy = OrgPolicy(RUN, load_profile("customer_first"), FakeLLM({}), store, scenario_end=90)
    _, state, view = setup(twin, day)
    policy.decide(day, view, [rack(day)], twin, state)
    assert twin.whatifs and {d for _, d in twin.whatifs} == {expect}
    assert all(e["horizon_days"] == expect for e in rows(store, "evidence"))


def test_whatif_levers_carry_provisional_ids(store):
    twin = RecordingTwin()
    _, _, d = run_day(store, base_script(), [rack()], twin=twin)
    levers = [lv for lvs, _ in twin.whatifs for lv in lvs]
    assert levers and all(lv.decision_id.startswith(f"whatif:{RUN}:{DAY}:") for lv in levers)
    assert all(lv.decision_id == d.decision_id for lv in d.levers)


def test_lock_over_three_days_clamped_and_applied(store):
    twin = RecordingTwin()
    script = base_script({
        ("MaybeProposal", "quality"): MaybeProposal(proposal=None),
        ("QualityReply", "quality"): QualityReply(position=pos("o1", ["ev66-1"]),
                                                  lock=c.Lock(target="customer", ids=["C001"], days=7,
                                                              reason="key customer week")),
    })
    policy, _, d = run_day(store, script, [key_week()], twin=twin)
    prot = [lv for lv in d.levers if lv.kind == "protect"]
    assert prot and prot[0].days == 3 and prot[0].ids == ["C001"]
    locks = [u["text"] for u in rows(store, "utterances") if u["kind"] == "lock"]
    assert "lock clamped to 3 days (Quality alone)" in locks
    # the evidence was re-run with the Protect lever: predicted / last_whatif include it
    assert any(lv.kind == "protect" for lv in policy.last_whatif.levers)
    opts = rows(store, "options")
    assert all(any(lv["kind"] == "protect" for lv in o["levers"]) for o in opts)
    assert len(twin.whatifs) <= 1 + len(opts) + 3


def test_lock_survives_position_fallback(store):
    script = base_script({
        ("MaybeProposal", "quality"): MaybeProposal(proposal=None),
        ("QualityReply", "quality"): QualityReply(position=pos("o1", ["ev-bogus"]),
                                                  lock=c.Lock(target="customer", ids=["C001"], days=2,
                                                              reason="key customer week")),
    })
    _, llm, d = run_day(store, script, [key_week()])
    assert llm.count("QualityReply", "quality") == 2
    assert any(r["kind"] == "llm_fallback" and r["role"] == "quality" for r in rows(store, "llm_calls"))
    prot = [lv for lv in d.levers if lv.kind == "protect"]
    assert prot and prot[0].days == 2


def test_amendments_capped_at_two(store):
    twin = RecordingTwin()
    script = base_script({
        ("Position", "ops_manager"): pos("o1", ["ev66-1"], "amend",
                                         amendment=[c.ReslotTonight(n_moves=120, order="zone")]),
        ("Position", "reslotting_engineer"): pos("o1", ["ev66-1"], "amend",
                                                 amendment=[c.ReslotTonight(n_moves=130)]),
        ("SafetyReply", "safety"): SafetyReply(position=pos("o1", ["ev66-1"], "amend",
                                                            amendment=[c.CloseAisle(aisle=5, days=2)])),
    })
    run_day(store, script, [rack()], twin=twin)
    amended = [o for o in rows(store, "options") if o["option_id"] not in (opt(1), opt(2))]
    assert len(amended) == 2
    assert any("amendment dropped: cap 2" in u["text"] for u in rows(store, "utterances") if u["role"] == "safety")
    assert len(twin.whatifs) == 1 + 2 + 2


def test_display_ids_in_replies_map_to_store_ids(store):
    script = base_script({
        ("Position", "ops_manager"): pos("o2", ["ev66-2"]),
        ("ChairDecision", "program_manager"): c.ChairDecision(option_id="o1", escalate=False,
                                                              rationale="progress", evidence_ids=["ev66-1"]),
    })
    _, llm, d = run_day(store, script, [rack()])
    assert llm.count("Position", "ops_manager") == 1  # accepted first time
    ops = [u for u in rows(store, "utterances") if u["role"] == "ops_manager" and u["kind"] == "position"]
    assert ops[0]["option_id"] == opt(2) and ops[0]["evidence_ids"] == [ev(2)]
    assert d.option_id == opt(1)


def test_prompts_contain_no_run_id(store):
    run = "RUNzq9"
    llm = CapturingLLM({
        ("Proposal", "reslotting_engineer"): proposal("reslot 150 by value", c.ReslotTonight(n_moves=150)),
        ("MaybeProposal", "ops_manager"): MaybeProposal(proposal=proposal("reslot 100 by zone",
                                                                          c.ReslotTonight(n_moves=100, order="zone"))),
        ("QualityReply", "quality"): QualityReply(position=pos("o1", ["ev66-1"]),
                                                  lock=c.Lock(target="customer", ids=["C001"], days=2, reason="kc")),
        ("ChairDecision", "program_manager"): c.ChairDecision(option_id="o1", escalate=True,
                                                              rationale="escalate", evidence_ids=["ev66-1"]),
    })
    policy = OrgPolicy(run, load_profile("customer_first"), llm, store)
    twin, state, view = setup()
    policy.pending_inbox.append("garbled note from the floor")
    policy.decide(DAY, view, [rack(), key_week()], twin, state)
    assert llm.count("DirectorRuling") == 1
    # next day the board shows yesterday's lock (with its decision id) and evidence rows
    st, _ = twin.apply_levers(state, [c.Protect(target="customer", ids=["C001"], days=3,
                                                decision_id=f"{run}-d{DAY}")], DAY)
    policy.decide(DAY + 1, twin.view(st), [rack(DAY + 1)], twin, st)
    assert llm.texts and not [t for t in llm.texts if run in t]
    assert any("Inbox (unparsed)" in t and "garbled note" in t for t in llm.texts)
    assert any("(d66)" in t for t in llm.texts)


class Boom(FakeLLM):
    def complete(self, tier, messages, schema=None, **kw):
        if schema is not None and schema.__name__ == "ChairDecision":
            raise TimeoutError("read timed out")
        return super().complete(tier, messages, schema, **kw)


def test_transport_error_uses_fallback_and_logs(store):
    policy = OrgPolicy(RUN, load_profile("customer_first"), Boom(base_script()), store)
    twin, state, view = setup()
    d = policy.decide(DAY, view, [rack()], twin, state)
    assert isinstance(d, c.Decision) and d.option_id
    fb = [r for r in rows(store, "llm_calls") if r["model"] == "(transport)"]
    assert fb and fb[0]["kind"] == "llm_fallback" and fb[0]["role"] == "program_manager"
    assert "TimeoutError" in str(fb[0]["request"]) and fb[0]["day"] == DAY


def test_on_rejections_undoes_pause_and_briefs(store):
    script = base_script({
        ("ChairDecision", "program_manager"): c.ChairDecision(option_id="o2", escalate=False, rationale="hold",
                                                              evidence_ids=["ev66-2"]),
        ("MaybeProposal", "ops_manager"): MaybeProposal(proposal=proposal("pause", c.PauseReslot(days=2))),
    })
    llm = CapturingLLM(script)
    policy = OrgPolicy(RUN, load_profile("customer_first"), llm, store)
    twin, state, view = setup()
    d = policy.decide(DAY, view, [rack()], twin, state)
    assert [lv.kind for lv in d.levers] == ["pause_reslot"] and policy.paused_until == DAY + 1
    policy.on_rejections(DAY, [c.LeverRejection(lever=d.levers[0], reason="no moves queued")])
    assert policy.paused_until == 0
    assert policy.pending_rejections == ["rejected by floor: pause_reslot — no moves queued"]
    n = len(llm.calls)
    policy.decide(DAY + 1, view, [rack()], twin, state)  # otherwise quiet: the rejection makes it a meeting
    assert any(s == "ChairTriage" for s, *_ in llm.calls[n:])
    assert any("rejected by floor: pause_reslot — no moves queued" in t for t in llm.texts)
    assert policy.pending_rejections == []


def test_on_rejections_restores_standing_plan_and_overtime(store):
    policy = OrgPolicy(RUN, load_profile("customer_first"), FakeLLM(), store)
    rs = c.ReslotTonight(n_moves=50, order="zone", decision_id="d")
    ot = c.Overtime(shift="day", pool="pm", hours=6, decision_id="d")
    policy._apply_run_state(DAY, [rs, ot])
    wk = (DAY - 1) // 7
    assert policy.standing_budget == 50 and policy.overtime_used[wk] == 6
    policy.on_rejections(DAY, [c.LeverRejection(lever=ot, reason="pool empty")])
    assert policy.standing_budget == 50 and policy.overtime_used.get(wk, 0) == 0
    policy.on_rejections(DAY, [c.LeverRejection(lever=rs, reason="x")])
    assert policy.standing_budget == 200 and policy.standing_order == "value"


def test_snapshot_state_round_trips(store):
    import json
    policy, _, _ = run_day(store, base_script(), [rack()])
    policy.pending_rejections.append("rejected by floor: overtime — x")
    snap = json.loads(json.dumps(policy.snapshot_state()))
    assert snap["version"] == 1 and snap["last_ref"] and snap["last_ref_day"] == DAY
    hires = [h for h in rows(store, "hires") if h.get("released_day") is None]
    other = OrgPolicy("child", load_profile("customer_first"), FakeLLM(), store)
    other.restore({"hires": hires, "paused_until": 0, "org_state": snap}, None)
    assert other.snapshot_state() == snap
    assert other.present == policy.present and "safety" in other.present


def test_restore_rebuilds_present_and_applies_forced(store):
    policy = OrgPolicy("child", load_profile("customer_first"), FakeLLM(), store)
    hires = [{"role": "quality", "day": 60, "trigger": "key_customer_week", "released_day": None},
             {"role": "nobody", "day": 60, "trigger": "x", "released_day": None}]
    forced = c.Decision(decision_id="f1", day=DAY, decided_by="user", rationale="fork",
                        levers=[c.PauseReslot(days=3, decision_id="f1"), c.ReslotTonight(n_moves=80, decision_id="f1"),
                                c.Overtime(shift="day", pool="pm", hours=4, decision_id="f1")])
    policy.restore({"hires": hires, "paused_until": 2}, forced)
    assert policy.present == set(policy.always) | {"quality"}
    assert policy.quiet_streak == {"quality": 0}
    assert policy.paused_until == DAY + 2 and policy.standing_budget == 80
    assert policy.overtime_used == {(DAY - 1) // 7: 4.0}
    assert not rows(store, "decisions")
    policy.on_rejections(DAY, [c.LeverRejection(lever=forced.levers[0], reason="x")])
    assert policy.paused_until == 2


def test_violations_relative_to_ref(store):
    policy = OrgPolicy(RUN, load_profile("customer_first"), FakeLLM(), store)
    y = c.Kpis(lines=1000, on_time_pct=80.0, mispick_rate=1.0, pm_peak_zone_load=1.2, stockout_lines=40)
    assert set(policy._violations(y, DAY)) == {"on_time_pct", "mispick_rate", "pm_peak_zone_load", "stockout_lines"}
    ref = c.Kpis(lines=1000, on_time_pct=81.0, mispick_rate=0.9, pm_peak_zone_load=1.25, stockout_lines=30)
    policy.last_ref = c.WhatIf(levers=[], days=3, kpis_by_day=[ref, ref, ref], kpis_total=ref,
                               expected_near_misses=0.0, safety_risk_max=0.5, pm_peak_zone_load=1.25)
    policy.last_ref_day = DAY - 1
    assert policy._violations(y, DAY) == []
    worse = y.model_copy(update={"on_time_pct": 78.0, "mispick_rate": 1.2, "stockout_lines": 46,
                                 "safety_risk_max": 1.1})
    assert set(policy._violations(worse, DAY)) == {"on_time_pct", "mispick_rate", "stockout_lines", "safety_risk_max"}


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
