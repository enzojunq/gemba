"""Floor twin tests on the `contracts.FloorTwin` seam. Mini fixture for everything; real inputs.db tests skip if absent."""

from __future__ import annotations

import json
import os
import pickle
import time
from pathlib import Path

import numpy as np
import pytest

import contracts as c
from floor import constants as K
from floor.twin import Twin, aggregate_kpis
from scenarios.loader import load_scenario, scenario_events, validate_scenario
from tests.fixtures_floor import KEY_CUSTOMER, build_mini_inputs

SC = c.Scenario(id="mini", seed=3, days=(61, 70))
REAL_DB = Path(__file__).resolve().parent.parent / "inputs.db"


@pytest.fixture(scope="session")
def mini_url(tmp_path_factory) -> str:
    p = tmp_path_factory.mktemp("floor") / "mini_inputs.db"
    return build_mini_inputs(p)


@pytest.fixture(scope="session")
def twin(mini_url) -> Twin:
    return Twin(mini_url)


@pytest.fixture(scope="session")
def real_twin() -> Twin:
    if not REAL_DB.exists():
        pytest.skip("inputs.db not generated yet")
    return Twin(f"sqlite:///{REAL_DB}")


def reslot(n, did, **kw):
    return c.ReslotTonight(n_moves=n, decision_id=did, **kw)


def run(twin, levers_by_day, days=range(61, 64), events_by_day=None, run_id="r1", scenario=SC):
    s = twin.initial_state(scenario)
    results, cons = [], []
    for d in days:
        s, _ = twin.apply_levers(s, levers_by_day.get(d, []), d)
        s, r = twin.step_day(s, d, twin.actual_demand(d), (events_by_day or {}).get(d, []))
        results.append(r)
        cons += twin.consequences(s, r, run_id)
    return s, results, cons


# ----------------------------------------------------------------------------- protocol / determinism
def test_protocol_and_view(twin):
    assert isinstance(twin, c.FloorTwin)
    s = twin.initial_state(SC)
    v = twin.view(s)
    assert v.day == 61 and v.move_queue == v.moves_target_total > 0 and v.moves_done_total == 0
    assert v.labor_available["day"]["pm"] == 12 and v.labor_available["night"]["night_move"] == 4
    assert set(v.overtime_days_last4) == set(c.Pool.__args__)
    assert v.learning_dip_skus == 0 and v.temps_active == 0


def test_determinism_and_pickle(twin):
    lv = {61: [reslot(150, "d61")], 62: [reslot(100, "d62"), c.Overtime(shift="day", pool="forklift", hours=2, decision_id="d62")]}
    s1, r1, c1 = run(twin, lv)
    s2, r2, c2 = run(twin, lv)
    assert [r.model_dump_json() for r in r1] == [r.model_dump_json() for r in r2]
    assert [e.model_dump() for e in c1] == [e.model_dump() for e in c2]
    s3 = pickle.loads(pickle.dumps(s1))
    assert twin.view(s3) == twin.view(s1)
    assert np.array_equal(s3.sku_loc, s1.sku_loc)


def test_copy_on_write(twin):
    s0 = twin.initial_state(SC)
    s1, _ = twin.apply_levers(s0, [c.CloseAisle(aisle=7, days=3, decision_id="d")], 61)
    assert twin.view(s0).closed_aisles == [] and twin.view(s1).closed_aisles == [7]
    s2, r = twin.step_day(s1, 61, twin.actual_demand(61), [])
    assert s1.day == 61 and s2.day == 62
    assert twin.view(s1).moves_done_total == 0


