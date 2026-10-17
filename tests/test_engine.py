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
