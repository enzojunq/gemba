"""learn.retro and learn.gate without network: a scripted fake LLM (canned CoachProposals),
StubTwin, baseline policies.

Verification forks use policy_name="static" (an LLM-free baseline): the parent here is a
NaiveBigBang run, and forking with "big_bang" would restart the big bang on the fork day.
"""
import pytest

import contracts as c
from engine import events as ev
from engine.run import run
from engine.store import RunStore
from engine.stub_twin import StubTwin
from eval.baselines import NaiveBigBang, StaticRules
from learn import gate as gate_mod
from learn import playbook as pb
from learn.retro import CoachProposal, find_ref_run, regret_days, retro, retro_full, trace
from org.scoring import load_profile


class FakeMeta:
    def __init__(self, fallback_used=False):
        self.usd, self.latency_ms, self.fallback_used = 0.0, 0, fallback_used


class FakeCoach:
    """Returns the scripted proposals in order (the last one repeats); records every call."""
    on_call = "absent"

    def __init__(self, *proposals, fail=False):
        self.proposals, self.fail, self.calls = list(proposals), fail, []

    def complete(self, tier, messages, schema, *, role, run_id, reasoning=False, max_tokens=800, fallback=None,
                 **_):
        self.calls.append(dict(tier=tier, messages=messages, schema=schema, role=role, run_id=run_id,
                               reasoning=reasoning, max_tokens=max_tokens))
        if self.fail:
            return fallback, FakeMeta(fallback_used=True)
        p = self.proposals[min(len(self.calls) - 1, len(self.proposals) - 1)]
        return p, FakeMeta()


LESSON = c.Lesson(role="reslotting_engineer", tags=["mispick_rate_high", "customer_escalation"],
                  trigger="two big reslot nights in a row raise key-customer mispicks",
                  advice="Never run two maximum reslot nights back to back; pause a night so pickers relearn moved SKUs.")
PAUSE2 = CoachProposal(alternative_levers=[c.PauseReslot(days=2)], lesson=LESSON,
                       why="The second 280-leg night doubled the learning dip.")
BIGGER = CoachProposal(alternative_levers=[c.ReslotTonight(n_moves=1200), c.HireTemps(count=8, start_day=62, days=3)],
                       lesson=c.Lesson(role="ops_manager", tags=["on_time_pct"], trigger="t",
                                       advice="Push harder: more moves and more temps."), why="more")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMBA_SNAPSHOT_DIR", str(tmp_path / "snaps"))
    s = RunStore(f"sqlite:///{tmp_path / 'runs.db'}")
    s.create_schema()
    return s


@pytest.fixture
def parent(store):
    sc = c.Scenario.model_validate({**ev.load_scenario("reslot_default").model_dump(), "days": (61, 66)})
    return run(sc, "customer_first", NaiveBigBang(), StubTwin(), store=store)


def test_regret_and_trace(store, parent):
    prof = load_profile("customer_first")
    ref = find_ref_run(store, parent, twin=StubTwin(), profile=prof)
    assert ref != parent and all(d["decided_by"] == "do_nothing" for d in store.decisions(ref))
    assert find_ref_run(store, parent, twin=StubTwin(), profile=prof) == ref  # found, not re-run
    rds = regret_days(store, parent, ref, prof, max_regret_days=3)
    assert 1 <= len(rds) <= 3
    tr = trace(store, rds[0])
    assert tr is not None and tr.decision["levers"]
    assert tr.chain.startswith("dec:")


