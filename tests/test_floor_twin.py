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
    assert twin.view(s).frozen_aisles == [12] and twin.view(s).closed_aisles == []  # damaged: open, no moves
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


# ----------------------------------------------------------------------------- perturbation (spec 13.1)
def test_perturbation_changes_kpis_and_empty_is_identity(twin):
    base = c.Scenario(id="p0", seed=3, days=(61, 70), events=[c.ScenarioEvent(kind="demand_surge", day=62, args={"family": "F001", "factor": 2, "days": 2})])
    pert = base.model_copy(update={"id": "p1", "perturbation": {"family_scale_pct": 10, "seasonal_group_seed": 5, "surge_shift_days": 1}})
    _, r0, _ = run(twin, {}, days=[61], scenario=base)
    _, r0b, _ = run(twin, {}, days=[61], scenario=base)
    _, r1, _ = run(twin, {}, days=[61], scenario=pert)
    assert r0[0].model_dump_json() == r0b[0].model_dump_json()
    assert r1[0].kpis != r0[0].kpis
    # a second perturbed run is identical (deterministic from seed + perturbation)
    _, r1b, _ = run(twin, {}, days=[61], scenario=pert)
    assert r1[0].model_dump_json() == r1b[0].model_dump_json()
    # surge shift: with shift 1 the surge bites on day 63 instead of 62
    s = twin.initial_state(pert)
    assert s.surge_shift == 1 and any(e.kind == "demand_surge" and e.start_day == 63 for e in s.effects)
    ev = c.Event(event_id="x", kind="demand_surge", day=62, args={"family": "F001", "factor": 2, "days": 2}, announced_day=60, source="scripted")
    s, r62 = twin.step_day(s, 62, twin.actual_demand(62), [ev])
    s, r63 = twin.step_day(s, 63, twin.actual_demand(63), [])
    raw62, raw63 = len(twin.actual_demand(62).lines), len(twin.actual_demand(63).lines)
    assert r62.kpis.lines == raw62 and r63.kpis.lines > raw63


def test_canonical_scenarios_load_with_perturbation(twin):
    for n in ("train_1", "train_2", "train_3", "test_1", "test_2", "test_3"):
        sc = load_scenario(n)
        s = twin.initial_state(sc)
        assert twin.view(s).day == sc.days[0]


# ----------------------------------------------------------------------------- real-db: moves drive routing, hero cascade
def test_real_executed_moves_drive_routing(real_twin):
    from eval.baselines import StaticRules
    from floor.calibrate import _loop

    sc = load_scenario("reslot_default")
    s0 = real_twin.initial_state(sc)
    loc0 = s0.sku_loc.copy()
    inp = real_twin.inputs
    pm_share = lambda st: float(inp.velocity_hist[inp.loc_kind[np.where(st.sku_loc >= 0, st.sku_loc, 0)] == 0].sum() / inp.velocity_hist.sum())  # noqa: E731
    share0 = pm_share(s0)
    s = s0
    for d in range(61, 71):
        s, _ = real_twin.apply_levers(s, [reslot(200, f"d{d}")], d)
        s, r = real_twin.step_day(s, d, real_twin.actual_demand(d), [])
        if d == 61:
            assert int(s.dip_mask(62).sum()) > 0  # moved SKUs enter the learning dip
    moved = np.flatnonzero(s.sku_loc != loc0)
    assert len(moved) > 300 and s.moves_done_total >= 1800  # spec 7.1: ~2,400 legs, 12 nights at 200
    assert pm_share(s) > share0 + 0.1  # routing follows the executed slotting
    assert all(s.sku_loc[m.sku] == m.to_loc for m in s.moves if m.legs_done >= m.legs and m.legs > 0)
    assert int(s.dip_mask(80).sum()) == 0  # dip timers expire
    # executed layout vs oracle (target applied instantly) agree on PM load within 10% at equal progress
    rows = _loop(real_twin, sc, StaticRules("rs"), 25)
    orc = _loop(real_twin, sc, StaticRules("or"), 25, oracle=True)
    tail_exec = np.mean([r["pm_peak_zone_load"] for r in rows[-3:]])
    tail_orc = np.mean([r["pm_peak_zone_load"] for r in orc[-3:]])
    assert rows[-1]["reslot_progress_pct"] > 90
    assert abs(tail_exec - tail_orc) / tail_orc < 0.10


