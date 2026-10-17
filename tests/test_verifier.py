import pytest

from contracts import DERIVED_METRICS, KPI_NAMES, DoneCheck, Kpis
from engine.verifier import check_day, check_final, template
from org.scoring import load_profile


@pytest.mark.parametrize("pid", ["customer_first", "efficiency_first"])
def test_template_valid(pid):
    checks = template(load_profile(pid))
    assert checks
    for c in checks:
        assert isinstance(c, DoneCheck)
        assert c.min_threshold <= c.threshold <= c.max_threshold
        assert c.metric in KPI_NAMES or c.metric in DERIVED_METRICS
        assert c.check_name == f"{c.scope}.{c.metric}"
    names = {c.check_name for c in checks}
    assert len(names) == len(checks)


def test_template_contents():
    by_name = {c.check_name: c for c in template(load_profile("customer_first"))}
    assert {"daily.expected_near_misses", "final.lph_lift_pct", "final.score"} <= set(by_name)
    ot = by_name["daily.on_time_pct"]
    assert (ot.op, ot.threshold, ot.min_threshold, ot.max_threshold) == (">=", 95.0, 93.0, 97.0)
    mp = by_name["daily.mispick_rate"]
    assert (mp.threshold, mp.min_threshold, mp.max_threshold) == (0.5, 0.25, 0.75)
    mv = by_name["daily.moves_done"]
    assert (mv.threshold, mv.min_threshold, mv.max_threshold) == (200, 150, 280)  # spec 7.1 budget, 7.3 crew max
    assert by_name["daily.pm_peak_zone_load"].threshold == pytest.approx(1.10)
    fo = by_name["final.on_time_pct"]
    assert (fo.threshold, fo.min_threshold, fo.max_threshold) == (97.0, 95.0, 98.0)
    ll = by_name["final.lph_lift_pct"]
    assert (ll.threshold, ll.min_threshold, ll.max_threshold) == (8, 4, 12)
    rp = by_name["final.reslot_progress_pct"]
    assert (rp.threshold, rp.min_threshold, rp.max_threshold) == (90, 80, 100)
    assert by_name["final.score"].op == ">=" and by_name["final.score"].threshold == 0
    scopes = {c.scope for c in by_name.values()}
    assert scopes == {"daily", "final"}


def test_runner_stubs_return_empty():
    checks = template(load_profile("customer_first"))
    assert check_day(checks, Kpis()) == []
    assert check_final(checks, Kpis()) == []