# ----------------------------------------------------------------------------- levers
def test_levers_visible_in_view_and_rejections(twin):
    s0 = twin.initial_state(SC)
    levers = [
        c.CloseAisle(aisle=7, days=3, decision_id="d"),
        c.Protect(target="skus", ids=["S0001", "S0002"], days=2, decision_id="d"),
        c.Protect(target="zone", ids=["2"], days=2, decision_id="d"),
        reslot(50, "d"),
        c.HireTemps(count=3, start_day=61, days=2, decision_id="d"),
        c.CloseAisle(aisle=99, days=1, decision_id="d"),
        reslot(5, "d", include_zones=[9]),
        c.LaborAllocate(shift="day", pool="pm", headcount=99, decision_id="d"),
        c.HireTemps(count=9, start_day=61, days=1, decision_id="d"),
        c.Protect(target="skus", ids=["NOPE"], days=1, decision_id="d"),
    ]
    s, rej = twin.apply_levers(s0, levers, 61)
    v = twin.view(s)
    assert v.closed_aisles == [7]
    assert {p.target for p in v.protected} == {"skus", "zone"} and all(p.decision_id == "d" for p in v.protected)
    assert v.temps_active == 3
    assert sum(1 for m in s.moves if m.approved) > 0 and all(m.decision_id == "d" for m in s.moves if m.approved)
    assert len(rej) == 5
    assert "99" in rej[0].reason and "zone" in rej[1].reason and "headcount" in rej[2].reason
    assert "temp" in rej[3].reason and "sku" in rej[4].reason
    # protected SKUs are never queued for tonight
    protected_idx = {twin.inputs.sku_index["S0001"], twin.inputs.sku_index["S0002"]}
    assert not any(m.approved and m.sku in protected_idx for m in s.moves)


def test_reslot_executes_at_night_and_tags_decision(twin):
    s, res, _ = run(twin, {61: [reslot(120, "d61")]}, days=[61])
    assert 0 < res[0].kpis.moves_done <= 120
    assert twin.view(s).moves_done_total == res[0].kpis.moves_done
    assert twin.view(s).learning_dip_skus > 0
    assert all(dec == "d61" for dec in s.dip_decision.values())
    night = [h for h in res[0].hourly["hours"] if h["phase"] == "night"]
    assert sum(h["moves"]["done"] for h in night) == res[0].kpis.moves_done
    assert night[0]["moves"]["sample"] and {"sku", "from", "to"} <= set(night[0]["moves"]["sample"][0])


def test_pause_reslot_blocks_tonight(twin):
    _, res, _ = run(twin, {61: [c.PauseReslot(days=2, decision_id="p"), reslot(100, "d")]}, days=[61])
    assert res[0].kpis.moves_done == 0


# ----------------------------------------------------------------------------- forecast / what-if
def test_whatif_never_touches_actuals(twin, monkeypatch):
    s = twin.initial_state(SC)
    s, _ = twin.step_day(s, 61, twin.actual_demand(61), [])

    def boom(day):
        raise AssertionError("whatif called actual_demand")

    monkeypatch.setattr(twin, "actual_demand", boom)
    w = twin.whatif(s, [reslot(100, "w")], 3, [])
    assert w.days == 3 and len(w.kpis_by_day) == 3
    assert w.kpis_total == aggregate_kpis(w.kpis_by_day)
    assert w.kpis_by_day[0].lines > 0
    assert s.day == 62  # what-if does not mutate the state it was given


def test_forecast_reads_only_past_days(twin):
    s = twin.initial_state(SC)
    inp = twin.inputs
    seen: list[int] = []
    original = inp.day_slices

    class Spy(dict):
        def __getitem__(self, k):
            seen.append(k)
            return original[k]

        def __contains__(self, k):
            return k in original

    inp.day_slices = Spy(original)
    try:
        fc = twin.forecast_demand(s, 61, 3, [])
    finally:
        inp.day_slices = original
    assert all(d < 61 for d in seen) and seen
    assert [f.day for f in fc] == [61, 62, 63]
    assert set(fc[0].orders.columns) >= {"order_id", "release_hour", "customer_id", "priority", "cutoff_hour"}
    assert set(fc[0].lines.columns) == {"order_id", "line_no", "sku_id", "qty"}
    f2 = twin.forecast_demand(s, 61, 3, [])
    assert fc[0] == f2[0]  # seeded


def test_forecast_scales_with_announced_surge(twin):
    s = twin.initial_state(SC)
    ev = c.Event(event_id="e", kind="demand_surge", day=62, args={"family": "F001", "factor": 3, "days": 1},
                 announced_day=60, source="scripted")
    base = twin.forecast_demand(s, 61, 2, [])[1]
    surged = twin.forecast_demand(s, 61, 2, [ev])[1]
    assert len(surged.lines) > len(base.lines)


