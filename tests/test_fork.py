import json

import pytest

import contracts as c
from engine import events as ev
from engine.fork import fork_run
from engine.run import run
from engine.store import RunStore
from engine.stub_twin import StubTwin
from eval.baselines import NaiveBigBang, StaticRules


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMBA_SNAPSHOT_DIR", str(tmp_path / "snaps"))
    s = RunStore(f"sqlite:///{tmp_path / 'runs.db'}")
    s.create_schema()
    return s


def _kpis(store, rid):
    return {r["day"]: json.dumps(r["kpis"], sort_keys=True) for r in store.day_results(rid)}


@pytest.fixture
def parent(store):
    return run("reslot_default", "customer_first", StaticRules(), StubTwin(), store=store)


@pytest.mark.parametrize("lever,first_diff", [(c.CloseAisle(aisle=7, days=3), 69), (c.PauseReslot(days=5), 70)])
def test_fork_diverges_from_fork_day(store, parent, lever, first_diff):
    child = fork_run(parent, 69, [lever], twin=StubTwin(), store=store)
    row = store.get_run(child)
    assert row["parent_run_id"] == parent and row["fork_day"] == 69 and row["status"] == "done"
    assert row["fork_decision"]["levers"][0]["kind"] == lever.kind
    p, k = _kpis(store, parent), _kpis(store, child)
    assert sorted(k) == list(range(61, 91))
    assert all(p[d] == k[d] for d in range(61, 69))
    assert any(p[d] != k[d] for d in range(69, 91))
    assert p[first_diff] != k[first_diff]
    [forced] = [d for d in store.decisions(child) if d["day"] == 69]
    assert forced["decided_by"] == "fork" and forced["decision_id"] == f"{child}-fork"
    assert forced["levers"][0]["decision_id"] == f"{child}-fork"
    [nxt] = [d for d in store.decisions(child) if d["day"] == 70]
    assert nxt["decided_by"] == "static" and nxt["decision_id"] == f"{child}-d70"


def test_fork_relogs_pending_consequences(store):
    parent = run("reslot_default", "customer_first", NaiveBigBang(), StubTwin(), store=store)
    esc = [e for e in store.events(parent) if e["kind"] == "customer_escalation"]
    assert esc
    day = esc[0]["day"]  # fork on the escalation's own day: generated the day before, still pending
    child = fork_run(parent, day, [], twin=StubTwin(), store=store, policy_name="do_nothing")
    assert esc[0]["event_id"] in {e["event_id"] for e in store.events(child)}
    edges = store.cascade(child, f"evt:{esc[0]['event_id']}")
    assert any(e["from_id"].startswith("dec:") for e in edges)
    node = f"evt:{esc[0]['event_id']}"
    assert len([e for e in store.table("cascade_edges", child) if e["to_id"] == node]) == 1  # no duplicate edge


def test_fork_on_first_day_without_snapshot(store, parent):
    child = fork_run(parent, 61, [c.PauseReslot(days=2)], twin=StubTwin(), store=store)
    assert len(store.day_results(child)) == 30


def test_fork_with_constraint_event(store, parent):
    con = c.Event(event_id="fork-rack-69", kind="rack_damage", day=69, args={"aisle": 3, "days": 2},
                  announced_day=69, source="inbox")
    child = fork_run(parent, 69, [], [con], twin=StubTwin(), store=store)
    [row] = store.table("constraints", child)
    assert row["constraint_id"] == "fork-rack-69" and row["day_end"] == 70
    assert _kpis(store, parent)[69] != _kpis(store, child)[69]  # delivered to step_day on its start day


class RestoringOrg(StaticRules):
    """Stands in for OrgPolicy: records kwargs, restore() calls and exposes snapshot_state()."""
    name = "org"
    instances: list = []

    def __init__(self, run_id, llm=None, profile=None, store=None, playbook_rules=None, seed=0, scenario_end=None):
        super().__init__(run_id)
        self.kwargs = dict(profile=profile, store=store, playbook_rules=playbook_rules, seed=seed,
                           scenario_end=scenario_end)
        self.restored = []
        self.days = []
        RestoringOrg.instances.append(self)

    def decide(self, day, view, events, twin, state):
        self.days.append(day)
        return super().decide(day, view, events, twin, state)

    def restore(self, payload, forced):
        self.restored.append((payload, forced))

    def snapshot_state(self):
        return {"days_decided": list(self.days)}


def test_fork_restores_org_with_forced_decision(store, monkeypatch):
    RestoringOrg.instances = []
    monkeypatch.setattr("org.standup.OrgPolicy", RestoringOrg)
    sc = c.Scenario.model_validate({**ev.load_scenario("reslot_default").model_dump(), "days": (61, 72)})
    parent = run(sc, "customer_first", RestoringOrg("", store=store), StubTwin(), store=store)
    child = fork_run(parent, 66, [c.PauseReslot(days=2)], twin=StubTwin(), store=store, policy_name="org",
                     horizon=3)
    pol = RestoringOrg.instances[-1]
    assert pol.kwargs["store"] is store and pol.kwargs["profile"].id == "customer_first"
    assert pol.kwargs["scenario_end"] == 72 and pol.kwargs["seed"] == 1  # the parent's end, not the horizon
    [(payload, forced)] = pol.restored
    assert payload["org_state"] == {"days_decided": [61, 62, 63, 64, 65]}  # parent's end of day 65
    assert forced.decided_by == "fork" and forced.day == 66 and forced.decision_id == f"{child}-fork"
    assert [lv.kind for lv in forced.levers] == ["pause_reslot"] and forced.levers[0].decision_id == forced.decision_id
    assert pol.days == [67, 68]  # day 66 is the forced decision


def test_fork_first_day_restore_gets_empty_payload(store, monkeypatch):
    RestoringOrg.instances = []
    monkeypatch.setattr("org.standup.OrgPolicy", RestoringOrg)
    parent = run("reslot_default", "customer_first", StaticRules(), StubTwin(), store=store)
    fork_run(parent, 61, [], twin=StubTwin(), store=store, policy_name="org", horizon=1)
    [(payload, forced)] = RestoringOrg.instances[-1].restored
    assert payload == {} and forced.day == 61