def test_retro_accepts_pause_lesson(store, parent):
    llm = FakeCoach(PAUSE2)
    res = retro_full([parent], store=store, llm=llm, twin=StubTwin(), profile="customer_first", version="v0",
                     max_regret_days=1, horizon=3, policy_name="static")
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["reasoning"] is True and call["max_tokens"] == 2000 and call["role"] == "retro_coach"
    assert call["schema"] is CoachProposal and "Cascade:" in call["messages"][1]["content"]
    assert "Retro Coach" in call["messages"][0]["content"]

    verdict = (res.accepted + res.rejected)[0]
    fork = store.get_run(verdict.fork_run_id)
    assert fork["parent_run_id"] == parent and fork["fork_day"] == verdict.day
    fork_days = [r["day"] for r in store.day_results(verdict.fork_run_id) if r["day"] >= verdict.day]
    assert fork_days == list(range(verdict.day, min(66, verdict.day + 2) + 1))  # horizon trims the fork
    # On the stub, pausing after the big bang beats the parent over the window.
    assert verdict.accepted, (verdict.score_delta, verdict.breaches_fork, verdict.breaches_parent)
    assert verdict.fork_score > verdict.parent_score

    rows = store.playbook_rows("v1")
    assert len(rows) == 1 and rows[0]["status"] == "candidate"
    assert rows[0]["role"] == "reslotting_engineer"
    assert rows[0]["tags"] == ["mispick_rate", "customer_escalation"]  # normalised to the standup's tags
    ev = rows[0]["evidence"]
    assert ev["run_id"] == parent and ev["fork_run_id"] == verdict.fork_run_id and ev["day"] == verdict.day
    assert ev["score_delta"] > 0 and rows[0]["score_delta"] == pytest.approx(ev["score_delta"])
    assert pb.load_rules(store, "v1")[0]["advice"] == LESSON.advice  # under the gate: candidates load


def test_retro_rejects_worse_alternative(store, parent):
    accepted = retro([parent], store=store, llm=FakeCoach(BIGGER), twin=StubTwin(), profile="customer_first",
                     max_regret_days=1, horizon=3, policy_name="static")
    assert accepted == []
    assert store.playbook_rows("v1") == []
    forks = [r for r in store.list_runs("fork") if r["parent_run_id"] == parent]
    assert len(forks) == 1


def test_retro_coach_fallback(store, parent):
    res = retro_full([parent], store=store, llm=FakeCoach(fail=True), twin=StubTwin(), profile="customer_first",
                     max_regret_days=1, horizon=2, policy_name="static")
    assert res.accepted == [] and res.rejected == [] and res.forks == 0  # coach fallback: no lesson
    assert res.coach_calls == 1 and store.playbook_rows("v1") == []
    assert any(r.get("kind") == "llm_fallback" and r.get("role") == "retro_coach"
               for r in store.table("llm_calls", parent))


def test_retro_cli_accepts_playbook_alias(store, parent, monkeypatch):
    import learn.retro as lr
    seen = {}
    monkeypatch.setattr(lr, "retro_full", lambda ids, **kw: seen.update(ids=ids, **kw) or lr.RetroResult(
        candidate_version="v2"))
    monkeypatch.setattr("llm.client.LLM", lambda **kw: FakeCoach(PAUSE2))
    assert lr.main(["--train", parent, "--playbook", "v1", "--store", store.url, "--policy", "static"]) == 0
    assert seen["ids"] == [parent] and seen["version"] == "v1"


# --------------------------------------------------------------------------- gate


class Row:
    def __init__(self, contender, score, violations, run_id="r"):
        self.contender, self.run_id = contender, run_id
        self.metrics = {"score": score, "violations": violations}


def _seed_candidate(store):
    pb.save_rules(store, "v1", [pb.lesson_rule(LESSON, {"score_delta": 0.1})])


@pytest.mark.parametrize("rows,promoted", [
    ([Row("crew", 0.10, 2), Row("crew_v1", 0.20, 2), Row("crew", 0.00, 1), Row("crew_v1", 0.05, 1)], True),
    ([Row("crew", 0.10, 1), Row("crew_v1", 0.30, 3)], False),  # better score, more violations
    ([Row("crew", 0.10, 1), Row("crew_v1", 0.05, 0)], False),  # fewer violations, worse score
])
def test_gate(store, monkeypatch, rows, promoted):
    _seed_candidate(store)
    seen = {}

    def fake_evaluate(scenario_ids, profile_id, contenders, seeds=None, **kw):
        seen.update(scenario_ids=scenario_ids, contenders=contenders, seeds=seeds)
        return rows

    monkeypatch.setattr("eval.run_eval.evaluate", fake_evaluate)
    res = gate_mod.gate(["test_1", "test_2"], "customer_first", "v0", "v1", store=store, twin=StubTwin(),
                        seeds=[1])
    assert seen["contenders"] == ["crew", "crew_v1", "static"] and seen["scenario_ids"] == ["test_1", "test_2"]
    assert res.promoted is promoted
    status = {r["status"] for r in store.playbook_rows("v1")}
    assert status == ({"active"} if promoted else {"rejected"})
    assert pb.load_rules(store, "v1") == ([] if not promoted else pb.load_rules(store, "v1", ["active"]))
    [g] = [r for r in store.table("eval_results") if r["contender"] == "gate"]
    assert g["metrics"]["promoted"] is promoted


