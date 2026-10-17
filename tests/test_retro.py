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
from eval.baselines import NaiveBigBang
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
    v = (res.accepted + res.rejected)[0]
    assert v.fallback_used and v.alternative_levers == [c.PauseReslot(days=1)]
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
    assert seen["contenders"] == ["crew", "crew_v1"] and seen["scenario_ids"] == ["test_1", "test_2"]
    assert res.promoted is promoted
    status = {r["status"] for r in store.playbook_rows("v1")}
    assert status == ({"active"} if promoted else {"rejected"})
    assert pb.load_rules(store, "v1") == ([] if not promoted else pb.load_rules(store, "v1", ["active"]))
    [g] = [r for r in store.table("eval_results") if r["contender"] == "gate"]
    assert g["metrics"]["promoted"] is promoted


def test_gate_contender_names():
    assert gate_mod.contender_for("v0") == "crew" and gate_mod.contender_for("v2") == "crew_v2"
