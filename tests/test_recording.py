"""engine.recording exports a run (and its forks) into a standalone store the console can replay."""
from engine import run as runmod
from engine.recording import export_runs
from engine.store import RunStore
from engine.stub_twin import StubTwin
from eval.baselines import StaticRules
from org.scoring import load_profile
import engine.events as ev


def test_export_run_with_fork(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMBA_SNAPSHOT_DIR", str(tmp_path / "snap"))
    src_url = f"sqlite:///{tmp_path / 'runs.db'}"
    store = RunStore(src_url); store.create_schema()
    sc = ev.load_scenario("reslot_default").model_copy(update={"days": (61, 64)})
    rid = runmod.run(sc, load_profile("customer_first"), StaticRules("x"), StubTwin(), store=store, mode="backtest")
    out = tmp_path / "rec" / "hero.db"
    counts = export_runs(src_url, [rid], str(out), with_forks=True)
    assert counts["day_results"] == 4 and counts["decisions"] == 4 and counts["runs"] == 1
    dst = RunStore(f"sqlite:///{out}")
    assert [r["day"] for r in dst.day_results(rid)] == [61, 62, 63, 64]
    assert dst.get_run(rid)["status"] == "done"
    # re-export is idempotent (rows replaced, not duplicated)
    counts2 = export_runs(src_url, [rid], str(out))
    assert counts2["day_results"] == 4 and len(dst.day_results(rid)) == 4
