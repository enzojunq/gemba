"""Console API tests: build the fixture into a temp store and hit every endpoint (spec 14)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from console import fixture
from console.server import app

HERO, FORK = fixture.HERO_RUN, fixture.FORK_RUN


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module):
    db = tmp_path_factory.mktemp("console") / "demo_runs.db"
    url = fixture.build(str(db))
    monkeypatch_module.setenv("RUN_STORE_URL", url)
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def monkeypatch_module():
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    yield mp
    mp.undo()


def test_index_and_static(client):
    r = client.get("/")
    assert r.status_code == 200 and "<canvas" in r.text and 'id="room"' in r.text
    for f in ("app.js", "floor.js", "meeting.js", "timeline.js", "cascade.js", "compare.js", "styles.css"):
        assert client.get(f"/static/{f}").status_code == 200, f


def test_runs_list_and_header(client):
    runs = client.get("/runs").json()
    assert runs["recorded"] is False
    ids = {r["run_id"] for r in runs["runs"]}
    assert {HERO, FORK} <= ids
    hdr = client.get(f"/runs/{HERO}").json()
    assert hdr["scenario"] == "reslot_default" and hdr["profile"] == "customer_first" and hdr["playbook"] == "v0"
    assert hdr["status"] == "done" and hdr["first_day"] == 61 and hdr["last_day"] == 90 and len(hdr["days"]) == 30
    assert hdr["thresholds"]["on_time_pct"] == 95.0 and hdr["thresholds"]["mispick_rate"] == 0.5
    assert any(d["has_meeting"] for d in hdr["days"]) and any(not d["has_meeting"] for d in hdr["days"])
    kinds = {e["kind"] for e in hdr["events"]}
    assert {"rack_damage", "demand_surge", "customer_escalation"} <= kinds
    assert all("label" in e for e in hdr["events"])
    fk = client.get(f"/runs/{FORK}").json()
    assert fk["parent_run_id"] == HERO and fk["fork_day"] == 69 and fk["fork_decision"]["label"]
    assert client.get("/runs/nope").status_code == 404


def test_day_bundle_meeting_day(client):
    d = client.get(f"/runs/{HERO}/day/66").json()
    assert d["day"] == 66 and d["kpis"]["on_time_pct"] > 0
    h = d["hourly"]
    assert h["version"] == 1 and len(h["hours"]) == 24 and h["hours"][0]["hour"] == 6 and h["hours"][-1]["hour"] == 5
    first = h["hours"][0]
    assert len(first["aisles"]) == 28 and len(first["pm_zones"]) == 6 and 7 in first["frozen_aisles"]
    assert any(x["phase"] == "night" and x["moves"]["done"] > 0 and x["moves"]["sample"] for x in h["hours"])
    m = d["meeting"]
    assert m["kind"] == "standup" and "Sam" in m["attendees"]
    assert any(u["kind"] == "veto" and u["role"] == "Sam" for u in m["utterances"])
    assert any(o["vetoed_by"] == "Sam" and o["feasible"] is False for o in m["options"])
    assert all("lever_labels" in o and "label" in o for o in m["options"])
    assert d["decision"]["option_id"].endswith("-B") and d["decision"]["lever_labels"]
    assert "Sam" in d["active_hires"]
    assert any(c["kind"] == "aisle_closure" for c in d["constraints"])
    assert {c["check_name"] for c in d["checks"]} >= {"daily.on_time_pct", "daily.mispick_rate"}


def test_day_bundle_lock_and_quiet_day(client):
    d = client.get(f"/runs/{HERO}/day/67").json()
    assert d["meeting"]["kind"] == "escalation"
    assert any(u["kind"] == "lock" and u["role"] == "Ellen" for u in d["meeting"]["utterances"])
    assert any(c["label"].startswith("Key customer week") for c in d["constraints"])
    q = client.get(f"/runs/{HERO}/day/62").json()
    assert q["meeting"]["kind"] == "quiet" and len(q["meeting"]["utterances"]) == 1  # spec 10.5: chair's one line
    assert q["meeting"]["utterances"][0]["role"] == "Megan" and q["decision"]["levers"][0]["kind"] == "reslot_tonight"
    hdr = client.get(f"/runs/{HERO}").json()
    row = next(d for d in hdr["days"] if d["day"] == 62)
    assert row["has_meeting"] is False and row["meeting_kind"] == "quiet"
    drift = client.get(f"/runs/{HERO}/day/70").json()["drift"]
    assert drift and drift[0]["kind"] == "prediction_error" and drift[0]["response"] == "recalibrate"
    assert client.get(f"/runs/{HERO}/day/99").status_code == 404


def test_stream_replays_meeting_then_hours(client):
    events = []
    with client.stream("GET", f"/runs/{HERO}/stream?speed=500&from_day=66&max_days=1") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        cur = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                cur = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and cur:
                events.append((cur, json.loads(line[5:])))
                cur = None
    names = [e for e, _ in events]
    assert names[0] == "hello" and names[1] == "day" and "meeting_open" in names and "meeting_close" in names
    assert names.count("meeting") == 7 and names.count("hour") == 24 and names[-1] == "done"
    assert names.index("meeting_close") < names.index("hour")
    hours = [d for e, d in events if e == "hour"]
    assert hours[0]["h"]["hour"] == 6 and hours[-1]["h"]["hour"] == 5 and hours[0]["n"] == 24
    assert "hourly" not in events[1][1]  # hourly travels in hour ticks, not in the day event


def test_inbox_round_trip(client):
    r = client.post(f"/runs/{HERO}/inbox", json={"text": "Forklift 3 is down this afternoon", "day_hint": 70})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] and any(m["text"] == "Forklift 3 is down this afternoon" for m in j["pending"])
    hdr = client.get(f"/runs/{HERO}").json()
    assert any(m["msg_id"] == j["msg_id"] and m["consumed_day"] is None for m in hdr["inbox"])
    assert client.post(f"/runs/{HERO}/inbox", json={"text": "   "}).status_code == 422


def test_compare_deltas_and_first_divergence(client):
    j = client.get(f"/compare?a={HERO}&b={FORK}").json()
    assert j["a"]["run_id"] == HERO and j["b"]["run_id"] == FORK
    assert j["first_divergence"]["day"] == 69
    assert j["first_divergence"]["b"]["levers"][0].startswith("protect zone")
    rows = {r["day"]: r for r in j["days"]}
    assert rows[65]["differs"] is False and rows[69]["differs"] is True
    assert rows[65]["delta"]["on_time_pct"] == 0  # copied prefix is identical
    assert rows[75]["delta"]["on_time_pct"] > 0 and rows[75]["delta"]["reslot_progress_pct"] < 0
    assert j["totals"]["on_time_pct"]["delta"] > 0
    assert client.get(f"/compare?a={HERO}&b=nope").status_code == 404


def test_cascade_hero_chain(client):
    j = client.get(f"/cascade/{HERO}/evt:c2-67-C001").json()
    ids = {n["id"] for n in j["nodes"]}
    assert {f"dec:{HERO}-d64", "evt:c2-67-C001", f"mtg:m-{HERO}-67", f"dec:{HERO}-d67",
            f"con:con-{HERO}-67-key_customer_week"} <= ids
    labels = {n["id"]: n for n in j["nodes"]}
    assert labels[f"dec:{HERO}-d64"]["label"] == "Decision day 64" and "280" in labels[f"dec:{HERO}-d64"]["detail"]
    assert labels["evt:c2-67-C001"]["label"].startswith("Customer escalation")
    assert labels[f"con:con-{HERO}-67-key_customer_week"]["type"] == "con"
    kinds = {(e["from"], e["to"], e["kind"]) for e in j["edges"]}
    assert (f"dec:{HERO}-d64", "evt:c2-67-C001", "consequence") in kinds
    assert ("evt:c2-67-C001", f"mtg:m-{HERO}-67", "meeting") in kinds
    assert (f"mtg:m-{HERO}-67", f"dec:{HERO}-d67", "decision") in kinds
    # KPI node gets a value from day_results
    k = client.get(f"/cascade/{HERO}/kpi:{HERO}:66:safety_risk_max").json()
    kpi = next(n for n in k["nodes"] if n["type"] == "kpi")
    assert kpi["value"] is not None and kpi["day"] == 66
    assert client.get(f"/cascade/{HERO}/evt:unknown").json()["nodes"][0]["id"] == "evt:unknown"


def test_fork_and_start_run_without_engine(client, monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("engine.api") or (name == "engine" and a and a[2] and "api" in a[2]):
            raise ImportError("engine.api absent in this test")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    r = client.post(f"/runs/{HERO}/fork", json={"day": 69, "preset": "protect_pm"})
    assert r.status_code == 503 and "engine.api" in r.json()["detail"]["error"]
    r = client.post("/runs", json={"scenario": "reslot_default", "profile": "customer_first", "playbook": "v0"})
    assert r.status_code == 503
    assert client.post(f"/runs/{HERO}/fork", json={"day": 69, "preset": "nope"}).status_code == 422
    assert client.get("/forks/presets").json()["presets"]["protect_pm"]["levers"][0]["kind"] == "protect"
