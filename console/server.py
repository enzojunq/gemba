"""Gemba console: FastAPI + SSE over the run store (spec section 14, CONTRACT 7/8).

Reads `runs.db` (RUN_STORE_URL) through `engine.store.RunStore`; writes only `inbox` rows;
starts/forks runs only through `engine.api` (imported lazily; 503 if absent).

    uv run uvicorn console.server:app --port 8000
    RUN_STORE_URL=sqlite:///recordings/hero.db uv run uvicorn console.server:app --port 8000
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

import contracts as c
from engine.store import JSON_COLS, RunStore, md

STATIC = Path(__file__).parent / "static"
KPI_SPARKS = ("on_time_pct", "mispick_rate", "lph", "reslot_progress_pct")
HOUR_ORDER = list(range(6, 24)) + list(range(0, 6))

app = FastAPI(title="Gemba console")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.middleware("http")
async def _no_stale_static(request, call_next):
    """ES modules and CSS are revalidated on every load so edits show up without a hard refresh."""
    resp = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        resp.headers["Cache-Control"] = "no-cache"
    return resp

_store: RunStore | None = None


def store() -> RunStore:
    """One RunStore per process; re-created when RUN_STORE_URL changes (tests)."""
    global _store
    url = os.environ.get("RUN_STORE_URL", "sqlite:///runs.db")
    if _store is None or _store.url != url:
        _store = RunStore(url)
    return _store


def recorded() -> bool:
    """True when the store is a committed recording (demo honesty badge)."""
    url = store().url
    return "recordings/" in url.replace("\\", "/")


def _row(r: Any) -> dict:
    """Like engine.store._row but tolerant: a JSON-named column holding plain text stays text
    (drift_flags.response is a plain string while llm_calls.response is JSON)."""
    out = {}
    for k, v in r._mapping.items():
        if k in JSON_COLS and isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        out[k] = v
    return out


def _q(table: str, **where: Any) -> list[dict]:
    t = md.tables[table]
    stmt = select(t)
    for k, v in where.items():
        stmt = stmt.where(t.c[k] == v)
    with store().engine.connect() as conn:
        return [_row(r) for r in conn.execute(stmt).all()]


def _run_or_404(run_id: str) -> dict:
    run = store().get_run(run_id)
    if not run:
        raise HTTPException(404, f"run {run_id!r} not found")
    return run


# --------------------------------------------------------------------------- labels

ROLE_SHORT = {
    "Megan": "program manager", "Ryan": "ops manager", "Luke": "reslotting engineer", "Paige": "analytics",
    "Dana": "plant director", "Sam": "safety", "Ellen": "quality", "Marcus": "shift lead", "Tom": "replenishment",
}

EVENT_LABEL = {
    "labor_shortage": "Labor shortage", "demand_surge": "Demand surge", "rack_damage": "Rack damage",
    "pm_zone_down": "PM zone down", "leadership_visit": "Leadership visit", "key_customer_week": "Key customer week",
    "new_sku_launch": "New SKU launch", "returns_spike": "Returns spike", "wms_sync_delay": "WMS sync delay",
    "forklift_breakdown": "Forklift breakdown", "customer_escalation": "Customer escalation",
    "absenteeism": "Absenteeism", "staging_congestion": "Staging congestion", "safety_review": "Safety review",
    "aisle_closure": "Aisle closure", "director_escalation": "Director escalation", "replen_alert": "Replen alert",
}


def lever_label(lv: dict) -> str:
    k = lv.get("kind")
    if k == "reslot_tonight":
        z = lv.get("include_zones") or []
        return f"reslot {lv.get('n_moves')} legs" + (f" in zones {','.join(map(str, z))}" if z else "")
    if k == "pause_reslot":
        return f"pause reslot {lv.get('days')} d"
    if k == "labor_allocate":
        return f"{lv.get('pool')} {lv.get('shift')} → {lv.get('headcount')}"
    if k == "overtime":
        return f"overtime {lv.get('hours')} h {lv.get('pool')} {lv.get('shift')}"
    if k == "hire_temps":
        return f"hire {lv.get('count')} temps for {lv.get('days')} d"
    if k == "protect":
        return f"protect {lv.get('target')} {','.join(map(str, lv.get('ids', [])))} {lv.get('days')} d"
    if k == "resize_slots":
        return f"resize {len(lv.get('skus', []))} slots to {lv.get('days_cover')} d cover"
    if k == "wave_priority":
        return f"wave {lv.get('mode')}"
    if k == "close_aisle":
        return f"close aisle {lv.get('aisle')} {lv.get('days')} d"
    if k == "reopen_aisle":
        return f"reopen aisle {lv.get('aisle')}"
    if k == "staging_clearance":
        return f"clear staging with {lv.get('n_movers')} movers"
    return str(k)


ZONE_NAMES = {"zone": "Pick Module zone", "skus": "SKU", "customer": "customer"}


def lever_effect(lv: dict) -> str:
    """Plain-language effect of a lever, for a first-time viewer (what changes on the floor)."""
    k = lv.get("kind")
    if k == "reslot_tonight":
        n = lv.get("n_moves", 0)
        z = lv.get("include_zones") or []
        tone = "aggressive night: " if n >= 260 else ("slow night: " if n <= 120 else "")
        return f"{tone}{n} slot moves tonight" + (f", only Pick Module zones {','.join(map(str, z))}" if z else "")
    if k == "pause_reslot":
        d = lv.get("days", 1)
        return "no slot moves tonight" if d == 1 else f"no slot moves for {d} nights"
    if k == "labor_allocate":
        return f"{lv.get('headcount')} people on {POOL_NAME.get(lv.get('pool'), lv.get('pool'))} for the {lv.get('shift')} shift"
    if k == "overtime":
        return f"{lv.get('hours')} h overtime for {POOL_NAME.get(lv.get('pool'), lv.get('pool'))} on {lv.get('shift')}"
    if k == "hire_temps":
        return f"{lv.get('count')} temps hired for {lv.get('days')} days"
    if k == "protect":
        ids = lv.get("ids", [])
        t = lv.get("target")
        if t == "customer":
            return f"{', '.join(ids)}'s SKUs locked in place for {lv.get('days')} days"
        if t == "zone":
            return f"Pick Module zone{'s' if len(ids) > 1 else ''} {','.join(ids)} frozen for {lv.get('days')} days"
        return f"{len(ids)} SKUs locked in place for {lv.get('days')} days"
    if k == "resize_slots":
        return f"{len(lv.get('skus', []))} fast-moving SKUs get {lv.get('days_cover')} days of stock at the pick face"
    if k == "wave_priority":
        return {"rush_first": "rush orders picked first", "by_cutoff": "orders picked in truck-cutoff order",
                "by_zone": "orders picked zone by zone"}.get(lv.get("mode"), f"wave {lv.get('mode')}")
    if k == "close_aisle":
        return f"aisle {lv.get('aisle')} closed for {lv.get('days')} days"
    if k == "reopen_aisle":
        return f"aisle {lv.get('aisle')} reopened"
    if k == "staging_clearance":
        return f"{lv.get('n_movers')} movers clear the staging buffer tonight"
    return str(k)


POOL_NAME = {"pm": "Pick Module picking", "forklift": "forklift picking", "pallet": "pallet picking",
             "replen": "replenishment", "consolidation": "the consolidator", "loading": "truck loading",
             "night_move": "night moves", "temp": "temps"}


def effects(levers: list | None) -> str:
    return "; ".join(lever_effect(lv) for lv in (levers or [])) or "no change to the plan"


def event_plain(e: dict) -> str:
    """One clause a non-warehouse person gets, e.g. 'rack damage closed aisle 7'."""
    a = e.get("args") or {}
    k = e.get("kind")
    return {
        "labor_shortage": f"{a.get('pct', '')}% of {POOL_NAME.get(a.get('pool'), a.get('pool'))} staff out for {a.get('days', '')} days",
        "demand_surge": f"orders for family {a.get('family') or a.get('customer') or ''} surge ×{a.get('factor', '')} for {a.get('days', '')} days",
        "rack_damage": f"rack damage closed aisle {a.get('aisle', '')}",
        "pm_zone_down": f"Pick Module zone {a.get('zone', '')} down for {a.get('hours', '')} hours",
        "leadership_visit": "leadership walks the floor: no moves in aisles " + "-".join(map(str, (min(a.get('no_moves_in_aisles', [0])), max(a.get('no_moves_in_aisles', [0]))))),
        "key_customer_week": f"key customer {a.get('customer', '')} week: zero tolerance for errors",
        "new_sku_launch": f"{a.get('n', '')} new SKUs launched",
        "returns_spike": f"returns up {a.get('pct', '')}% for {a.get('days', '')} days",
        "wms_sync_delay": f"warehouse system lags {a.get('days', '')} days",
        "forklift_breakdown": f"{a.get('count', '')} forklifts down for {a.get('days', '')} days",
        "customer_escalation": f"customer {a.get('customer', '')} escalated over mis-picks",
        "absenteeism": f"absenteeism up {a.get('pct', '')}% in {POOL_NAME.get(a.get('pool'), a.get('pool'))}",
        "staging_congestion": f"staging buffer at {a.get('occupancy', '')}% chokes nearby aisles",
        "safety_review": f"near-miss in aisle {a.get('aisle', '')}: safety review",
        "aisle_closure": f"aisle {a.get('aisle', '')} forced closed for {a.get('days', '')} days",
        "director_escalation": f"the director was pulled in over {str(a.get('metric', '')).replace('_', ' ')}",
        "replen_alert": f"stock-outs at {a.get('stockout_pct', '')}%: replenishment alert",
    }.get(k, EVENT_LABEL.get(k, k))


def event_label(e: dict) -> str:
    a = e.get("args") or {}
    k = e.get("kind")
    base = EVENT_LABEL.get(k, k)
    detail = ""
    if "aisle" in a:
        detail = f"aisle {a['aisle']}"
    elif "customer" in a:
        detail = a["customer"]
    elif "family" in a:
        detail = f"{a['family']} ×{a.get('factor', '')}"
    elif "pool" in a:
        detail = f"{a['pool']} −{a.get('pct', '')}%"
    elif "zone" in a:
        detail = f"zone {a['zone']} {a.get('hours', '')} h"
    elif "metric" in a:
        detail = a["metric"].replace("_", " ")
    elif "occupancy" in a:
        detail = f"{a['occupancy']}%"
    elif "pct" in a:
        detail = f"+{a['pct']}%"
    elif "no_moves_in_aisles" in a:
        detail = "aisles " + "-".join(map(str, (min(a["no_moves_in_aisles"]), max(a["no_moves_in_aisles"]))))
    return f"{base}: {detail}" if detail else base


# --------------------------------------------------------------------------- run reads

def _run_header(run: dict) -> dict:
    days = [d["day"] for d in store().day_results(run["run_id"])]
    return {
        "run_id": run["run_id"], "scenario": run["scenario_id"], "profile": run["profile_id"],
        "playbook": run["playbook_version"], "status": run["status"], "mode": run["mode"], "seed": run["seed"],
        "parent_run_id": run["parent_run_id"], "fork_day": run["fork_day"], "fork_decision": run["fork_decision"],
        "started_at": run["started_at"], "finished_at": run["finished_at"], "llm_usd": run["llm_usd"],
        "first_day": min(days) if days else None, "last_day": max(days) if days else None, "n_days": len(days),
        "recorded": recorded(),
    }


def _meeting_bundle(run_id: str, day: int) -> dict | None:
    ms = [m for m in _q("meetings", run_id=run_id) if m["day"] == day]
    if not ms:
        return None
    m = ms[0]
    mid = m["meeting_id"]
    utts = sorted(_q("utterances", run_id=run_id, meeting_id=mid), key=lambda u: u["seq"])
    opts = _q("options", run_id=run_id, meeting_id=mid)
    for o in opts:
        o["label"] = o["option_id"].rsplit("-", 1)[-1]
        o["lever_labels"] = [lever_label(lv) for lv in (o["levers"] or [])]
        o["effect"] = effects(o["levers"])
    for u in utts:
        u["title"] = ROLE_SHORT.get(u["role"], "")
        u["option_label"] = u["option_id"].rsplit("-", 1)[-1] if u.get("option_id") else None
    return {**m, "utterances": utts, "options": sorted(opts, key=lambda o: -(o["score"] or 0))}


def _day_bundle(run_id: str, day: int, with_hourly: bool = True) -> dict:
    drs = [d for d in store().day_results(run_id) if d["day"] == day]
    if not drs:
        raise HTTPException(404, f"day {day} not in run {run_id}")
    dr = drs[0]
    decs = [d for d in store().decisions(run_id) if d["day"] == day]
    dec = decs[0] if decs else None
    if dec:
        dec["lever_labels"] = [lever_label(lv) for lv in (dec["levers"] or [])]
        dec["effect"] = effects(dec["levers"])
    events = [dict(e, label=event_label(e), plain=event_plain(e)) for e in store().events(run_id) if e["day"] == day]
    hires = [h for h in _q("hires", run_id=run_id) if h["day"] == day or h.get("released_day") == day]
    active_hires = [h["role"] for h in _q("hires", run_id=run_id)
                    if h["day"] <= day and (h["released_day"] is None or h["released_day"] > day)]
    checks = [k for k in _q("checks", run_id=run_id) if k["day"] == day]
    drift = [f for f in _q("drift_flags", run_id=run_id) if f["day"] == day]
    cons = [dict(k, label=event_label(k)) for k in _q("constraints", run_id=run_id)
            if k["day_start"] <= day <= (k["day_end"] or k["day_start"])]
    return {
        "run_id": run_id, "day": day, "kpis": dr["kpis"], "predicted": dr["predicted"],
        "violations": dr["violations"] or [], "hourly": dr["hourly"] if with_hourly else None,
        "events": events, "meeting": _meeting_bundle(run_id, day), "decision": dec, "hires": hires,
        "active_hires": active_hires, "checks": checks, "drift": drift, "constraints": cons,
    }


# --------------------------------------------------------------------------- endpoints

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/runs")
def list_runs() -> dict:
    return {"recorded": recorded(), "store": store().url, "runs": [_run_header(r) for r in store().list_runs()]}


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = _run_or_404(run_id)
    hdr = _run_header(run)
    days = store().day_results(run_id)
    decs = {d["day"]: d for d in store().decisions(run_id)}
    meetings = {m["day"]: m for m in store().meetings(run_id)}
    checks = _q("checks", run_id=run_id)
    thresholds: dict[str, float] = {}
    for k in checks:
        if k["scope"] == "daily" and k["check_name"].split(".", 1)[1] in KPI_SPARKS:
            thresholds[k["check_name"].split(".", 1)[1]] = k["threshold"]
    for k in checks:
        if k["scope"] == "final" and k["check_name"].split(".", 1)[1] == "reslot_progress_pct":
            thresholds["reslot_progress_pct"] = k["threshold"]
    hdr["thresholds"] = thresholds
    hdr["events"] = [dict(e, label=event_label(e), plain=event_plain(e)) for e in store().events(run_id)]
    hdr["hires"] = _q("hires", run_id=run_id)
    hdr["constraints"] = [dict(k, label=event_label(k)) for k in _q("constraints", run_id=run_id)]
    hdr["agreements"] = _q("agreements", run_id=run_id)
    hdr["evidence"] = [{k: v for k, v in e.items() if k != "spec"} for e in _q("evidence", run_id=run_id)]
    hdr["drift"] = _q("drift_flags", run_id=run_id)
    hdr["inbox"] = _q("inbox", run_id=run_id)
    hdr["days"] = [{
        "day": d["day"], "kpis": d["kpis"], "violations": d["violations"] or [],
        "has_meeting": meetings.get(d["day"], {}).get("kind") in ("standup", "escalation"),
        "meeting_kind": meetings.get(d["day"], {}).get("kind"),
        "decision_id": decs.get(d["day"], {}).get("decision_id"),
        "decision_label": "; ".join(lever_label(lv) for lv in (decs.get(d["day"], {}).get("levers") or [])),
        "decision_effect": effects(decs.get(d["day"], {}).get("levers")),
        "deviates": decs.get(d["day"], {}).get("deviates_from_top_score"),
        "checks_failed": [k["check_name"] for k in checks if k["day"] == d["day"] and not k["passed"]],
    } for d in days]
    return hdr


@app.get("/runs/{run_id}/day/{day}")
def get_day(run_id: str, day: int) -> dict:
    _run_or_404(run_id)
    return dict(_day_bundle(run_id, day), recorded=recorded())


# --------------------------------------------------------------------------- SSE

def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def _stream(run_id: str, speed: float, from_day: int | None, max_days: int | None):
    """Replay existing days at `speed` simulated hours per real second, then tail while running.
    One `hour` event per tick; `meeting` events per utterance before the day's hours."""
    run = _run_or_404(run_id)
    yield _sse("hello", {"run": _run_header(run), "speed": speed})
    hour_dt = 1.0 / max(speed, 0.01)
    utt_dt = min(1.6, 1.6 / math.sqrt(max(speed, 1.0)))
    known = sorted(d["day"] for d in store().day_results(run_id))
    if not known:
        yield _sse("waiting", {"run_id": run_id})
    day = from_day if from_day is not None else (known[0] if known else None)
    sent = 0
    idle = 0.0
    while True:
        known = sorted(d["day"] for d in store().day_results(run_id))
        if day is None and known:
            day = known[0]
        if day is not None and day in known:
            bundle = _day_bundle(run_id, day)
            hourly = bundle.pop("hourly") or {}
            yield _sse("day", bundle)
            m = bundle.get("meeting")
            if m and m.get("kind") != "quiet":  # a quiet day's one line travels in the day event
                yield _sse("meeting_open", {"day": day, "meeting_id": m["meeting_id"], "kind": m["kind"],
                                            "attendees": m["attendees"], "agenda": m["agenda"]})
                for u in m["utterances"]:
                    yield _sse("meeting", {"day": day, **u})
                    await asyncio.sleep(utt_dt)
                yield _sse("meeting_close", {"day": day, "meeting_id": m["meeting_id"], "options": m["options"],
                                             "decision": bundle.get("decision"), "outcome": m.get("outcome")})
                await asyncio.sleep(utt_dt)
            hours = hourly.get("hours") or []
            for i, h in enumerate(hours):
                yield _sse("hour", {"day": day, "i": i, "n": len(hours), "h": h})
                await asyncio.sleep(hour_dt)
            yield _sse("day_end", {"day": day, "kpis": bundle["kpis"], "checks": bundle["checks"]})
            sent += 1
            if max_days is not None and sent >= max_days:
                yield _sse("done", {"day": day, "reason": "max_days"})
                return
            day += 1
            idle = 0.0
            continue
        status = (store().get_run(run_id) or {}).get("status")
        if status in ("running", "queued"):
            yield _sse("waiting", {"run_id": run_id, "day": day, "status": status})
            await asyncio.sleep(1.0)
            idle += 1.0
            continue
        yield _sse("done", {"day": day, "status": status})
        return


