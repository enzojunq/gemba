import pytest

import contracts as c
from engine.run import run
from engine.store import RunStore
from engine.stub_twin import StubTwin
from engine.verifier import check_day, check_final, template
from eval.baselines import StaticRules
from org.scoring import aggregate_run, load_profile, score_kpis


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMBA_SNAPSHOT_DIR", str(tmp_path / "snaps"))
    s = RunStore(f"sqlite:///{tmp_path / 'runs.db'}")
    s.create_schema()
    return s


def test_check_day_ops_and_derived():
    checks = template(load_profile("customer_first"))
    res = {r.check_name: r for r in check_day(checks, c.Kpis(on_time_pct=90, moves_done=250),
                                                {"expected_near_misses": 0.2})}
    assert res["daily.on_time_pct"].passed is False and res["daily.on_time_pct"].value == 90
    assert res["daily.moves_done"].passed is False
    assert res["daily.jam_hours"].passed is True
    assert res["daily.expected_near_misses"].passed is True
    assert all(r.scope == "daily" for r in res.values())
    fin = {r.check_name: r.passed for r in check_final(checks, c.Kpis(on_time_pct=97, reslot_progress_pct=95))}
    assert fin["final.reslot_progress_pct"] is True and fin["final.score"] is None


def test_failing_day_writes_check_row(store):
    rid = run("reslot_default", "customer_first", StaticRules(), StubTwin(), store=store)
    rows = store.table("checks", rid)
    daily = [r for r in rows if r["scope"] == "daily"]
    assert len(daily) == 30 * 7
    d63 = {r["check_name"]: r for r in daily if r["day"] == 63}
    assert d63["daily.on_time_pct"]["passed"] is False  # labor shortage day
    assert d63["daily.expected_near_misses"]["passed"] is None  # no what-if from a baseline
    final = {r["check_name"]: r for r in rows if r["scope"] == "final"}
    assert len(final) == 5 and final["final.score"]["passed"] is None and final["final.lph_lift_pct"]["value"] is None


def test_final_derived_with_reference(store):
    ref = run("reslot_default", "customer_first", StaticRules(), StubTwin(), store=store)
    ref_kpis = aggregate_run([c.Kpis.model_validate(r["kpis"]) for r in store.day_results(ref)])
    rid = run("reslot_default", "customer_first", StaticRules(), StubTwin(), store=store,
              ref_kpis=ref_kpis, do_nothing_kpis=ref_kpis)
    final = {r["check_name"]: r for r in store.table("checks", rid) if r["scope"] == "final"}
    # reslot_progress_pct is scored against REF = 0 (org/scoring.py), so a run scored against an
    # identical REF is 0.10 * progress/100, not 0
    expected = score_kpis(ref_kpis, ref_kpis, load_profile("customer_first")).total
    assert final["final.score"]["value"] == pytest.approx(expected) and final["final.score"]["passed"] is True
    assert final["final.lph_lift_pct"]["value"] == pytest.approx(0.0)
    assert final["final.lph_lift_pct"]["passed"] is False
