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


def test_export_with_learning_pulls_rule_and_gate_runs(tmp_path, monkeypatch):
    import json, sqlite3
    monkeypatch.setenv("GEMBA_SNAPSHOT_DIR", str(tmp_path / "snap"))
    src_url = f"sqlite:///{tmp_path / 'runs.db'}"
    store = RunStore(src_url); store.create_schema()
    sc = ev.load_scenario("reslot_default").model_copy(update={"days": (61, 62)})
    prof = load_profile("customer_first")
    hero = runmod.run(sc, prof, StaticRules("h"), StubTwin(), store=store, mode="backtest")
    parent = runmod.run(sc, prof, StaticRules("p"), StubTwin(), store=store, mode="eval")
    fork = runmod.run(sc, prof, StaticRules("f"), StubTwin(), store=store, mode="fork")
    con = sqlite3.connect(str(tmp_path / "runs.db"))
    con.execute("INSERT INTO playbook_rules (version, rule_id, role, tags, trigger, advice, evidence, score_delta, status) VALUES (?,?,?,?,?,?,?,?,?)",
                ("v1", "r1", "safety", json.dumps(["rack_damage"]), "t", "a", json.dumps({"run_id": parent, "fork_run_id": fork, "score_delta": 0.02}), 0.02, "active"))
    con.execute("INSERT INTO eval_results (eval_id, scenario_id, profile_id, contender, playbook_version, seed, metrics) VALUES (?,?,?,?,?,?,?)",
                ("e1", "test_1", "customer_first", "gate", "v1", 1, json.dumps({"run_ids_from": [parent], "run_ids_to": [fork]})))
    con.commit(); con.close()
    out = tmp_path / "rec" / "hero.db"
    counts = export_runs(src_url, [hero], str(out), with_learning=True)
    assert counts["runs"] == 3 and counts["playbook_rules"] == 1 and counts["eval_results"] == 1
    dst = RunStore(f"sqlite:///{out}")
    assert {r["run_id"] for r in dst.list_runs()} == {hero, parent, fork}