# ----------------------------------------------------------------------------- consequences
def test_c1_dip_raises_mispicks_on_moved_skus(twin):
    _, base, _ = run(twin, {}, days=[61, 62])
    _, moved, _ = run(twin, {61: [reslot(300, "d61")]}, days=[61, 62])
    assert moved[1].kpis.mispick_rate > base[1].kpis.mispick_rate


def test_c2_fires_with_decision_cause_after_aggressive_night(twin):
    lv = {d: [reslot(1000, f"d{d}"), c.HireTemps(count=8, start_day=61, days=4, decision_id=f"d{d}")] for d in (61,)}
    _, results, cons = run(twin, lv, days=range(61, 65))
    key = [r.kpis.mispick_rate_by_customer.get(KEY_CUSTOMER, 0) for r in results]
    assert max(key) > K.C2_KEY_MISPICK_PCT
    c2 = [e for e in cons if e.kind == "customer_escalation"]
    assert len(c2) >= 1 and c2[0].caused_by.startswith("dec:") and c2[0].args["customer"] == KEY_CUSTOMER
    assert c2[0].source == "consequence" and c2[0].day == c2[0].announced_day


def test_c6_fires_with_kpi_cause(twin):
    ev = {d: [c.Event(event_id=f"ls{d}", kind="labor_shortage", day=d, args={"pool": "pm", "pct": 90, "days": 1},
                      announced_day=d, source="scripted")] for d in (61, 62, 63)}
    _, results, cons = run(twin, {}, days=[61, 62, 63], events_by_day=ev)
    assert all(r.kpis.on_time_pct < K.C6_ON_TIME for r in results)
    c6 = [e for e in cons if e.kind == "director_escalation"]
    assert len(c6) == 1 and c6[0].caused_by == c.kpi_node_id("r1", 62, "on_time_pct") and c6[0].day == 63


def test_c3_absenteeism_after_overtime_streak(twin):
    lv = {d: [c.Overtime(shift="day", pool="forklift", hours=2, decision_id=f"ot{d}")] for d in (61, 62, 63)}
    _, results, cons = run(twin, lv, days=[61, 62, 63, 64])
    assert results[0].kpis.overtime_hours > 0
    c3 = [e for e in cons if e.kind == "absenteeism"]
    assert len(c3) == 1 and c3[0].caused_by == "dec:ot63" and c3[0].args["pool"] == "forklift"


def test_c5_safety_review_from_near_miss(twin):
    s = twin.initial_state(SC)
    s.near_misses.append((61, 4, None))
    s.kpi_history.append({"day": 61, "on_time": 99, "key_mispick": 0, "stockout_pct": 0, "staging_0600": 0, "dip_decision": None})
    r = c.DayResult(day=61, kpis=c.Kpis(near_misses=1))
    ev = twin.consequences(s, r, "r1")
    assert [e.kind for e in ev] == ["safety_review"] and ev[0].caused_by == c.kpi_node_id("r1", 61, "near_misses")
    s.near_misses.append((63, 4, None))
    ev2 = twin.consequences(s, c.DayResult(day=63, kpis=c.Kpis()), "r1")
    assert any(e.kind == "aisle_closure" and e.args["aisle"] == 4 for e in ev2)


def test_events_bite(twin):
    base = run(twin, {}, days=[61])[1][0].kpis
    ev = c.Event(event_id="e", kind="rack_damage", day=61, args={"aisle": 12, "days": 3}, announced_day=61, source="scripted")
    s, res, _ = run(twin, {}, days=[61], events_by_day={61: [ev]})
    assert twin.view(s).closed_aisles == [12]
    assert res[0].hourly["hours"][0]["frozen_aisles"] == [12]
    short = c.Event(event_id="e2", kind="labor_shortage", day=61, args={"pool": "forklift", "pct": 50, "days": 1},
                    announced_day=61, source="scripted")
    res2 = run(twin, {}, days=[61], events_by_day={61: [short]})[1][0].kpis
    assert res2.lph <= base.lph or res2.on_time_pct <= base.on_time_pct