def test_real_hero_cascade_under_big_bang(real_twin):
    from eval.baselines import NaiveBigBang

    sc = load_scenario("reslot_default")
    fired = []
    for seed in (1, 2, 3):
        policy = NaiveBigBang(f"bb{seed}")
        s = real_twin.initial_state(sc.model_copy(update={"seed": seed}))
        pending: list[c.Event] = []
        for d in range(61, 66):
            dec = policy.decide(d, real_twin.view(s), [], real_twin, s)
            s, _ = real_twin.apply_levers(s, dec.levers, d)
            s, r = real_twin.step_day(s, d, real_twin.actual_demand(d), [])
            pending += real_twin.consequences(s, r, policy.run_id)
        c2 = [e for e in pending if e.kind == "customer_escalation"]
        if c2:
            fired.append((seed, c2[0].caused_by))
    assert fired and all(cb.startswith("dec:") for _, cb in fired), fired


# ----------------------------------------------------------------------------- scenario overrides
def test_overrides_headcounts_and_legs_budget(twin):
    base = c.Scenario(id="o0", seed=3, days=(61, 70))
    short = base.model_copy(update={"id": "o1", "overrides": {"headcounts": {"day": {"forklift": 10}}}})
    s_base = twin.initial_state(base)
    s_short = twin.initial_state(short)
    assert twin.view(s_short).labor_available["day"]["forklift"] == 10
    assert twin.view(s_short).labor_available["swing"]["forklift"] == twin.view(s_base).labor_available["swing"]["forklift"]
    _, r0, _ = run(twin, {}, days=[61], scenario=base)
    _, r0b, _ = run(twin, {}, days=[61], scenario=base.model_copy(update={"overrides": {}}))
    _, r1, _ = run(twin, {}, days=[61], scenario=short)
    assert r0[0].model_dump_json() == r0b[0].model_dump_json()
    assert r1[0].kpis.labor_cost_usd < r0[0].kpis.labor_cost_usd
    # the mini fixture (281 lines/day) never queues, so the on-time effect is asserted on the real db below
    capped = base.model_copy(update={"id": "o2", "overrides": {"budgets": {"legs_per_night": 50}}})
    _, rc, _ = run(twin, {61: [reslot(200, "d")]}, days=[61], scenario=capped)
    assert 0 < rc[0].kpis.moves_done <= 50
    errs = validate_scenario({"id": "x", "seed": 1, "days": [61, 70], "overrides": {
        "headcounts": {"dawn": {"forklift": 3}, "day": {"robots": 1, "pm": -2}}, "budgets": {"legs_per_night": "many", "x": 1}}})
    assert len(errs) == 5 and any("unknown shift 'dawn'" in e for e in errs) and any("unknown pool 'robots'" in e for e in errs)


def test_real_override_forklift_10_lowers_on_time(real_twin):
    sc = load_scenario("reslot_default")
    short = sc.model_copy(update={"overrides": {"headcounts": {"day": {"forklift": 10}, "swing": {"forklift": 10}}}})
    _, r0, _ = run(real_twin, {}, days=[61], scenario=sc)
    _, r1, _ = run(real_twin, {}, days=[61], scenario=short)
    assert real_twin.view(real_twin.initial_state(short)).labor_available["day"]["forklift"] == 10
    assert r1[0].kpis.on_time_pct < r0[0].kpis.on_time_pct


def test_progress_reaches_100_and_stall_is_visible(twin):
    s = twin.initial_state(SC)
    for d in range(61, 70):
        s, _ = twin.apply_levers(s, [reslot(500, f"d{d}")], d)
        s, r = twin.step_day(s, d, twin.actual_demand(d), [])
    v = twin.view(s)
    assert v.move_queue == 0 and v.reslot_progress_pct == 100.0 and r.kpis.reslot_progress_pct == 100.0
    # every SKU protected: the plan cannot advance and the day result says why
    s = twin.initial_state(SC)
    all_skus = twin.inputs.skus["sku_id"].tolist()
    s, _ = twin.apply_levers(s, [c.Protect(target="skus", ids=all_skus, days=5, decision_id="p"), reslot(200, "d")], 61)
    s, r = twin.step_day(s, 61, twin.actual_demand(61), [])
    assert r.kpis.moves_done == 0 and twin.view(s).move_queue > 0
    assert any("none are selectable" in v for v in r.violations)


