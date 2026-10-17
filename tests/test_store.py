import pytest

import contracts as c
from engine.store import RunStore


@pytest.fixture
def store(tmp_path):
    s = RunStore(f"sqlite:///{tmp_path / 'runs.db'}")
    s.create_schema()
    return s


def _day(store, run_id, day, *, meeting_id=None, events=(), consequences=(), hourly=None, constraints=()):
    dec = c.Decision(decision_id=f"{run_id}-d{day}", day=day, decided_by="static", rationale="r",
                     levers=[c.ReslotTonight(n_moves=200, decision_id=f"{run_id}-d{day}")],
                     constraints=list(constraints), meeting_id=meeting_id)
    res = c.DayResult(day=day, kpis=c.Kpis(on_time_pct=96.0), hourly=hourly or {})
    store.write_day(run_id, day, dec, [], res, predicted=c.Kpis(on_time_pct=95.0), events=list(events),
                    new_consequences=list(consequences),
                    checks=[{"check_name": "on_time", "scope": "daily", "passed": True, "value": 96.0, "threshold": 95.0}],
                    drift_flags=[], snapshot_path=f"snapshots/{run_id}/{day}.pkl")
    return dec


def _evt(eid, day, kind="rack_damage", args=None, source="scripted", caused_by=None):
    return c.Event(event_id=eid, kind=kind, day=day, args=args or {"aisle": 7, "days": 3},
                   announced_day=day, source=source, caused_by=caused_by)


def test_schema_and_run_lifecycle(store):
    run_id = store.create_run("backtest", "reslot_default", "customer_first", "v0", 1)
    assert run_id.startswith("bt-") and len(run_id) == 11
    store.finish_run(run_id, "done", 0.25)
    run = store.get_run(run_id)
    assert run["status"] == "done" and run["llm_usd"] == 0.25 and run["finished_at"]
    assert [r["run_id"] for r in store.list_runs()] == [run_id]
    for t in ("runs", "day_results", "events_log", "constraints", "agreements", "evidence", "meetings",
              "utterances", "options", "decisions", "hires", "checks", "drift_flags", "cascade_edges",
              "llm_calls", "playbook_rules", "eval_results", "inbox"):
        assert store.table(t) == [] or t == "runs"


def test_write_day_roundtrip(store):
    run_id = store.create_run("backtest")
    _day(store, run_id, 61, events=[_evt("e1", 61)])
    [dr] = store.day_results(run_id)
    assert dr["kpis"]["on_time_pct"] == 96.0 and dr["predicted"]["on_time_pct"] == 95.0
    assert dr["snapshot_path"].endswith("61.pkl")
    [d] = store.decisions(run_id)
    assert d["levers"][0]["kind"] == "reslot_tonight" and d["levers"][0]["decision_id"] == f"{run_id}-d61"
    assert store.events(run_id)[0]["args"] == {"aisle": 7, "days": 3}
    assert len(store.table("checks", run_id)) == 1


def test_write_day_is_atomic(store):
    run_id = store.create_run("backtest")
    with pytest.raises(TypeError):  # day_results insert fails after decision + events were written
        _day(store, run_id, 61, events=[_evt("e1", 61)], hourly={"bad": object()})
    for t in ("decisions", "events_log", "day_results", "checks", "cascade_edges"):
        assert store.table(t, run_id) == []


def test_cascade_edges_written_automatically(store):
    run_id = store.create_run("backtest")
    store.write_meeting(run_id, "m61", 61, "standup", ["chair"], ["rack"], caused_by=["evt:e1"])
    con = _evt("k1", 61, kind="key_customer_week", args={"customer": "KEY1", "days": 5}, source="inbox")
    dec = _day(store, run_id, 61, meeting_id="m61", events=[_evt("e1", 61)], constraints=[con],
               consequences=[_evt("c2-x", 62, kind="customer_escalation", args={"customer": "KEY1"},
                                  source="consequence", caused_by=f"dec:{run_id}-d61")])
    edges = {(e["from_id"], e["to_id"]) for e in store.table("cascade_edges", run_id)}
    assert ("evt:e1", "mtg:m61") in edges
    assert ("mtg:m61", f"dec:{dec.decision_id}") in edges
    assert (f"dec:{dec.decision_id}", "con:k1") in edges
    assert (f"dec:{dec.decision_id}", "evt:c2-x") in edges
    assert ("evt:e1", "evt:e1") not in edges  # scripted root event has no edge
    chain = store.cascade(run_id, "evt:c2-x")
    assert {e["to_id"] for e in chain} >= {"evt:c2-x", f"dec:{dec.decision_id}", "mtg:m61"}
    kpi = c.kpi_node_id(run_id, 62, "on_time_pct")
    store.write_event(run_id, _evt("c6-x", 63, kind="director_escalation", args={"metric": "on_time_pct"},
                                   source="consequence", caused_by=kpi), day=62)
    assert any(e["from_id"] == kpi and e["to_id"] == "evt:c6-x" for e in store.table("cascade_edges", run_id))


def test_copy_run_prefix(store):
    parent = store.create_run("backtest")
    for d in (61, 62, 63):
        store.write_meeting(parent, f"m{d}", d, "standup", ["chair"], [], caused_by=None)
        store.write_utterance(parent, f"m{d}", 1, "chair", "decision", "go")
        _day(store, parent, d, meeting_id=f"m{d}",
             consequences=[_evt(f"c6-{d}", d + 1, kind="director_escalation", args={"metric": "on_time_pct"},
                                source="consequence", caused_by=c.kpi_node_id(parent, d, "on_time_pct"))])
    child = store.create_run("fork", parent_run_id=parent, fork_day=63)
    store.copy_run_prefix(parent, child, 63)
    assert [r["day"] for r in store.day_results(child)] == [61, 62]
    assert [r["day"] for r in store.decisions(child)] == [61, 62]
    assert {m["meeting_id"] for m in store.table("utterances", child)} == {"m61", "m62"}
    edges = store.table("cascade_edges", child)
    assert edges and all(e["day"] < 63 for e in edges)
    assert all(not e["from_id"].startswith(f"kpi:{parent}:") for e in edges)
    assert any(e["from_id"].startswith(f"kpi:{child}:") for e in edges)
    assert len(store.day_results(parent)) == 3


def test_inbox_push_pop(store):
    run_id = store.create_run("backtest")
    store.inbox_push(run_id, "rack damage aisle 7")
    store.inbox_push(run_id, "later", day_hint=70)
    assert store.inbox_pop(run_id, 62) == ["rack damage aisle 7"]
    assert store.inbox_pop(run_id, 63) == []
    assert store.inbox_pop(run_id, 70) == ["later"]
    assert {r["consumed_day"] for r in store.table("inbox", run_id)} == {62, 70}


def test_write_llm_call_accepts_client_dict(store):
    run_id = store.create_run("backtest")
    store.write_llm_call({
        "call_id": "abc", "run_id": run_id, "role": "chair", "model": "m", "prompt_hash": "h",
        "request": {"tier": "big", "messages": [], "mode": "json_schema"}, "response": {"text": "{}"},
        "tokens_in": 10, "tokens_out": 5, "cached_tokens": 0, "latency_ms": 12.5, "usd": 0.001,
        "from_cache": False, "fallback_used": False, "kind": "llm_call", "unknown_key": 1}, day=61)
    [row] = store.table("llm_calls", run_id)
    assert row["mode"] == "json_schema" and row["kind"] == "llm_call" and row["day"] == 61
    assert row["request"]["tier"] == "big"
