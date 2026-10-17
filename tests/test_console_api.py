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


# --------------------------------------------------------------------------- scenarios (META_HARNESS 4)

VALID_YAML = "id: judge_test\nseed: 7\ndays: [61, 70]\nevents:\n  - {kind: rack_damage, day: 66, args: {aisle: 7, days: 3}}\n"


@pytest.fixture
def scenario_cleanup():
    from console.server import SCENARIO_DIR
    yield
    for sid in ("judge_test", "judge_test2"):
        p = SCENARIO_DIR / f"{sid}.yaml"
        if p.exists():
            p.unlink()


def test_scenarios_list_and_meta(client):
    j = client.get("/scenarios").json()
    ids = {s["id"] for s in j["scenarios"]}
    assert {"reslot_default", "train_1", "test_1"} <= ids
    default = next(s for s in j["scenarios"] if s["id"] == "reslot_default")
    assert default["protected"] and default["n_events"] == 8 and "rack damage" in default["summary"]
    m = client.get("/scenarios/meta").json()
    assert "rack_damage" in m["kinds"] and "aisle_closure" not in m["kinds"]
    assert m["args"]["rack_damage"] == ["aisle", "days"] and "forklift" in m["pools"]
    assert m["headcounts"]["day"]["pm"] == 12 and "reslot_default" in m["protected"]
    one = client.get("/scenarios/reslot_default").json()
    assert one["scenario"]["id"] == "reslot_default" and one["protected"]
    assert client.get("/scenarios/nope").status_code == 404
    assert client.get("/scenarios/..%2Fx").status_code in (404, 422)


def test_scenarios_save_valid(client, scenario_cleanup):
    r = client.post("/scenarios", json={"yaml": VALID_YAML})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] and j["id"] == "judge_test" and j["path"].endswith("judge_test.yaml") and j["errors"] == []
    back = client.get("/scenarios/judge_test").json()["scenario"]
    assert back["events"][0]["kind"] == "rack_damage" and back["days"] == [61, 70]
    assert any(s["id"] == "judge_test" for s in client.get("/scenarios").json()["scenarios"])


def test_scenarios_save_rejects_bad_input(client, scenario_cleanup):
    bad_kind = client.post("/scenarios", json={"yaml": VALID_YAML.replace("rack_damage", "earthquake")}).json()
    assert not bad_kind["ok"] and any("unknown kind" in e for e in bad_kind["errors"])
    consequence = client.post("/scenarios", json={"yaml": VALID_YAML.replace("rack_damage", "aisle_closure")}).json()
    assert not consequence["ok"] and any("consequence" in e for e in consequence["errors"])
    bad_day = client.post("/scenarios", json={"yaml": VALID_YAML.replace("day: 66", "day: 95")}).json()
    assert not bad_day["ok"] and any("outside" in e for e in bad_day["errors"])
    protected = client.post("/scenarios", json={"yaml": VALID_YAML.replace("judge_test", "reslot_default")}).json()
    assert not protected["ok"] and any("protected" in e for e in protected["errors"])
    traversal = client.post("/scenarios", json={"yaml": VALID_YAML.replace("judge_test", "../judge_test")}).json()
    assert not traversal["ok"] and any("slug" in e for e in traversal["errors"])
    broken = client.post("/scenarios", json={"yaml": "id: [unclosed"}).json()
    assert not broken["ok"] and broken["errors"][0].startswith("yaml:")
    from console.server import SCENARIO_DIR
    assert not (SCENARIO_DIR / "reslot_default.yaml").read_text().startswith("id: judge")  # untouched


def test_scenarios_phrases_and_describe(client):
    j = client.post("/scenarios/phrases", json={"events": [{"kind": "rack_damage", "args": {"aisle": 7}},
                                                         {"kind": "labor_shortage", "args": {"pool": "forklift", "pct": 20, "days": 2}}]}).json()
    assert j["phrases"][0] == "rack damage closed aisle 7" and "forklift" in j["phrases"][1]
    r = client.post("/scenarios/describe", json={"text": "peak week with night absences"})
    assert r.status_code in (200, 503)
    if r.status_code == 503:
        assert "scenario designer" in r.json()["detail"]["error"]
    else:
        assert set(r.json()) >= {"ok", "scenario", "errors"}


def test_bearer_token_guards_writes(client, monkeypatch, scenario_cleanup):
    monkeypatch.setenv("GEMBA_API_TOKEN", "s3cret")
    assert client.post("/scenarios", json={"yaml": VALID_YAML}).status_code == 401
    assert client.post("/runs", json={"scenario": "reslot_default", "profile": "customer_first"}).status_code == 401
    ok = client.post("/scenarios", json={"yaml": VALID_YAML}, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200 and ok.json()["ok"]
    assert client.get("/scenarios").status_code == 200  # reads stay open


def test_token_not_needed_from_this_machine(monkeypatch, scenario_cleanup):
    """The operator's browser on localhost keeps working with the token on; a wrong token still fails."""
    from fastapi.testclient import TestClient
    from console.server import app
    monkeypatch.setenv("GEMBA_API_TOKEN", "s3cret")
    local = TestClient(app, client=("127.0.0.1", 50000))
    assert local.post("/scenarios", json={"yaml": VALID_YAML}).status_code == 200
    assert local.post("/scenarios", json={"yaml": VALID_YAML}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_describe_uses_designer_without_network(client, monkeypatch):
    """The endpoint's shape over B's describe(), with the LLM replaced by a canned result."""
    import importlib
    sd = importlib.import_module("org.scenario_designer")
    canned = {"ok": True, "yaml": "id: peak_week\nseed: 1\ndays: [61, 70]\nevents: []\n",
              "scenario": {"id": "peak_week", "seed": 1, "days": [61, 70], "events": []}, "errors": [],
              "dropped": ["event 2 (pm_zone_down): zone 9 not in 1-6"], "summary": "Peak week rehearsal.",
              "confidence": 0.9, "draft": None, "calls": 1}
    monkeypatch.setattr(sd, "describe", lambda text, llm=None, **kw: canned)
    j = client.post("/scenarios/describe", json={"text": "peak week"}).json()
    assert j["ok"] and j["scenario"]["id"] == "peak_week" and j["summary"] == "Peak week rehearsal."
    assert j["confidence"] == 0.9 and j["dropped"] == canned["dropped"] and j["yaml"].startswith("id: peak_week")
    bad = dict(canned, ok=False, scenario={}, errors=["not a scenario request (confidence 0.2)"], confidence=0.2)
    monkeypatch.setattr(sd, "describe", lambda text, llm=None, **kw: bad)
    j = client.post("/scenarios/describe", json={"text": "what time is lunch"}).json()
    assert not j["ok"] and "not a scenario" in j["errors"][0] and j["scenario"] == {}


def test_learning_empty_store_has_shape(client):
    j = client.get("/learning").json()
    assert set(j) == {"rules", "scenarios", "gate"}
