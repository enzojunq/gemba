import json
import time

import pytest

import contracts as c
from engine import api, events as ev
from engine import run as engine_run
from engine.run import make_policy, run
from engine.store import RunStore
from engine.stub_twin import StubTwin
from eval.baselines import NaiveBigBang, StaticRules


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMBA_SNAPSHOT_DIR", str(tmp_path / "snaps"))
    s = RunStore(f"sqlite:///{tmp_path / 'runs.db'}")
    s.create_schema()
    return s


def test_scenario_and_calendar():
    sc = ev.load_scenario("reslot_default")
    assert sc.days == (61, 90) and sc.seed == 1 and len(sc.events) == 8
    cal = ev.build_calendar(sc, "r")
    surge = next(e for e in cal if e.kind == "demand_surge")
    assert surge.event_id == "evt-demand_surge-69" and surge.announced_day == 67
    assert surge.source == "scripted" and surge.caused_by is None
    visit = next(e for e in cal if e.kind == "leadership_visit")
    assert visit.args["no_moves_in_aisles"] == [10, 11, 12, 13, 14] and visit.announced_day == 73


def test_30_day_static_run(store):
    t0 = time.perf_counter()
    rid = run("reslot_default", "customer_first", StaticRules(), StubTwin(), store=store)
    assert time.perf_counter() - t0 < 5
    assert len(store.day_results(rid)) == 30
    decs = store.decisions(rid)
    assert len(decs) == 30 and decs[0]["decision_id"] == f"{rid}-d61"
    assert store.get_run(rid)["status"] == "done"
    assert {e["event_id"] for e in store.events(rid)} >= {"evt-labor_shortage-63", "evt-returns_spike-86"}
    assert store.day_results(rid)[0]["snapshot_path"].endswith("61.pkl")


def test_determinism(store):
    a = run("reslot_default", "customer_first", StaticRules(), StubTwin(), store=store, seed=1)
    b = run("reslot_default", "customer_first", StaticRules(), StubTwin(), store=store, seed=1)
    ka = [json.dumps(r["kpis"], sort_keys=True) for r in store.day_results(a)]
    kb = [json.dumps(r["kpis"], sort_keys=True) for r in store.day_results(b)]
    assert ka == kb and len(ka) == 30


def test_cascade_reaches_decision(store):
    rid = run("reslot_default", "customer_first", NaiveBigBang(), StubTwin(), store=store)
    esc = [e for e in store.events(rid) if e["kind"] == "customer_escalation"]
    assert esc, "C2 should fire after two 280-leg nights"
    edges = store.cascade(rid, f"evt:{esc[0]['event_id']}")
    nodes = {e["from_id"] for e in edges} | {e["to_id"] for e in edges}
    assert any(n.startswith("dec:") for n in nodes)


def test_days_override_cli(store, capsys):
    engine_run.main(["--days", "61-70", "--policy", "static", "--twin", "stub", "--store", store.url])
    out = capsys.readouterr().out.strip().splitlines()
    rid = out[0]
    assert len(out) == 6
    assert [r["day"] for r in store.day_results(rid)] == list(range(61, 71))


def test_make_policy():
    assert isinstance(make_policy("static", "r"), StaticRules)
    with pytest.raises(ValueError):
        make_policy("nope", "r")


def test_failed_run_is_marked(store):
    class Boom(StaticRules):
        def decide(self, day, view, events, twin, state):
            if day == 63:
                raise RuntimeError("boom")
            return super().decide(day, view, events, twin, state)

    with pytest.raises(RuntimeError):
        run("reslot_default", "customer_first", Boom(), StubTwin(), store=store)
    [r] = store.list_runs()
    assert r["status"] == "failed" and len(store.day_results(r["run_id"])) == 2