# ----------------------------------------------------------------------------- hourly payload
def test_hourly_payload_conforms(twin):
    _, res, _ = run(twin, {61: [reslot(50, "d")]}, days=[61])
    h = res[0].hourly
    json.dumps(h)
    assert h["version"] == 1 and h["day"] == 61 and len(h["hours"]) == 24
    assert [x["hour"] for x in h["hours"]] == list(range(6, 24)) + list(range(0, 6))
    keys = {"hour", "phase", "pools", "pm_zones", "aisles", "aisle_people", "safety_risk", "frozen_aisles",
            "consolidator_open", "jam", "trucks", "staging_occupancy", "stockouts", "mispicks", "near_miss_aisle", "moves"}
    for x in h["hours"]:
        assert keys <= set(x)
        assert set(x["pools"]) == set(c.Pool.__args__)
        assert all({"available", "busy", "queue", "served"} == set(v) for v in x["pools"].values())
        assert len(x["pm_zones"]) == 6 and len(x["aisles"]) == 28 and len(x["aisle_people"]) == 28 and len(x["safety_risk"]) == 28
        assert set(x["trucks"]) == {"loaded", "late", "next_cutoff"}
        assert x["phase"] in ("day", "swing", "night")
    assert len(json.dumps(h)) < 60_000


def test_scenario_loader_and_validation():
    sc = load_scenario("reslot_default")
    assert sc.days == (61, 90) and sc.seed == 1 and len(sc.events) == 8
    evs = scenario_events(sc)
    assert all(e.announced_day == e.day - c.ANNOUNCE_LEAD_DAYS[e.kind] for e in evs)
    bad = {"id": "x", "seed": 1, "days": [61, 70], "events": [
        {"kind": "rack_damage", "day": 65, "args": {"aisle": 99, "days": 1}},
        {"kind": "pm_zone_down", "day": 95, "args": {"zone": 9, "hours": 1}},
        {"kind": "bogus", "day": 62}, {"kind": "labor_shortage", "day": 62, "args": {"pool": "nope", "pct": 1, "days": 1}}]}
    errs = validate_scenario(bad)
    assert len(errs) >= 5
    for n in ("train_1", "train_2", "train_3", "test_1", "test_2", "test_3"):
        s = load_scenario(n)
        assert s.days[1] - s.days[0] == 9
    assert any(e.kind == "forklift_breakdown" for e in load_scenario("test_3").events)


# ----------------------------------------------------------------------------- real inputs.db
def test_real_timings(real_twin):
    sc = load_scenario("reslot_default")
    s = real_twin.initial_state(sc)
    s, _ = real_twin.apply_levers(s, [reslot(200, "d")], 61)
    t0 = time.perf_counter()
    s, r = real_twin.step_day(s, 61, real_twin.actual_demand(61), [])
    dt = time.perf_counter() - t0
    assert dt < 0.3, f"step_day took {dt:.3f}s"
    t0 = time.perf_counter()
    real_twin.whatif(s, [reslot(200, "w")], 3, [])
    dt = time.perf_counter() - t0
    assert dt < 1.0, f"3-day whatif took {dt:.3f}s"
    assert len(json.dumps(r.hourly)) < 60_000


def test_real_30_day_loop_with_baselines(real_twin):
    from eval.baselines import DoNothing, StaticRules

    sc = load_scenario("reslot_default")
    calendar = scenario_events(sc)
    for policy in (StaticRules("rs"), DoNothing("rd")):
        s = real_twin.initial_state(sc)
        pending: list[c.Event] = []
        for day in sc.day_range:
            events = [e for e in calendar if e.day == day] + [e for e in pending if e.day == day]
            view = real_twin.view(s)
            decision = policy.decide(day, view, c.visible(events, day), real_twin, s)
            s, _ = real_twin.apply_levers(s, decision.levers, day)
            s, r = real_twin.step_day(s, day, real_twin.actual_demand(day), c.active(events, day))
            pending += real_twin.consequences(s, r, policy.run_id)
            assert r.kpis.lines > 0
        if policy.name == "static":
            assert real_twin.view(s).reslot_progress_pct > 80
