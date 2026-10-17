import pytest

from contracts import Kpis, Profile, WhatIf
from org.scoring import (
    AGGREGATION, Feasibility, aggregate_run, feasibility, least_bad, load_profile,
    score_kpis, score_whatif, validate_profile,
)

REF = Kpis(on_time_pct=95.0, mispick_rate=0.6, lph=100.0, reslot_progress_pct=0.0, labor_cost_usd=10_000.0)


@pytest.fixture
def cf() -> Profile:
    return load_profile("customer_first")


def test_identical_is_zero(cf):
    s = score_kpis(REF, REF, cf)
    assert s.total == 0.0
    assert set(s.terms) == set(cf.weights)


def test_on_time_plus_five(cf):
    s = score_kpis(REF.model_copy(update={"on_time_pct": 100.0}), REF, cf)
    assert s.terms["on_time_pct"] == pytest.approx(0.35)
    assert s.total == pytest.approx(0.35)


def test_clip(cf):
    s = score_kpis(REF.model_copy(update={"on_time_pct": 145.0}), REF, cf)
    assert s.terms["on_time_pct"] == pytest.approx(0.35)
    s = score_kpis(REF.model_copy(update={"on_time_pct": 45.0}), REF, cf)
    assert s.terms["on_time_pct"] == pytest.approx(-0.35)


def test_mispick_worse_negative(cf):
    s = score_kpis(REF.model_copy(update={"mispick_rate": 0.85}), REF, cf)
    assert s.terms["mispick_rate"] == pytest.approx(-0.25 * 0.5)


def test_lph_relative(cf):
    s = score_kpis(REF.model_copy(update={"lph": 110.0}), REF, cf)
    assert s.terms["lph"] == pytest.approx(0.20)


def test_progress_absolute(cf):
    for ref_prog in (0.0, 40.0, 80.0):
        ref = REF.model_copy(update={"reslot_progress_pct": ref_prog})
        s = score_kpis(ref.model_copy(update={"reslot_progress_pct": 50.0}), ref, cf)
        assert s.terms["reslot_progress_pct"] == pytest.approx(0.05)


def test_labor_cost_higher(cf):
    s = score_kpis(REF.model_copy(update={"labor_cost_usd": 11_000.0}), REF, cf)
    assert s.terms["labor_cost_usd"] == pytest.approx(-0.10)


def test_relative_zero_ref_no_div_zero(cf):
    zero = Kpis()
    s = score_kpis(Kpis(lph=0.5), zero, cf)
    assert s.terms["lph"] == pytest.approx(0.20 * 0.5)


def test_score_whatif(cf):
    def wi(k):
        return WhatIf(levers=[], days=1, kpis_by_day=[k], kpis_total=k,
                      expected_near_misses=0, safety_risk_max=0, pm_peak_zone_load=0)
    opt = REF.model_copy(update={"on_time_pct": 100.0})
    assert score_whatif(wi(opt), wi(REF), cf).total == pytest.approx(0.35)


def test_bad_weights_rejected():
    with pytest.raises(ValueError):
        validate_profile(Profile(id="bad", weights={"on_time_pct": 0.5, "lph": 0.4}))
    with pytest.raises(ValueError):
        validate_profile(Profile(id="bad", weights={"on_time_pct": 0.5, "nonsense": 0.5}))


def test_both_profiles_load():
    cf = load_profile("customer_first")
    ef = load_profile("efficiency_first")
    assert cf.id == "customer_first" and ef.id == "efficiency_first"
    assert ef.weights["lph"] == 0.35
    assert cf.hard_limits["pm_peak_zone_load"] == 1.10
    assert cf.targets["lph_lift_pct"] == 8


def test_load_by_path():
    from org.scoring import PROFILES_DIR
    assert load_profile(PROFILES_DIR / "efficiency_first.yaml").id == "efficiency_first"


def test_aggregate_run():
    days = [
        Kpis(on_time_pct=90, mispick_rate=0.4, lph=100, reslot_progress_pct=10, labor_cost_usd=1000,
             pm_peak_zone_load=0.9, jam_hours=1, moves_queued=50, mispick_rate_by_customer={"a": 1.0}),
        Kpis(on_time_pct=100, mispick_rate=0.6, lph=120, reslot_progress_pct=30, labor_cost_usd=2000,
             pm_peak_zone_load=1.2, jam_hours=2, moves_queued=20, mispick_rate_by_customer={"a": 3.0, "b": 2.0}),
    ]
    agg = aggregate_run(days)
    assert agg.on_time_pct == 95
    assert agg.mispick_rate == pytest.approx(0.5)
    assert agg.lph == 110
    assert agg.reslot_progress_pct == 30
    assert agg.labor_cost_usd == 3000
    assert agg.pm_peak_zone_load == 1.2
    assert agg.jam_hours == 3 and isinstance(agg.jam_hours, int)
    assert agg.moves_queued == 20
    assert agg.mispick_rate_by_customer == {"a": 2.0, "b": 2.0}
    assert AGGREGATION["reslot_progress_pct"] == "end"


def test_feasibility_pm_load(cf):
    wi = WhatIf(levers=[], days=3, kpis_by_day=[], kpis_total=Kpis(),
                expected_near_misses=0.1, safety_risk_max=0.8, pm_peak_zone_load=1.2)
    f = feasibility(wi, cf)
    assert not f.feasible
    assert set(f.breaches) == {"pm_peak_zone_load"}
    assert f.breaches["pm_peak_zone_load"] == pytest.approx(0.1)
    assert f.total_breach == pytest.approx(0.1)

    ok = feasibility(wi.model_copy(update={"pm_peak_zone_load": 1.10}), cf)
    assert ok.feasible and ok.breaches == {}


def test_feasibility_relative_to_ref(cf):
    def wi(risk, nm=0.1, pm=0.9):
        return WhatIf(levers=[], days=3, kpis_by_day=[], kpis_total=Kpis(),
                      expected_near_misses=nm, safety_risk_max=risk, pm_peak_zone_load=pm)

    ref = wi(1.3)  # do-nothing already breaches safety_risk_max (limit 1.0)
    assert not feasibility(ref, cf).feasible
    assert feasibility(wi(1.3), cf, ref).feasible  # no worse than REF
    assert feasibility(wi(1.1), cf, ref).feasible  # better than REF, still above the limit
    worse = feasibility(wi(1.4), cf, ref)
    assert not worse.feasible and worse.breaches["safety_risk_max"] == pytest.approx(0.1)
    # a limit REF does not breach stays absolute
    pm = feasibility(wi(1.3, pm=1.2), cf, ref)
    assert set(pm.breaches) == {"pm_peak_zone_load"} and pm.breaches["pm_peak_zone_load"] == pytest.approx(0.1)
    # REF under the limit: same as no REF
    assert feasibility(wi(1.05), cf, wi(0.5)).breaches == feasibility(wi(1.05), cf).breaches


def test_least_bad():

    opts = [
        Feasibility(feasible=False, breaches={"pm_peak_zone_load": 0.3}),
        Feasibility(feasible=False, breaches={"safety_risk_max": 0.05, "pm_peak_zone_load": 0.05}),
        Feasibility(feasible=False, breaches={"expected_near_misses": 0.2}),
    ]
    assert least_bad(opts) == 1
