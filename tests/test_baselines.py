import contracts as c
from engine.stub_twin import StubTwin
from eval import POLICIES, DoNothing, NaiveBigBang, StaticRules

SC = c.Scenario(id="s", seed=1, days=(61, 65))


def _ctx():
    twin = StubTwin()
    s = twin.initial_state(SC)
    return twin, s, twin.view(s)


def test_protocol():
    for cls in POLICIES.values():
        assert isinstance(cls("bt-1"), c.DecisionPolicy)


def test_static_rules():
    twin, s, v = _ctx()
    p = StaticRules("bt-1")
    ev = c.Event(event_id="e", kind="rack_damage", day=62, args={"aisle": 1, "days": 1}, announced_day=62,
                 source="scripted")
    d61 = p.decide(61, v, [ev], twin, s)
    assert [(lv.kind, lv.n_moves, lv.decision_id) for lv in d61.levers] == [("reslot_tonight", 200, "bt-1-d61")]
    d62 = p.decide(62, v, [ev], twin, s)
    assert [lv.kind for lv in d62.levers] == ["pause_reslot"] and d62.levers[0].decision_id == "bt-1-d62"
    assert d62.decided_by == "static" and d62.option_id is None
    # a consequence event (its own doing) does not pause the baseline
    cons = c.Event(event_id="c", kind="replen_alert", day=63, args={"stockout_pct": 2.0}, announced_day=63,
                   source="consequence", caused_by="kpi:bt-1:62:stockout_lines")
    d63 = p.decide(63, v, [cons], twin, s)
    assert [lv.kind for lv in d63.levers] == ["reslot_tonight"]


def test_big_bang_and_do_nothing():
    twin, s, v = _ctx()
    p = NaiveBigBang("bt-1")
    ds = [p.decide(d, v, [], twin, s) for d in range(61, 65)]
    assert [len(d.levers) for d in ds] == [2, 1, 0, 0]
    assert all(lv.decision_id == d.decision_id for d in ds for lv in d.levers)
    assert ds[0].levers[0].n_moves == 1200 and ds[0].levers[1].kind == "hire_temps"
    dn = DoNothing("bt-1").decide(61, v, [], twin, s)
    assert dn.levers == [] and dn.decision_id == "bt-1-d61"
    checks = [c.DoneCheck(check_name="x", scope="daily", metric="on_time_pct", op=">=", threshold=95,
                          min_threshold=90, max_threshold=97)]
    assert DoNothing().pick_thresholds(checks) == checks


def test_static_runs_end_to_end_on_stub():
    twin = StubTwin()
    s = twin.initial_state(SC)
    p = StaticRules("bt-1")
    for d in SC.day_range:
        dec = p.decide(d, twin.view(s), [], twin, s)
        s, rej = twin.apply_levers(s, dec.levers, d)
        assert rej == []
        s, r = twin.step_day(s, d, twin.actual_demand(d), [])
    assert twin.view(s).moves_done_total == 1000