def test_gate_contender_names():
    assert gate_mod.contender_for("v0") == "crew" and gate_mod.contender_for("v2") == "crew_v2"


def _write_day(store, rid, day, levers, consequences=()):
    did = f"{rid}-d{day}"
    dec = c.Decision(decision_id=did, day=day, decided_by="org", rationale="r",
                     levers=[lv.model_copy(update={"decision_id": did}) for lv in levers])
    store.write_day(rid, day, dec, [], c.DayResult(day=day, kpis=c.Kpis(on_time_pct=96.0)),
                    new_consequences=list(consequences))
    return did


def test_trace_starts_from_delayed_consequence(store):
    from learn.retro import RegretDay
    rid = store.create_run("backtest", "reslot_default", "customer_first", "v0", 1)
    _write_day(store, rid, 61, [c.ReslotTonight(n_moves=200)])
    _write_day(store, rid, 62, [])
    d63 = _write_day(store, rid, 63, [c.Overtime(shift="day", pool="pm", hours=2)])
    late = c.Event(event_id="c3pm-late", kind="absenteeism", day=66, args={"pool": "pm", "pct": 15, "days": 3},
                   announced_day=66, source="consequence", caused_by=c.node_id(c.DEC, d63))
    after = c.Event(event_id="c3pm-after", kind="absenteeism", day=66, args={"pool": "pm", "pct": 15, "days": 3},
                    announced_day=66, source="consequence", caused_by=c.node_id(c.DEC, f"{rid}-d65"))
    _write_day(store, rid, 64, [], consequences=[late])
    _write_day(store, rid, 65, [c.Overtime(shift="day", pool="pm", hours=2)], consequences=[after])
    tr = trace(store, RegretDay(rid, 63, -0.1, 0.0, 0.1, [], ["on_time_pct"]))
    assert tr is not None and tr.via == "cascade"
    assert tr.decision["decision_id"] == d63  # day+3 consequence of the regret-day decision
    assert "evt:c3pm-late" in tr.chain


def test_retro_org_forks_get_policy_kwargs(store, parent, monkeypatch):
    seen = []

    class FakeOrg(StaticRules):
        name = "org"

        def __init__(self, run_id, llm=None, profile=None, store=None, playbook_rules=None, seed=0,
                     scenario_end=None):
            super().__init__(run_id)
            seen.append(dict(llm=llm, profile=profile, store=store, playbook_rules=playbook_rules, seed=seed,
                             scenario_end=scenario_end))

    monkeypatch.setattr("org.standup.OrgPolicy", FakeOrg)
    llm = FakeCoach(PAUSE2)
    retro_full([parent], store=store, llm=llm, twin=StubTwin(), profile="customer_first", version="v0",
               max_regret_days=1, horizon=2, policy_name="org")
    assert seen, "the verification fork should build the org policy"
    kw = seen[-1]
    assert kw["llm"] is llm and kw["store"] is store and kw["profile"].id == "customer_first"
    assert kw["scenario_end"] == 66 and kw["seed"] == 1 and kw["playbook_rules"] == []


def test_retro_refuses_real_twin_instance(store, parent):
    class RealishTwin:
        pass

    with pytest.raises(TypeError):
        retro_full([parent], store=store, llm=FakeCoach(PAUSE2), twin=RealishTwin(), profile="customer_first",
                   version="v0", max_regret_days=1, horizon=2, policy_name="static")


# ---------------------------------------------------------------- integrity of the acceptance rule


def test_retro_rejects_when_floor_rejected_alternative(store, parent, monkeypatch):
    real = store.decisions

    def decisions(run_id):
        rows = real(run_id)
        if (store.get_run(run_id) or {}).get("parent_run_id"):
            rows = [{**r, "rejections": [{"lever": {"kind": "pause_reslot", "days": 2}, "reason": "nope"}]}
                    for r in rows]
        return rows
    monkeypatch.setattr(store, "decisions", decisions)
    res = retro_full([parent], store=store, llm=FakeCoach(PAUSE2), twin=StubTwin(), profile="customer_first",
                     max_regret_days=1, horizon=3, policy_name="static")
    assert res.accepted == [] and len(res.rejected) == 1
    v = res.rejected[0]
    assert v.reason.startswith("alternative rejected by the floor: pause_reslot: nope")
    assert v.evidence["reason"] == v.reason


