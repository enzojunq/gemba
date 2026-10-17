import contracts as c
from engine.drift import DriftDetector


def _ctx(**kw):
    return {"moves_done": 100, "explained_stall": False, "reslot_n_moves": 200} | kw


def test_plan_churn_after_three_changes():
    d = DriftDetector()
    assert d.check(61, _ctx(reslot_n_moves=200)) == []
    assert d.check(62, _ctx(reslot_n_moves=150)) == []
    assert d.check(63, _ctx(reslot_n_moves=250)) == []
    [f] = d.check(64, _ctx(reslot_n_moves=100))
    assert f.kind == "plan_churn" and f.response == "replan"


def test_stable_plan_no_flags():
    d = DriftDetector()
    assert all(d.check(day, _ctx()) == [] for day in range(61, 70))


def test_prediction_error_two_days_then_recurring():
    d = DriftDetector()
    pred, bad, ok = c.Kpis(on_time_pct=95, lph=100), c.Kpis(on_time_pct=95, lph=80), c.Kpis(on_time_pct=95, lph=99)
    assert d.check(61, _ctx(actual=bad, predicted=pred)) == []
    [f] = d.check(62, _ctx(actual=bad, predicted=pred))
    assert f.kind == "prediction_error" and f.response == "recalibrate"
    assert d.check(63, _ctx(actual=ok, predicted=pred)) == []
    assert d.check(64, _ctx(actual=bad, predicted=pred)) == []
    [f2] = d.check(65, _ctx(actual=bad, predicted=pred))
    assert f2.kind == "prediction_error" and f2.response == "shrink_or_stop"


def test_prediction_error_needs_consecutive_days():
    d = DriftDetector()
    pred, bad, ok = c.Kpis(lph=100), c.Kpis(lph=70), c.Kpis(lph=100)
    for day, a in zip(range(61, 67), [bad, ok, bad, ok, bad, ok]):
        assert d.check(day, _ctx(actual=a, predicted=pred)) == []


def test_stall_and_explained():
    d = DriftDetector()
    assert d.check(61, _ctx(moves_done=0, explained_stall=True)) == []
    assert d.check(62, _ctx(moves_done=0)) == []
    assert d.check(63, _ctx(moves_done=0)) == []
    [f] = d.check(64, _ctx(moves_done=0))
    assert f.kind == "stall"


def test_repeat_whatif_and_overrun():
    d = DriftDetector()
    assert d.check(61, _ctx(spec_hashes=["h1", "h1"])) == []
    [f] = d.check(62, _ctx(spec_hashes=["h1", "h2"]))
    assert f.kind == "repeat_whatif"
    [o] = d.check(63, _ctx(llm_usd=2.0, budget_usd=1.5))
    assert o.kind == "overrun"
    assert d.check(64, _ctx(llm_usd=3.0, budget_usd=1.5)) == []  # once per run


def test_counters_roundtrip():
    d = DriftDetector()
    d.check(61, _ctx(reslot_n_moves=1))
    d.check(62, _ctx(reslot_n_moves=2))
    assert DriftDetector.from_counters(d.counters()) == d


def test_engine_recalibrates_on_prediction_error(tmp_path, monkeypatch):
    from engine.run import run
    from engine.store import RunStore
    from engine.stub_twin import StubTwin
    from eval.baselines import StaticRules

    monkeypatch.setenv("GEMBA_SNAPSHOT_DIR", str(tmp_path / "snaps"))
    store = RunStore(f"sqlite:///{tmp_path / 'runs.db'}")

    class Predicting(StaticRules):
        def decide(self, day, view, events, twin, state):
            d = super().decide(day, view, events, twin, state)
            return _WithPrediction(**d.model_dump(), predicted=c.Kpis(on_time_pct=50, lph=50))

    class _WithPrediction(c.Decision):
        predicted: c.Kpis

    calls = []

    class Twin(StubTwin):
        def recalibrate(self, state, actual, predicted):
            calls.append((len(actual), len(predicted)))
            return state

    rid = run("reslot_default", "customer_first", Predicting(), Twin(), store=store)
    flags = store.drift_flag_rows(rid)
    pe = [f for f in flags if f["kind"] == "prediction_error"]
    assert pe and pe[0]["response"] == "recalibrate" and pe[1]["response"] == "shrink_or_stop"
    assert calls and calls[0] == (2, 2)
    assert store.day_results(rid)[0]["predicted"]["lph"] == 50
