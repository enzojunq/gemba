"""Eval harness (spec 15) and report/gate (spec 13.2). No LLM: baselines on StubTwin."""

import subprocess
import sys
from pathlib import Path

import pytest

import contracts as c
from engine import events as ev
from engine.store import RunStore
from engine.stub_twin import StubTwin
from eval.report import by_scenario, gate_summary, render, table
from eval.run_eval import METRIC_KEYS, EvalRow, evaluate, parse_contender

ROOT = Path(__file__).resolve().parent.parent
TRAIN = ["train_1", "train_2", "train_3"]
TEST = ["test_1", "test_2", "test_3"]


def _store(tmp_path) -> RunStore:
    s = RunStore(f"sqlite:///{(tmp_path / 'runs.db').as_posix()}")
    s.create_schema()
    return s


def test_scenarios_load_and_split():
    scs = {sid: ev.load_scenario(sid) for sid in TRAIN + TEST}
    for sid, sc in scs.items():
        assert sc.id == sid and sc.days == (61, 70)
        assert 2 <= len(sc.events) <= 3
        assert len({e.day for e in sc.events}) == len(sc.events)
        assert set(sc.perturbation) == {"family_scale_pct", "seasonal_group_seed", "surge_shift_days"}
        assert abs(sc.perturbation["family_scale_pct"]) <= 10
    assert [scs[s].seed for s in TRAIN] == [11, 12, 13] and [scs[s].seed for s in TEST] == [21, 22, 23]
    train_kinds = {e.kind for s in TRAIN for e in scs[s].events}
    test_kinds = {e.kind for s in TEST for e in scs[s].events}
    assert "forklift_breakdown" in {e.kind for e in scs["test_3"].events}
    assert "forklift_breakdown" not in train_kinds
    assert any(e.kind == "wms_sync_delay" for s in TEST for e in scs[s].events)
    assert test_kinds - train_kinds
    assert ev.load_scenario("reslot_default").days == (61, 90)


def test_parse_contender():
    assert parse_contender("static") == ("static", None)
    assert parse_contender("crew") == ("org", "v0")
    assert parse_contender("crew_v1") == ("org", "v1")
    with pytest.raises(ValueError):
        parse_contender("nope")


def test_evaluate_baselines(tmp_path):
    store = _store(tmp_path)
    rows = evaluate(["reslot_default"], "customer_first", ["do_nothing", "static", "big_bang"], seeds=[1],
                    days=(61, 63), store=store, twin=StubTwin(), snapshot_dir=str(tmp_path / "snap"))
    assert [r.contender for r in rows] == ["do_nothing", "static", "big_bang"]
    by = {r.contender: r for r in rows}
    for r in rows:
        assert list(r.metrics) == list(METRIC_KEYS)
        assert all(r.metrics[k] is not None for k in METRIC_KEYS)
        assert r.seed == 1 and r.scenario_id == "reslot_default"
        assert store.get_run(r.run_id)["status"] == "done"
    assert by["do_nothing"].metrics["score"] == pytest.approx(0.0, abs=1e-9)
    assert by["do_nothing"].metrics["lph_lift_pct"] == pytest.approx(0.0, abs=1e-9)
    assert by["static"].metrics["score_vs_static"] == pytest.approx(0.0, abs=1e-12)
    assert by["static"].metrics["reslot_progress_pct"] > 0
    assert by["big_bang"].metrics["violations"] >= 1  # 1200 moves/night breaks the moves budget check
    assert all(r.metrics["llm_usd"] == 0 and r.metrics["meetings_held"] == 0 for r in rows)
    stored = store.table("eval_results")
    assert len(stored) == 3
    assert {s["contender"] for s in stored} == {"do_nothing", "static", "big_bang"}
    assert all(set(METRIC_KEYS) <= set(s["metrics"]) for s in stored)


def test_evaluate_runs_refs_even_if_not_requested(tmp_path):
    store = _store(tmp_path)
    rows = evaluate(["train_1"], "customer_first", ["big_bang"], days=(61, 62), store=store, twin=StubTwin(),
                    snapshot_dir=str(tmp_path / "snap"))
    assert [r.contender for r in rows] == ["big_bang"] and rows[0].seed == 11
    assert {r["scenario_id"] for r in store.list_runs("eval")} == {"train_1"}
    assert len(store.list_runs("eval")) == 3  # do_nothing (REF) + static (score_vs_static) + big_bang


def _row(ct, seed, score, viol, scenario="s"):
    m = {k: 0.0 for k in METRIC_KEYS}
    m.update(score=score, violations=viol)
    return EvalRow(scenario_id=scenario, profile_id="customer_first", contender=ct, playbook_version=None,
                   seed=seed, run_id=f"r-{ct}-{seed}", metrics=m)


def test_table_and_gate():
    rows = [_row("do_nothing", 1, 0.0, 2), _row("static", 1, 0.1, 1), _row("crew", 1, 0.3, 3),
            _row("crew", 2, 0.5, 3)]
    t = table(rows)
    for ct in ("do_nothing", "static", "crew"):
        assert f"| {ct} |" in t
    assert "**0.400 ± 0.141**" in t  # crew's mean score is best, with spread over 2 seeds
    assert "**1.0**" in t  # static has the fewest violations: a baseline win is still reported
    assert t.splitlines()[0].count("|") == len(METRIC_KEYS) + 3
    assert list(by_scenario(rows + [_row("static", 1, 0, 0, "t")])) == ["s", "t"]
    assert "### s" in render(rows)

    v0 = [_row("crew", 1, 0.1, 2), _row("crew", 2, 0.2, 2)]
    v1_better = [_row("crew_v1", 1, 0.3, 2), _row("crew_v1", 2, 0.2, 2)]
    v1_unsafe = [_row("crew_v1", 1, 0.5, 3), _row("crew_v1", 2, 0.5, 2)]
    g = gate_summary(v0, v1_better)
    assert g["improved"] is True and g["mean_score_v1"] > g["mean_score_v0"] and g["violations_v1"] == g["violations_v0"]
    assert gate_summary(v0, v1_unsafe)["improved"] is False
    assert gate_summary(v0, v0)["improved"] is False


def test_cli(tmp_path):
    db = (tmp_path / "cli.db").as_posix()
    out = tmp_path / "eval_out.md"
    p = subprocess.run([sys.executable, "-m", "eval.run_eval", "--scenario", "reslot_default", "--profile",
                        "customer_first", "--seeds", "1", "--days", "61-62", "--twin", "stub",
                        "--store", f"sqlite:///{db}", "--out", str(out)],
                       cwd=ROOT, capture_output=True, text=True, timeout=300,
                       env={**__import__("os").environ, "GEMBA_SNAPSHOT_DIR": str(tmp_path / "snap")})
    assert p.returncode == 0, p.stderr
    for ct in ("do_nothing", "big_bang", "static"):
        assert f"| {ct} |" in p.stdout
    assert "| contender |" in p.stdout and out.read_text(encoding="utf-8").strip() == p.stdout.strip()