@app.get("/runs/{run_id}/stream")
async def stream(run_id: str, speed: float = Query(4.0, gt=0), from_day: int | None = None,
                 max_days: int | None = None) -> StreamingResponse:
    _run_or_404(run_id)
    return StreamingResponse(_stream(run_id, speed, from_day, max_days), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------- writes

class InboxIn(BaseModel):
    text: str
    day_hint: int | None = None


@app.post("/runs/{run_id}/inbox")
def inbox(run_id: str, body: InboxIn) -> dict:
    _run_or_404(run_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "text is empty")
    msg_id = store().inbox_push(run_id, text, body.day_hint)
    return {"ok": True, "msg_id": msg_id, "run_id": run_id, "text": text, "day_hint": body.day_hint,
            "pending": [m for m in _q("inbox", run_id=run_id) if m["consumed_day"] is None]}


PRESETS = {
    "protect_pm": {"label": "Protect the Pick Module all week",
                   "levers": [{"kind": "protect", "target": "zone", "ids": ["1", "2", "3", "4", "5", "6"], "days": 7}]},
    "pause_3": {"label": "Pause the reslot for 3 days", "levers": [{"kind": "pause_reslot", "days": 3}]},
    "temps_6": {"label": "Hire 6 temps for 10 days",
                "levers": [{"kind": "hire_temps", "count": 6, "start_day": 0, "days": 10}]},
}


class ForkIn(BaseModel):
    day: int
    levers: list[dict] = []
    constraints: list[dict] = []
    preset: str | None = None


def _engine_api():
    try:
        from engine import api  # type: ignore
    except Exception as exc:  # engine.api is B's; absent until integration
        raise HTTPException(503, {"error": "engine.api is not available in this checkout",
                                  "detail": str(exc), "hint": "make demo only needs reads; forks need B's engine"})
    return api


@app.get("/forks/presets")
def fork_presets() -> dict:
    return {"presets": PRESETS}


@app.post("/runs/{run_id}/fork")
def fork(run_id: str, body: ForkIn) -> dict:
    _run_or_404(run_id)
    levers = list(body.levers)
    if body.preset:
        if body.preset not in PRESETS:
            raise HTTPException(422, f"unknown preset {body.preset!r}; one of {sorted(PRESETS)}")
        levers = [dict(lv) for lv in PRESETS[body.preset]["levers"]]
        for lv in levers:
            if lv["kind"] == "hire_temps":
                lv["start_day"] = body.day
    api = _engine_api()
    typed = [_lever(lv) for lv in levers]
    cons = [c.Event(**e) for e in body.constraints]
    new_id = api.fork_run(run_id, body.day, typed, cons, store=store())
    return {"ok": True, "run_id": new_id, "parent_run_id": run_id, "day": body.day, "levers": levers}


def _lever(lv: dict):
    from pydantic import TypeAdapter
    return TypeAdapter(c.Lever).validate_python(lv)


class RunIn(BaseModel):
    scenario: str
    profile: str
    playbook: str = "v0"
    policy: str = "org"
    seed: int | None = None


@app.post("/runs")
def start_run(body: RunIn) -> dict:
    api = _engine_api()
    run_id = api.start_run(body.scenario, body.profile, body.playbook, body.policy, body.seed, store=store())
    return {"ok": True, "run_id": run_id}


@app.get("/runs/{run_id}/status")
def run_status(run_id: str) -> dict:
    run = _run_or_404(run_id)
    try:
        api = _engine_api()
        return {"run_id": run_id, "status": api.run_status(run_id, store=store())}
    except HTTPException:
        return {"run_id": run_id, "status": run["status"], "source": "store"}


# --------------------------------------------------------------------------- compare + cascade

@app.get("/compare")
def compare(a: str, b: str) -> dict:
    ra, rb = _run_or_404(a), _run_or_404(b)
    da = {d["day"]: d for d in store().day_results(a)}
    db = {d["day"]: d for d in store().day_results(b)}
    xa = {d["day"]: d for d in store().decisions(a)}
    xb = {d["day"]: d for d in store().decisions(b)}
    days = sorted(set(da) | set(db))
    rows = []
    first_div = None
    for day in days:
        ka, kb = (da.get(day) or {}).get("kpis"), (db.get(day) or {}).get("kpis")
        deltas = {k: (round(kb[k] - ka[k], 3) if ka and kb and isinstance(ka.get(k), (int, float)) and
                      isinstance(kb.get(k), (int, float)) else None) for k in c.KPI_NAMES}
        la = [lever_label(lv) for lv in ((xa.get(day) or {}).get("levers") or [])]
        lb = [lever_label(lv) for lv in ((xb.get(day) or {}).get("levers") or [])]
        ea, eb = effects((xa.get(day) or {}).get("levers")), effects((xb.get(day) or {}).get("levers"))
        differs = (day in xa and day in xb and _strip(xa[day]["levers"]) != _strip(xb[day]["levers"]))
        if differs and first_div is None:
            first_div = {"day": day, "a": {"decision_id": xa[day]["decision_id"], "levers": la, "effect": ea, "rationale": xa[day]["rationale"]},
                         "b": {"decision_id": xb[day]["decision_id"], "levers": lb, "effect": eb, "rationale": xb[day]["rationale"]}}
        rows.append({"day": day, "a": ka, "b": kb, "delta": deltas, "decision_a": la, "decision_b": lb,
                     "differs": differs})
    totals = {}
    for k in KPI_SPARKS + ("labor_cost_usd", "near_misses", "jam_hours"):
        va = [d["kpis"][k] for d in da.values() if d["kpis"] and k in d["kpis"]]
        vb = [d["kpis"][k] for d in db.values() if d["kpis"] and k in d["kpis"]]
        if va and vb:
            agg = (lambda xs: sum(xs) / len(xs)) if k in ("on_time_pct", "mispick_rate", "lph") else \
                  ((lambda xs: xs[-1]) if k == "reslot_progress_pct" else sum)
            totals[k] = {"a": round(agg(va), 3), "b": round(agg(vb), 3), "delta": round(agg(vb) - agg(va), 3)}
    return {"a": _run_header(ra), "b": _run_header(rb), "days": rows, "first_divergence": first_div,
            "totals": totals, "recorded": recorded()}


def _strip(levers: list | None) -> list:
    return [{k: v for k, v in lv.items() if k != "decision_id"} for lv in (levers or [])]


def _node_label(run_id: str, node: str, cache: dict) -> dict:
    kind, _, ident = node.partition(":")
    out = {"id": node, "type": kind, "label": ident, "day": None, "detail": ""}
    if kind == "dec":
        d = cache.setdefault("dec", {x["decision_id"]: x for x in store().decisions(run_id)}).get(ident)
        if d:
            out.update(day=d["day"], label=f"Decision day {d['day']}",
                       detail=effects(d["levers"]), levers="; ".join(lever_label(lv) for lv in (d["levers"] or [])),
                       rationale=d["rationale"], by=d["decided_by"], phrase=f"on day {d['day']} Megan chose {effects(d['levers'])}")
    elif kind == "evt":
        e = cache.setdefault("evt", {x["event_id"]: x for x in store().events(run_id)}).get(ident)
        if e:
            out.update(day=e["day"], label=event_label(e), detail=f"{e['source']} event, day {e['day']}", source=e["source"],
                       phrase=event_plain(e))
    elif kind == "mtg":
        m = cache.setdefault("mtg", {x["meeting_id"]: x for x in store().meetings(run_id)}).get(ident)
        if m:
            out.update(day=m["day"], label=f"{m['kind'].title()} day {m['day']}",
                       detail="; ".join(m["agenda"] or []), outcome=m.get("outcome"),
                       phrase=f"{'the director was called in' if m['kind'] == 'escalation' else 'the standup met'} on day {m['day']}")
    elif kind == "con":
        k = cache.setdefault("con", {x["constraint_id"]: x for x in _q("constraints", run_id=run_id)}).get(ident)
        if k:
            a = k.get("args") or {}
            who = a.get("customer") or a.get("aisle") or ""
            out.update(day=k["day_start"], label=f"Lock: {event_label(k)}",
                       detail=f"{k['owner_role'] or ''} day {k['day_start']}–{k['day_end']}", owner=k["owner_role"],
                       phrase=(f"{who}'s SKUs were locked until day {k['day_end']}" if a.get("customer") else
                               f"aisle {who} stayed closed until day {k['day_end']}" if a.get("aisle") else
                               f"a constraint held until day {k['day_end']}"))
    elif kind == "kpi":
        parts = ident.split(":")
        if len(parts) >= 3:
            day = int(parts[-2])
            name = parts[-1]
            out.update(day=day, label=f"{name.replace('_', ' ')} day {day}", detail="KPI",
                       phrase=f"{name.replace('_', ' ')} on day {day}")
            dr = next((d for d in store().day_results(run_id) if d["day"] == day), None)
            if dr and dr["kpis"] and name in dr["kpis"]:
                out["value"] = dr["kpis"][name]
                out["detail"] = f"{name.replace('_', ' ')} = {dr['kpis'][name]}"
    return out


@app.get("/cascade/{run_id}/{node_id:path}")
def cascade(run_id: str, node_id: str, depth: int = 6) -> dict:
    _run_or_404(run_id)
    edges = store().cascade(run_id, node_id, depth)
    ids = {node_id}
    for e in edges:
        ids.add(e["from_id"])
        ids.add(e["to_id"])
    cache: dict = {}
    nodes = [_node_label(run_id, n, cache) for n in sorted(ids)]
    nodes.sort(key=lambda n: (n["day"] if n["day"] is not None else 10**6, n["id"]))
    # one-line story: follow edges forward from the earliest node
    order = {"evt": 0, "mtg": 1, "dec": 2, "con": 3, "kpi": 4}
    chain = sorted(nodes, key=lambda n: (n["day"] if n["day"] is not None else 10**6, order.get(n["type"], 9)))
    sentence = " → ".join(n.get("phrase") or n["label"] for n in chain)
    return {"run_id": run_id, "root": node_id, "nodes": nodes, "sentence": sentence[:1].upper() + sentence[1:],
            "edges": [{"from": e["from_id"], "to": e["to_id"], "kind": e["kind"]} for e in edges],
            "recorded": recorded()}


@app.get("/health")
def health() -> dict:
    return {"ok": True, "store": store().url, "recorded": recorded(), "t": time.time()}
