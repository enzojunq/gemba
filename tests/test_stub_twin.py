import pickle

import pytest

import contracts as c
from engine.stub_twin import StubTwin

SC = c.Scenario(id="s", seed=3, days=(61, 70))


def _run(twin, levers_by_day, days=range(61, 64), events_by_day=None, run_id="r1"):
    s = twin.initial_state(SC)
    results, cons = [], []
    for d in days:
        s, _ = twin.apply_levers(s, levers_by_day.get(d, []), d)
        s, r = twin.step_day(s, d, twin.actual_demand(d), (events_by_day or {}).get(d, []))
        results.append(r)
        cons += twin.consequences(s, r, run_id)
    return s, results, cons


def reslot(n, did):
    return c.ReslotTonight(n_moves=n, decision_id=did)


def test_protocol_and_view_complete():
    twin = StubTwin()
    assert isinstance(twin, c.FloorTwin)
    v = twin.view(twin.initial_state(SC))
    assert v.day == 61 and v.moves_target_total == 2400 and v.move_queue == 2400
    assert v.labor_available["day"]["pm"] == 12 and v.labor_available["night"]["night_move"] == 4
    assert set(v.overtime_days_last4) == set(c.Pool.__args__)


def test_determinism_and_pickle():
    lv = {61: [reslot(280, "d61")], 62: [reslot(200, "d62")]}
    s1, r1, _ = _run(StubTwin(), lv)
    s2, r2, _ = _run(StubTwin(), lv)
    assert [r.kpis for r in r1] == [r.kpis for r in r2]
    assert pickle.loads(pickle.dumps(s1)) == s1


def test_close_aisle_and_rejections():
    twin = StubTwin()
    s0 = twin.initial_state(SC)
    s, rej = twin.apply_levers(s0, [c.CloseAisle(aisle=7, days=3, decision_id="d"),
                                    c.CloseAisle(aisle=99, days=1, decision_id="d"),
                                    c.ReslotTonight(n_moves=5, include_zones=[9], decision_id="d"),
                                    c.LaborAllocate(shift="day", pool="pm", headcount=25, decision_id="d")], 61)
    assert twin.view(s).closed_aisles == [7]
    assert twin.view(s0).closed_aisles == []  # copy on write
    assert len(rej) == 3 and "99" in rej[0].reason


def test_moves_dip_direction_and_cap():
    _, base, _ = _run(StubTwin(), {})
    s, moved, _ = _run(StubTwin(), {61: [reslot(1200, "d61")]})
    assert moved[0].kpis.moves_done == 280  # capped
    assert moved[1].kpis.mispick_rate > base[1].kpis.mispick_rate
    assert moved[1].kpis.on_time_pct < base[1].kpis.on_time_pct
    assert StubTwin().view(s).moves_done_total == 280


def test_events_lower_kpis():
    ev = lambda kind, args: c.Event(event_id="e", kind=kind, day=61, args=args, announced_day=61, source="scripted")  # noqa: E731
    _, base, _ = _run(StubTwin(), {}, days=[61])
    _, short, _ = _run(StubTwin(), {}, days=[61], events_by_day={61: [ev("labor_shortage", {"pool": "pm", "pct": 20, "days": 2})]})
    assert short[0].kpis.on_time_pct == pytest.approx(base[0].kpis.on_time_pct - 6)
    assert short[0].kpis.lph == pytest.approx(base[0].kpis.lph - 10)


def test_c2_fires_with_decision_attribution():
    lv = {d: [reslot(280, f"d{d}")] for d in range(61, 64)}
    _, results, cons = _run(StubTwin(), lv, days=range(61, 65))
    assert results[1].kpis.mispick_rate_by_customer["C001"] > 0.8
    c2 = [e for e in cons if e.kind == "customer_escalation"]
    assert len(c2) == 1 and c2[0].caused_by.startswith("dec:") and c2[0].day == 64
    c6 = [e for e in cons if e.kind == "director_escalation"]
    # day 61 is clean (95.5 >= 95); the dip pushes 62 and 63 below, so C6 fires on 63
    assert c6 and c6[0].caused_by == c.kpi_node_id("r1", 63, "on_time_pct")


def test_protecting_key_customer_prevents_c2():
    lv = {d: [reslot(280, f"d{d}")] for d in range(61, 64)}
    lv[61].append(c.Protect(target="customer", ids=["C001"], days=10, decision_id="p"))
    _, _, cons = _run(StubTwin(), lv, days=range(61, 65))
    assert not [e for e in cons if e.kind == "customer_escalation"]


def test_c3_overtime_absenteeism():
    lv = {d: [c.Overtime(shift="day", pool="pm", hours=8, decision_id=f"ot{d}")] for d in range(61, 64)}
    _, _, cons = _run(StubTwin(), lv, days=range(61, 64))
    [a] = [e for e in cons if e.kind == "absenteeism"]
    assert a.day == 64 and a.caused_by == "dec:ot63" and a.args["pool"] == "pm"


def test_whatif_uses_forecast_only(monkeypatch):
    twin = StubTwin()
    s = twin.initial_state(SC)
    monkeypatch.setattr(twin, "actual_demand", lambda day: (_ for _ in ()).throw(AssertionError("actuals")))
    w = twin.whatif(s, [c.CloseAisle(aisle=3, days=2, decision_id="x"), reslot(200, "x")], 3, [])
    assert len(w.kpis_by_day) == 3
    assert w.kpis_total.moves_done == sum(k.moves_done for k in w.kpis_by_day) == 200
    assert w.kpis_total.on_time_pct == pytest.approx(sum(k.on_time_pct for k in w.kpis_by_day) / 3)
    assert w.kpis_total.reslot_progress_pct == w.kpis_by_day[-1].reslot_progress_pct
    assert w.expected_near_misses == pytest.approx(0.2)  # 2 closed aisle-days
    assert twin.view(s).closed_aisles == []  # state untouched