def _wait(rid, store, timeout=10.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        st = api.run_status(rid, store=store)
        if st in ("done", "failed"):
            return st
        time.sleep(0.05)
    return api.run_status(rid, store=store)


def test_api_start_run(store):
    rid = api.start_run("reslot_default", "customer_first", "v0", policy="static", seed=1, store=store,
                        twin=StubTwin())
    assert api.run_status(rid, store=store) in ("queued", "running", "done")
    assert _wait(rid, store) == "done"
    assert len(store.day_results(rid)) == 30


def test_api_fork_and_failed_org(store):
    parent = api.start_run("reslot_default", "customer_first", "v0", policy="static", store=store,
                           twin=StubTwin(), days=(61, 72))
    assert _wait(parent, store) == "done"
    child = api.fork_run(parent, 66, [c.PauseReslot(days=2)], store=store, twin=StubTwin())
    assert _wait(child, store) == "done"
    assert store.get_run(child)["parent_run_id"] == parent
    assert [r["day"] for r in store.day_results(child)] == list(range(61, 73))
    try:
        import org.standup  # noqa: F401
    except ImportError:
        bad = api.start_run("reslot_default", "customer_first", "v0", policy="org", store=store, twin=StubTwin())
        assert _wait(bad, store) == "failed"


# --------------------------------------------------------------------------- review fixes (B)

import builtins
import logging
import sys

from engine import drift as drift_mod
from eval.baselines import DoNothing


def _short(days=(61, 62)):
    return c.Scenario.model_validate({**ev.load_scenario("reslot_default").model_dump(), "days": days})


class FakeOrg(StaticRules):
    """Stands in for org.standup.OrgPolicy: records its kwargs and the optional hooks."""
    name = "org"
    instances: list = []

    def __init__(self, run_id, llm=None, profile=None, store=None, playbook_rules=None, seed=0, scenario_end=None):
        super().__init__(run_id)
        self.kwargs = dict(llm=llm, profile=profile, store=store, playbook_rules=playbook_rules, seed=seed,
                           scenario_end=scenario_end)
        self.llm = None
        self.pending_inbox: list[str] = []
        self.rejections: list = []
        self.restored: list = []
        FakeOrg.instances.append(self)

    def on_rejections(self, day, rejections):
        self.rejections.append((day, list(rejections)))

    def restore(self, payload, forced):
        self.restored.append((payload, forced))

    def snapshot_state(self):
        return {"n_rejections": len(self.rejections)}


@pytest.fixture
def fake_org(monkeypatch):
    FakeOrg.instances = []
    monkeypatch.setattr("org.standup.OrgPolicy", FakeOrg)
    return FakeOrg


def test_policy_kwargs_reach_org_policy_cli(store, fake_org):
    engine_run.main(["--days", "61-63", "--policy", "org", "--twin", "stub", "--store", store.url, "--seed", "7"])
    [pol] = fake_org.instances
    kw = pol.kwargs
    assert kw["profile"].id == "customer_first" and kw["store"] is not None and kw["store"].url == store.url
    assert kw["scenario_end"] == 63 and kw["seed"] == 7 and kw["playbook_rules"] == []


def test_policy_kwargs_reach_org_policy_api(store, fake_org):
    rid = api.start_run("reslot_default", "customer_first", "v0", policy="org", store=store, twin=StubTwin(),
                        days=(61, 62))
    assert _wait(rid, store) == "done"
    [pol] = fake_org.instances
    assert pol.kwargs["store"] is store and pol.kwargs["scenario_end"] == 62 and pol.kwargs["seed"] == 1
    assert pol.kwargs["profile"].id == "customer_first"


def test_policy_kwargs_helper_loads_playbook(store, monkeypatch):
    seen = {}

    def load_rules(s, v):
        seen["args"] = (s, v)
        return [{"rule_id": "r"}]

    monkeypatch.setattr("learn.playbook.load_rules", load_rules)
    sc = ev.load_scenario("train_1")
    kw = engine_run.policy_kwargs("customer_first", store, "v2", sc)
    assert seen["args"] == (store, "v2") and kw["playbook_rules"] == [{"rule_id": "r"}]
    assert kw["seed"] == 11 and kw["scenario_end"] == 70 and kw["profile"].id == "customer_first"
    assert engine_run.policy_kwargs("customer_first", store, "v0", sc)["playbook_rules"] == []


def _import_raising(monkeypatch, name, exc):
    real = builtins.__import__

    def fake(mod, *a, **k):
        if mod == name:
            raise exc
        return real(mod, *a, **k)

    monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(builtins, "__import__", fake)


def test_make_twin_auto_reraises_foreign_missing_module(monkeypatch):
    _import_raising(monkeypatch, "floor.twin", ModuleNotFoundError("No module named 'numpyx'", name="numpyx"))
    with pytest.raises(ModuleNotFoundError) as ei:
        engine_run.make_twin("auto")
    assert ei.value.name == "numpyx"


def test_make_twin_auto_falls_back_only_for_floor(monkeypatch, caplog):
    _import_raising(monkeypatch, "floor.twin", ModuleNotFoundError("No module named 'floor.twin'", name="floor.twin"))
    with caplog.at_level(logging.WARNING, logger="engine.run"):
        assert isinstance(engine_run.make_twin("auto"), StubTwin)
    assert "StubTwin" in caplog.text


def test_make_twin_auto_propagates_other_errors(monkeypatch):
    _import_raising(monkeypatch, "floor.twin", ValueError("bug in A's module"))
    with pytest.raises(ValueError):
        engine_run.make_twin("auto")


def test_runs_twin_is_recorded(store):
    rid = run(_short(), "customer_first", StaticRules(), StubTwin(), store=store)
    assert store.get_run(rid)["twin"] == "StubTwin"


class BadAislePolicy(FakeOrg):
    def decide(self, day, view, events, twin, state):
        did = f"{self.run_id}-d{day}"
        return c.Decision(decision_id=did, day=day, decided_by="org", rationale="r",
                          levers=[c.CloseAisle(aisle=999, days=1, decision_id=did),
                                  c.PauseReslot(days=1, decision_id=did)])


def test_rejected_lever_dropped_and_reported(store):
    pol = BadAislePolicy("")
    rid = run(_short(), "customer_first", pol, StubTwin(), store=store)
    d61 = store.decisions(rid)[0]
    assert [lv["kind"] for lv in d61["levers"]] == ["pause_reslot"]
    assert [r["lever"]["kind"] for r in d61["rejections"]] == ["close_aisle"]
    assert [(day, [r.lever.kind for r in rs]) for day, rs in pol.rejections] == \
        [(61, ["close_aisle"]), (62, ["close_aisle"])]


class TodayConsequenceTwin(StubTwin):
    def __init__(self):
        super().__init__()
        self.seen: dict[int, list[str]] = {}

    def step_day(self, state, day, demand, events):
        self.seen[day] = [e.event_id for e in events]
        return super().step_day(state, day, demand, events)

    def consequences(self, state, result, run_id):
        if result.day != 61:
            return []
        return [c.Event(event_id="late-c", kind="director_escalation", day=61, args={"metric": "on_time_pct"},
                        announced_day=61, source="consequence", caused_by=None)]


def test_consequence_dated_today_is_redated(store):
    tw = TodayConsequenceTwin()
    rid = run(_short(), "customer_first", DoNothing(), tw, store=store)
    [row] = [e for e in store.events(rid) if e["event_id"] == "late-c"]
    assert row["day"] == 62
    assert "late-c" in tw.seen[62] and "late-c" not in tw.seen[61]


class ViewTwin(StubTwin):
    def __init__(self, **over):
        super().__init__()
        self.over = over

    def view(self, state):
        return super().view(state).model_copy(update=self.over)


@pytest.mark.parametrize("over,expected", [
    ({"move_queue": 0, "moves_done_total": 10, "moves_target_total": 100}, False),
    ({"move_queue": 50, "moves_done_total": 100, "moves_target_total": 100}, True),
])
def test_stall_explained_uses_totals(store, monkeypatch, over, expected):
    seen = []
    orig = drift_mod.DriftDetector.check

    def spy(self, day, obs):
        seen.append(obs["explained_stall"])
        return orig(self, day, obs)

    monkeypatch.setattr(drift_mod.DriftDetector, "check", spy)
    run(_short(), "customer_first", DoNothing(), ViewTwin(**over), store=store)
    assert seen == [expected, expected]


def test_failed_run_stores_error(store):
    class Boom(StaticRules):
        def decide(self, day, view, events, twin, state):
            raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError):
        run("reslot_default", "customer_first", Boom(), StubTwin(), store=store)
    [r] = store.list_runs()
    assert r["status"] == "failed" and "kaboom" in r["error"] and store.run_error(r["run_id"]) == r["error"]
    assert api.run_error(r["run_id"], store=store) == r["error"]


def test_unparseable_inbox_goes_to_pending_inbox(store):
    sc = _short()
    rid = store.create_run("backtest", sc.id, "customer_first", "v0", sc.seed)
    store.inbox_push(rid, "the dock door 4 is jammed", day_hint=62)
    pol = FakeOrg(rid)
    run(sc, "customer_first", pol, StubTwin(), store=store, run_id=rid)
    assert pol.pending_inbox == ["the dock door 4 is jammed"]


def test_snapshot_carries_org_state(store):
    from engine import snapshot
    pol = BadAislePolicy("")
    rid = run(_short(), "customer_first", pol, StubTwin(), store=store)
    rows = store.day_results(rid)
    assert snapshot.load(rows[0]["snapshot_path"])["org_state"] == {"n_rejections": 1}
    plain = run(_short(), "customer_first", StaticRules(), StubTwin(), store=store)
    assert snapshot.load(store.day_results(plain)[0]["snapshot_path"])["org_state"] is None