def test_real_whatif_safety_matches_actual_night(real_twin):
    import math

    sc = load_scenario("reslot_default")
    s0 = real_twin.initial_state(sc)
    cal = scenario_events(sc)
    for n in (200, 280):
        lv = [reslot(n, "w")]
        w = real_twin.whatif(s0, lv, 3, c.visible(cal, 61))
        s, _ = real_twin.apply_levers(s0, lv, 61)
        _, r = real_twin.step_day(s, 61, real_twin.actual_demand(61), [])
        assert abs(w.kpis_by_day[0].safety_risk_max - r.kpis.safety_risk_max) < 0.15
        assert w.kpis_by_day[0].safety_risk_max < 1.0 and w.safety_risk_max < 1.0
        assert w.kpis_by_day[0].moves_done == r.kpis.moves_done == n
        for v in (w.expected_near_misses, w.kpis_total.near_misses, w.pm_peak_zone_load, w.safety_risk_max):
            assert isinstance(v, float) and math.isfinite(v)
        assert all(isinstance(k.near_misses, float) and math.isfinite(k.near_misses) for k in w.kpis_by_day)
    assert s0.day == 61  # what-if never mutates its input state


def test_real_night_safety_monotone_and_below_limit(real_twin):
    """Night movers are people, not legs. On days 61-65 of reslot_default, for 100/200/280 legs: the night
    risk stays below 1.0 in actual days and what-ifs, no aisle-hour holds more movers than are on shift,
    and total night presence (mover-hours across aisles) is non-decreasing in legs. The per-hour maximum
    itself is not monotone: a bigger budget spreads the same third legs over more hours."""
    sc = load_scenario("reslot_default")
    cal = scenario_events(sc)
    s = real_twin.initial_state(sc)
    for d in range(61, 66):
        night_max, presence = {}, {}
        for n in (100, 200, 280):
            s_n, _ = real_twin.apply_levers(s, [reslot(n, f"d{d}")], d)
            _, r = real_twin.step_day(s_n, d, real_twin.actual_demand(d), [])
            night = [h for h in r.hourly["hours"] if h["phase"] == "night"]
            movers = max(h["pools"]["night_move"]["available"] for h in night)
            night_max[n] = max(max(h["safety_risk"]) for h in night)
            presence[n] = sum(sum(h["safety_risk"]) * 3.0 for h in night)
            assert night_max[n] < 1.0 and night_max[n] * 3.0 <= movers + 1e-6, (d, n, night_max[n], movers)
            assert r.kpis.safety_risk_max < 1.0, (d, n, r.kpis.safety_risk_max)
            w = real_twin.whatif(s, [reslot(n, "w")], 1, c.visible(cal, d))
            assert w.safety_risk_max < 1.0, (d, n, w.safety_risk_max)
        assert presence[100] <= presence[200] + 1e-6 <= presence[280] + 1e-6, (d, presence)
        s, _ = real_twin.apply_levers(s, [reslot(200, f"d{d}")], d)
        s, _ = real_twin.step_day(s, d, real_twin.actual_demand(d), [])


# ----------------------------------------------------------------------------- QA items 2, 4, 5, 6
def test_staging_clearance_frees_staging(twin):
    """After a night that could not finish its swaps, staging_clearance lowers staging occupancy."""
    base = c.Scenario(id="sc", seed=3, days=(61, 70))
    s = twin.initial_state(base)
    s, _ = twin.apply_levers(s, [reslot(3000, "d"), c.HireTemps(count=8, start_day=61, days=2, decision_id="d")], 61)
    s, r61 = twin.step_day(s, 61, twin.actual_demand(61), [])
    in_flight = sum(1 for m in s.moves if 0 < m.legs_done < m.legs)
    if in_flight == 0:
        pytest.skip("mini plan finished in one night; nothing to clear")
    assert twin.view(s).staging_occupancy > 0
    s_plain, r_plain = twin.step_day(s, 62, twin.actual_demand(62), [])
    s_clear, _ = twin.apply_levers(s, [c.StagingClearance(n_movers=4, decision_id="c")], 62)
    s_clear, r_clear = twin.step_day(s_clear, 62, twin.actual_demand(62), [])
    assert r_clear.kpis.staging_occupancy_max <= r_plain.kpis.staging_occupancy_max
    w0 = twin.whatif(s, [], 1, [])
    w1 = twin.whatif(s, [c.StagingClearance(n_movers=4, decision_id="c")], 1, [])
    assert w1.kpis_total.staging_occupancy_max <= w0.kpis_total.staging_occupancy_max