def test_retro_rejects_below_margin(store, parent, monkeypatch):
    import learn.retro as rm
    monkeypatch.setattr(rm, "RETRO_MIN_DELTA", 1e9)
    res = retro_full([parent], store=store, llm=FakeCoach(PAUSE2), twin=StubTwin(), profile="customer_first",
                     max_regret_days=1, horizon=3, policy_name="static")
    assert res.accepted == [] and "min_delta" in res.rejected[0].reason
    ev_ = res.rejected[0].evidence
    assert ev_["min_delta"] == 1e9 and "delta" in ev_ and ev_["fork_fallbacks"] == 0 and ev_["parent_fallbacks"] == 0


def test_retro_rejects_with_parent_fallbacks(store, parent):
    import uuid
    store.write_llm_call(call_id=uuid.uuid4().hex, run_id=parent, day=None, role="ops_manager", model="(code)",
                         request={}, response={"llm_fallback": True}, usd=0.0, kind="llm_fallback",
                         fallback_used=True)
    res = retro_full([parent], store=store, llm=FakeCoach(PAUSE2), twin=StubTwin(), profile="customer_first",
                     max_regret_days=1, horizon=3, policy_name="static")
    assert res.accepted == [] and res.rejected[0].parent_fallbacks == 1
    assert "fallback" in res.rejected[0].reason


def test_find_ref_run_ignores_older_do_nothing(store):
    from engine.run import make_policy
    sc = c.Scenario.model_validate({**ev.load_scenario("reslot_default").model_dump(), "days": (61, 66)})
    old = run(sc, "customer_first", make_policy("do_nothing", ""), StubTwin(), store=store, mode="retro")
    import time
    time.sleep(0.01)
    par = run(sc, "customer_first", NaiveBigBang(), StubTwin(), store=store)
    ref = find_ref_run(store, par, twin=StubTwin(), profile=load_profile("customer_first"))
    assert ref not in (old, par)
    assert store.get_run(ref)["started_at"] > store.get_run(par)["started_at"]


def test_fork_day_excluded_from_breaches(store, parent, monkeypatch):
    import learn.retro as rm
    seen = []
    real = rm.hard_breaches
    monkeypatch.setattr(rm, "hard_breaches", lambda s, rid, days: (seen.append(list(days)), real(s, rid, days))[1])
    res = retro_full([parent], store=store, llm=FakeCoach(PAUSE2), twin=StubTwin(), profile="customer_first",
                     max_regret_days=1, horizon=3, policy_name="static")
    v = (res.accepted + res.rejected)[0]
    assert seen and all(v.day not in ds for ds in seen) and all(min(ds) == v.day + 1 for ds in seen if ds)
    assert v.evidence["ref_run_id"]


def test_retro_asserts_playbook_version(store, parent):
    with pytest.raises(ValueError, match="playbook"):
        retro_full([parent], store=store, llm=FakeCoach(PAUSE2), twin=StubTwin(), profile="customer_first",
                   version="v3", max_regret_days=1, horizon=3, policy_name="static")


def test_gate_fallbacks_no_verdict(store, monkeypatch):
    import uuid
    _seed_candidate(store)
    store.write_llm_call(call_id=uuid.uuid4().hex, run_id="rb", day=1, role="ops_manager", model="(code)",
                         request={}, response={}, usd=0.0, kind="llm_fallback", fallback_used=True)
    rows = [Row("crew", 0.10, 1, "ra"), Row("crew_v1", 0.50, 0, "rb"), Row("static", 0.0, 0, "rs")]
    monkeypatch.setattr("eval.run_eval.evaluate", lambda *a, **k: rows)
    res = gate_mod.gate(["test_1"], "customer_first", "v0", "v1", store=store, twin=StubTwin(), seeds=[1])
    assert res.promoted is False and res.summary["reason"] == "fallbacks present: no verdict"
    assert {r["status"] for r in store.playbook_rows("v1")} == {"candidate"}
    [g] = [r for r in store.table("eval_results") if r["contender"] == "gate"]
    assert g["metrics"]["fallbacks_from"] == 0 and g["metrics"]["fallbacks_to"] == 1