def test_rack_damage_open_aisle_risk_and_close_aisle(twin):
    ev = c.Event(event_id="rd", kind="rack_damage", day=61, args={"aisle": 12, "days": 3}, announced_day=61, source="scripted")
    s0 = twin.initial_state(SC)
    _, r_base = twin.step_day(s0, 61, twin.actual_demand(61), [])
    s_dmg, r_dmg = twin.step_day(s0, 61, twin.actual_demand(61), [ev])
    risk = lambda r: max(h["safety_risk"][11] for h in r.hourly["hours"])  # noqa: E731
    assert risk(r_dmg) > risk(r_base)
    assert twin.view(s_dmg).frozen_aisles == [12] and twin.view(s_dmg).closed_aisles == []
    s_cl, _ = twin.apply_levers(s0, [c.CloseAisle(aisle=12, days=3, decision_id="d")], 61)
    s_cl, r_cl = twin.step_day(s_cl, 61, twin.actual_demand(61), [ev])
    assert risk(r_cl) == 0.0 and twin.view(s_cl).closed_aisles == [12]
    assert r_cl.kpis.safety_risk_max <= r_dmg.kpis.safety_risk_max


def test_leadership_visit_freezes_aisles_for_moves(twin):
    s0 = twin.initial_state(SC)
    aisles = sorted({int(twin.inputs.loc_aisle[m.to_loc]) for m in s0.moves if twin.inputs.loc_aisle[m.to_loc] > 0} |
                    {int(twin.inputs.loc_aisle[s0.sku_loc[m.sku]]) for m in s0.moves if twin.inputs.loc_aisle[s0.sku_loc[m.sku]] > 0})
    ev = c.Event(event_id="lv", kind="leadership_visit", day=61, args={"no_moves_in_aisles": aisles}, announced_day=56, source="scripted")
    s, _ = twin.apply_levers(s0, [reslot(200, "d")], 61)
    s, r = twin.step_day(s, 61, twin.actual_demand(61), [ev])
    assert r.hourly["hours"][0]["frozen_aisles"] == aisles
    touched = {int(twin.inputs.loc_aisle[m.to_loc]) for m in s.moves if m.legs_done > 0 and twin.inputs.loc_aisle[m.to_loc] > 0}
    assert not (touched & set(aisles))
    s2, _ = twin.apply_levers(s0, [reslot(200, "d")], 61)
    _, r2 = twin.step_day(s2, 61, twin.actual_demand(61), [])
    assert r.kpis.moves_done < r2.kpis.moves_done


def test_whatif_raises_on_invalid_levers(twin):
    s = twin.initial_state(SC)
    with pytest.raises(ValueError) as e:
        twin.whatif(s, [reslot(50, "w", include_zones=[9]), c.CloseAisle(aisle=99, days=1, decision_id="w")], 2, [])
    assert "zone" in str(e.value) and "99" in str(e.value)


def test_real_staging_clearance_after_big_bang_night(real_twin):
    sc = load_scenario("reslot_default")
    s = real_twin.initial_state(sc)
    s, _ = real_twin.apply_levers(s, [reslot(1200, "d"), c.HireTemps(count=8, start_day=61, days=2, decision_id="d")], 61)
    s, r61 = real_twin.step_day(s, 61, real_twin.actual_demand(61), [])
    assert r61.kpis.staging_occupancy_max > 90 and real_twin.view(s).staging_occupancy > 20
    s_plain, r_plain = real_twin.step_day(s, 62, real_twin.actual_demand(62), [])
    s_clear, _ = real_twin.apply_levers(s, [c.StagingClearance(n_movers=4, decision_id="c")], 62)
    s_clear, r_clear = real_twin.step_day(s_clear, 62, real_twin.actual_demand(62), [])
    assert r_clear.kpis.staging_occupancy_max < r_plain.kpis.staging_occupancy_max  # 100% -> ~25% night peak
    assert r_clear.kpis.moves_done >= r_plain.kpis.moves_done - 20
    w0 = real_twin.whatif(s, [], 1, [])
    w1 = real_twin.whatif(s, [c.StagingClearance(n_movers=4, decision_id="c")], 1, [])
    assert w1.kpis_total.staging_occupancy_max < w0.kpis_total.staging_occupancy_max
