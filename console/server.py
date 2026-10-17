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
import re
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

import yaml

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
    if request.headers.get("x-gemba-client", "").lower() == "nemoclaw":
        _note_sandbox_call(request.url.path)
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

# Roles: B's org/roles/*.yaml is the single source for id -> name/title; a fallback keeps the console
# working without the org package. `utterances.role`, attendees, hires and decided_by carry ROLE IDS.
_FALLBACK_ROLES = {
    "program_manager": ("Megan", "Program Manager", "core"), "ops_manager": ("Ryan", "Ops Manager", "core"),
    "reslotting_engineer": ("Luke", "Reslotting Engineer", "core"), "analytics": ("Paige", "Analytics", "core"),
    "plant_director": ("Dana", "Plant Director", "director"), "safety": ("Sam", "Safety Lead", "oncall"),
    "quality": ("Ellen", "Quality Lead", "oncall"), "shift_lead": ("Marcus", "Shift Lead", "oncall"),
    "replenishment": ("Tom", "Replenishment Lead", "oncall"),
}
_ROLE_ALIASES = {"reslot_engineer": "reslotting_engineer", "director": "plant_director"}
_roles_cache: dict | None = None


def roles() -> dict[str, dict]:
    """id -> {id, name, title, kind} with kind in core | oncall | director."""
    global _roles_cache
    if _roles_cache is not None:
        return _roles_cache
    out: dict[str, dict] = {}
    try:
        from org.roles import load_roles  # B's package; optional for the console
        for rid, r in load_roles().items():
            kind = "director" if getattr(r, "power", "") == "director" else (
                "core" if getattr(r, "attends", "") == "always" else "oncall")
            out[rid] = {"id": rid, "name": r.name, "title": r.title, "kind": kind}
    except Exception:
        pass
    for rid, (name, title, kind) in _FALLBACK_ROLES.items():
        out.setdefault(rid, {"id": rid, "name": name, "title": title, "kind": kind})
    _roles_cache = out
    return out


def role_of(x: str | None) -> dict:
    """Resolve a role id, an alias, or a first name (older fixtures) to the role record."""
    rs = roles()
    if not x:
        return {"id": "", "name": "", "title": "", "kind": "oncall"}
    key = _ROLE_ALIASES.get(x, x)
    if key in rs:
        return rs[key]
    for r in rs.values():
        if r["name"].lower() == str(x).lower():
            return r
    pretty = str(x).replace("_", " ")
    return {"id": x, "name": pretty.title(), "title": pretty, "kind": "oncall"}


def role_name(x: str | None) -> str:
    return role_of(x)["name"] if x else ""


_DECIDER = {"big_bang": "the big-bang plan", "static": "the static rules", "do_nothing": "the do-nothing plan", "fork": "the fork"}


def decider_name(x: str | None) -> str:
    """Who decided, for sentences: a role's first name, or the baseline that stands in for the org."""
    return _DECIDER.get(x or "") or role_name(x) or "the chair"


def protections(run_id: str) -> list[dict]:
    """Locks are `protect` levers on decisions (Ellen's C001 week, zone protection), not constraint rows."""
    out = []
    for d in store().decisions(run_id):
        for i, lv in enumerate(d["levers"] or []):
            if (lv or {}).get("kind") != "protect":
                continue
            days = int(lv.get("days") or 1)
            what = {"customer": "customer", "sku": "SKU", "zone": "zones", "aisle": "aisles"}.get(lv.get("target"), lv.get("target") or "")
            out.append({"constraint_id": f"{d['decision_id']}-p{i}", "kind": "protect", "label": f"protect {what} {', '.join(map(str, lv.get('ids') or []))}".strip(),
                        "day_start": d["day"], "day_end": d["day"] + days - 1, "owner_role": d["decided_by"], "owner": decider_name(d["decided_by"]),
                        "args": lv})
    return out


SCENARIO_TITLES = {"reslot_default": "Reslot of 28 aisles"}


def scenario_title(sid: str | None) -> str:
    if not sid:
        return "—"
    return SCENARIO_TITLES.get(sid, sid.replace("_", " ").capitalize())


VIOLATION_LABEL = {
    "pm_peak_zone_load": "Pick Module zone over its 110% limit", "safety_risk_max": "safety risk over the limit",
    "expected_near_misses": "expected near-misses over the limit", "on_time_pct": "on-time below target",
    "mispick_rate": "mis-picks above target",
}


def violation_label(v: str) -> str:
    for k, lab in VIOLATION_LABEL.items():
        if v.startswith(k):
            return lab
    return v.replace("_", " ")


def humanize_trigger(t: str | None) -> str:
    if not t:
        return ""
    t = t.strip()
    if t == "chair_escalates":
        return "the chair escalated"
    if t.startswith("chair:"):
        return "called in by the chair for " + EVENT_LABEL.get(t.split(":", 1)[1].strip(), t.split(":", 1)[1].strip().replace("_", " ")).lower()
    if t in EVENT_LABEL:
        return EVENT_LABEL[t].lower()
    return t.replace("_", " ")


def humanize_rationale(r: str | None) -> str:
    r = (r or "").strip()
    if r.startswith("(fallback)"):
        rest = r[len("(fallback)"):].strip(" :.")
        return "Chair's default choice" + (f": {rest}" if rest and rest != "the chair's choice stands" else "")
    return r


# The roles write in the harness's own vocabulary (option ids, what-if ids, lever calls, metric
# names). Judges read the room, so the console speaks plain words.
_LEVER_CALL = {"pause_reslot": "pause slot moves", "reslot_tonight": "slot moves tonight", "close_aisle": "close the aisle",
               "hire_temps": "bring in temps", "protect": "protect", "shift_labor": "move people", "set_priority": "set priorities"}
_METRIC = {"on_time_pct": "on-time", "stockout_lines": "stock-outs", "mispick_rate_pct": "mis-picks", "lph": "lines per labor hour",
           "reslot_progress_pct": "reslot progress", "labor_cost_usd": "labor cost", "safety_risk_max": "the safety limit", "safety_risk": "safety risk", "on_time": "on-time",
           "pm_peak_zone_load": "Pick Module peak load", "pm_peak_load": "Pick Module peak load",
           "director_escalation": "director escalation", "mispick_rate": "mis-pick rate", "mispicks": "mis-picks",
           "lines_per_labor_hour": "lines per labor hour", "staging_occupancy": "staging use", "reslot_progress": "reslot progress"}


def plain_text(text: str | None, letters: dict[str, str] | None = None, meeting_id: str | None = None) -> str:
    t = text or ""
    letters = letters or {}

    def opt(m):
        oid, n = m.group(0), m.group(1)
        lab = letters.get(oid) or (letters.get(f"{meeting_id}-o{n}") if meeting_id else None) or chr(64 + int(n))
        return f"option {lab}"
    t = re.sub(r"\b[a-z]{2}-[0-9a-f]{8}-m\d+-o(\d+)\b", opt, t)
    t = re.sub(r"\b(?<!option )o(\d)\b", opt, t)

    def call(m):
        name, args = m.group(1), m.group(2) or ""
        days = re.search(r"days\s*=\s*(\d+)", args)
        moves = re.search(r"n_moves\s*=\s*(\d+)", args)
        out = _LEVER_CALL.get(name, name.replace("_", " "))
        if moves: out = f"{moves.group(1)} {out}"
        if days: out += f" for {days.group(1)} day{'s' if days.group(1) != '1' else ''}"
        return out
    t = re.sub(r"\b(" + "|".join(_LEVER_CALL) + r")(?:\(([^)]*)\))?", call, t)
    t = re.sub(r"\(\s*ev\d+-\d+\s*\)", "(what-if)", t)
    t = re.sub(r"\bev\d+-\d+\b", "the what-if", t)
    for k in sorted(_METRIC, key=len, reverse=True):
        t = re.sub(r"\b" + k + r"\b", _METRIC[k], t)
    t = re.sub(r"\b([a-z]+)_([a-z_]+)\b", lambda m: m.group(0).replace("_", " "), t)
    # metric names that reached us with their underscores already turned into spaces
    for k, v in (("on time pct", "on-time"), ("stockout lines", "stock-outs"), ("mispick rate pct", "mis-picks"), ("mispick rate", "mis-picks")):
        t = re.sub(r"\b" + k + r"\b", v, t)
    return t


def option_letters(opts: list[dict]) -> dict[str, str]:
    """A, B, C… by order within the meeting (ids end in -A/-B or -o1/-o2)."""
    def key(o):
        tail = o["option_id"].rsplit("-", 1)[-1]
        digits = "".join(ch for ch in tail if ch.isdigit())
        return (0, int(digits)) if digits else (1, tail)
    return {o["option_id"]: chr(65 + i) for i, o in enumerate(sorted(opts, key=key))}

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
        "director_escalation": f"the director was pulled in over {_METRIC.get(str(a.get('metric', '')), str(a.get('metric', '')).replace('_', ' '))}",
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
        "run_id": run["run_id"], "scenario": run["scenario_id"], "scenario_title": scenario_title(run["scenario_id"]),
        "profile": run["profile_id"],
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
    letters = option_letters(opts)
    for o in opts:
        o["label"] = letters[o["option_id"]]
        o["lever_labels"] = [lever_label(lv) for lv in (o["levers"] or [])]
        o["effect"] = effects(o["levers"])
        o["proposed_by_id"] = o["proposed_by"]
        o["proposed_by"] = role_name(o["proposed_by"])
        o["vetoed_by_id"] = o["vetoed_by"]
        o["vetoed_by"] = role_name(o["vetoed_by"]) if o["vetoed_by"] else None
        o["locks"] = any((lv or {}).get("kind") == "protect" for lv in (o["levers"] or []))
    for u in utts:
        r = role_of(u["role"])
        u["role_id"], u["role"], u["title"] = r["id"], r["name"], r["title"]
        u["option_label"] = letters.get(u["option_id"]) if u.get("option_id") else None
        u["text"] = plain_text(u["text"], letters, mid)
    ids = [role_of(a)["id"] for a in (m["attendees"] or [])]
    return {**m, "attendee_ids": ids, "attendees": [role_name(a) for a in ids], "utterances": utts,
            "option_letters": letters, "options": sorted(opts, key=lambda o: -(o["score"] or 0))}


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
        dec["decided_by_id"] = dec["decided_by"]
        dec["decided_by"] = role_name(dec["decided_by"])
        dec["rationale"] = plain_text(humanize_rationale(dec["rationale"]))
    events = [dict(e, label=event_label(e), plain=event_plain(e)) for e in store().events(run_id) if e["day"] == day]
    all_hires = [dict(h, role_id=role_of(h["role"])["id"], role=role_name(h["role"]), trigger_label=plain_text(humanize_trigger(h["trigger"])))
                 for h in _q("hires", run_id=run_id)]
    hires = [h for h in all_hires if h["day"] == day or h.get("released_day") == day]
    active = [h for h in all_hires if h["day"] <= day and (h["released_day"] is None or h["released_day"] > day)]
    active_hires = [h["role"] for h in active]
    active_hire_ids = [h["role_id"] for h in active]
    checks = [k for k in _q("checks", run_id=run_id) if k["day"] == day]
    drift = [f for f in _q("drift_flags", run_id=run_id) if f["day"] == day]
    cons = [c for c in [dict(k, label=event_label(k), owner=role_name(k["owner_role"])) for k in _q("constraints", run_id=run_id)] + protections(run_id)
            if c["day_start"] <= day <= (c["day_end"] or c["day_start"])]
    return {
        "run_id": run_id, "day": day, "kpis": dr["kpis"], "predicted": dr["predicted"],
        "violations": dr["violations"] or [], "violation_labels": [violation_label(v) for v in (dr["violations"] or [])],
        "hourly": dr["hourly"] if with_hourly else None,
        "events": events, "meeting": _meeting_bundle(run_id, day), "decision": dec, "hires": hires,
        "active_hires": active_hires, "active_hire_ids": active_hire_ids, "checks": checks, "drift": drift,
        "constraints": cons, "roles": roles(),
    }


# --------------------------------------------------------------------------- auth (optional)

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def require_token(request: Request, authorization: str | None = Header(default=None)) -> None:
    """When GEMBA_API_TOKEN is set, writes from other hosts (the sandbox, the LAN) need
    `Authorization: Bearer <token>`; the operator's own browser on this machine stays open."""
    token = os.environ.get("GEMBA_API_TOKEN")
    if not token or (request.client and request.client.host in _LOOPBACK and not authorization):
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(401, "missing or invalid bearer token")


# --------------------------------------------------------------------------- endpoints

def _read_only(what: str) -> None:
    """The demo console serves a recording (`make demo`): replays and comparisons only. Live work
    (new runs, forks, inbox) needs the live store: `make console`."""
    if recorded():
        raise HTTPException(409, f"This console is replaying a recording, so it cannot {what}. "
                                 "Start the live console (make console) for that.")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


MODEL_LABEL = {"nvidia/NVIDIA-Nemotron-3-Super-120B-A12B": "Nemotron 3 Super", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": "Nemotron 3 Nano",
               "nvidia/NVIDIA-Nemotron-3-Ultra-253B-A30B": "Nemotron 3 Ultra", "deepseek-ai/DeepSeek-V4-Pro": "DeepSeek V4 Pro"}
SANDBOX_RUNS = Path(__file__).resolve().parent / "designer" / "sandbox_runs.json"
"""Run ids started by the Scenario Designer sandbox (a POST /runs carrying the bearer token from
another host). Kept as a small tracked file so a recording can say which runs NemoClaw started."""


def _sandbox_runs() -> set[str]:
    try:
        return set(json.loads(SANDBOX_RUNS.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _note_sandbox_run(run_id: str) -> None:
    ids = sorted(_sandbox_runs() | {run_id})
    SANDBOX_RUNS.write_text(json.dumps(ids, indent=0) + "\n", encoding="utf-8")


SANDBOX_SEEN = Path(__file__).resolve().parent / "designer" / "sandbox_seen.json"
"""When the Scenario Designer sandbox last called this API (it sends `X-Gemba-Client: nemoclaw`).
A small file, so the header chip survives a console restart and a recording replay can show it."""


def _sandbox_seen() -> dict | None:
    try:
        return json.loads(SANDBOX_SEEN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _note_sandbox_call(path: str) -> None:
    SANDBOX_SEEN.write_text(json.dumps({"client": "nemoclaw", "sandbox": "gemba", "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                        "path": path}) + "\n", encoding="utf-8")


_LIVE: set[str] = set()  # runs started by this console process (forks and POST /runs)


@app.get("/runs")
def list_runs() -> dict:
    from sqlalchemy import func
    calls, met = md.tables["llm_calls"], md.tables["meetings"]
    with store().engine.connect() as conn:  # which runs the org actually ran (cut-off runs never got a cost)
        org = {r[0] for r in conn.execute(select(calls.c.run_id).group_by(calls.c.run_id))}
        org |= {r[0] for r in conn.execute(select(met.c.run_id).group_by(met.c.run_id))}
        days = dict(conn.execute(select(md.tables["day_results"].c.run_id, func.count()).group_by(md.tables["day_results"].c.run_id)).all())
    with store().engine.connect() as conn:
        models: dict[str, set] = {}
        for rid, mdl in conn.execute(select(calls.c.run_id, calls.c.model).group_by(calls.c.run_id, calls.c.model)):
            if mdl and not str(mdl).startswith("("):
                models.setdefault(rid, set()).add(str(mdl))
    sandbox = _sandbox_runs()
    out = []
    for r in store().list_runs():
        h = _run_header(r)
        h["org"] = r["run_id"] in org
        h["models"] = sorted(MODEL_LABEL.get(m, m.rsplit("/", 1)[-1]) for m in models.get(r["run_id"], ()))
        h["via_sandbox"] = r["run_id"] in sandbox
        h["days_done"] = days.get(r["run_id"], 0)
        # a "running" run this process is not executing was cut off (console restarted)
        if h["status"] == "running" and r["run_id"] not in _LIVE:
            h["status"] = "stopped"
        out.append(h)
    return {"recorded": recorded(), "store": store().url, "runs": out, "sandbox_seen": _sandbox_seen()}


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
    hdr["hires"] = [dict(h, role_id=role_of(h["role"])["id"], role=role_name(h["role"]), trigger_label=plain_text(humanize_trigger(h["trigger"])))
                    for h in _q("hires", run_id=run_id)]
    hdr["constraints"] = [dict(k, label=event_label(k), owner=role_name(k["owner_role"])) for k in _q("constraints", run_id=run_id)] + protections(run_id)
    hdr["roles"] = roles()
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
        "violation_labels": [violation_label(v) for v in (d["violations"] or [])],
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
    _read_only("take an inbox message")
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "text is empty")
    msg_id = store().inbox_push(run_id, text, body.day_hint)
    return {"ok": True, "msg_id": msg_id, "run_id": run_id, "text": text, "day_hint": body.day_hint,
            "pending": [m for m in _q("inbox", run_id=run_id) if m["consumed_day"] is None]}


PRESETS = {  # first = default in the fork dialog; pause_3 diverges on any parent (COMMS B-2)
    "pause_3": {"label": "Pause the reslot for 3 days", "levers": [{"kind": "pause_reslot", "days": 3}]},
    "protect_pm": {"label": "Protect the Pick Module all week",
                   "levers": [{"kind": "protect", "target": "zone", "ids": ["1", "2", "3", "4", "5", "6"], "days": 7}]},
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


def _json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


@app.get("/learning")
def learning() -> dict:
    """Version N+1 for the console, both outcomes the data supports (COMMS B-13):
    - rules: each retro lesson with the fork that verified it (rule: fork beats parent, delta > 0),
    - scenarios: crew v0 vs crew vN per held-out test scenario, static as the reference (from the
      static row, else derived as score - score_vs_static), with run ids for Compare,
    - gate: the latest gate row (rule: spec 13.2, mean test score up and violations not up; no
      verdict when either side had LLM fallbacks)."""
    rules = []
    for r in _q("playbook_rules"):
        ev = _json(r.get("evidence")) or {}
        if not isinstance(ev, dict):
            ev = {}
        rid, fid = ev.get("run_id"), ev.get("fork_run_id")
        rules.append({"version": r["version"], "rule_id": r["rule_id"], "role_id": role_of(r["role"])["id"],
                      "role": role_name(r["role"]) or r["role"], "title": role_of(r["role"]).get("title", ""),
                      "tags": _json(r.get("tags")) or [], "trigger": plain_text(str(_json(r.get("trigger")) or "")),
                      "advice": plain_text(r["advice"]), "score_delta": r["score_delta"], "status": r["status"],
                      "day": ev.get("day"), "regret_day": ev.get("regret_day"), "run_id": rid, "fork_run_id": fid,
                      "parent_score": ev.get("parent_score"), "fork_score": ev.get("fork_score"),
                      "min_delta": ev.get("min_delta", 0), "fallbacks": (ev.get("fork_fallbacks") or 0) + (ev.get("parent_fallbacks") or 0),
                      "compare": f"{rid},{fid}" if rid and fid and store().get_run(rid) and store().get_run(fid) else None})
    rows = [dict(r, metrics=_json(r.get("metrics")) or {}) for r in _q("eval_results")]
    gates = [r for r in rows if r["contender"] == "gate"]
    def _norm(g: dict) -> dict:
        g = dict(g)
        for a, b in (("mean_score_v0", "mean_score_from"), ("mean_score_v1", "mean_score_to"),
                     ("violations_v0", "violations_from"), ("violations_v1", "violations_to")):
            if g.get(a) is None and g.get(b) is not None:
                g[a] = g[b]
        if g.get("promoted") is None:
            g["promoted"] = g.get("improved")
        g.setdefault("rule", "spec 13.2: mean test score up and violations not up")
        # a verdict exists when `promoted` is decided; `reason` is then its explanation, not a refusal
        g["no_verdict"] = g.get("promoted") is None or bool((g.get("fallbacks_from") or 0) + (g.get("fallbacks_to") or 0))
        return g
    gates = [_norm(r["metrics"]) for r in gates]
    final = gates[-1] if gates else None
    gate_runs = set((final or {}).get("run_ids_from") or []) | set((final or {}).get("run_ids_to") or [])
    by: dict[str, dict[str, dict]] = {}
    for r in rows:
        m = r["metrics"]
        if r["contender"] == "gate" or m.get("score") is None or not str(r["scenario_id"]).startswith("test"):
            continue  # the chart is the held-out test set only; train and baseline rows stay out
        name = r["contender"] if r["contender"] != "crew" else "crew_v0"
        if name.startswith("crew") and gate_runs and m.get("run_id") not in gate_runs:
            continue  # crew rows from an earlier (dry-run) gate: shown in the history note, not the chart
        sc = by.setdefault(r["scenario_id"], {})
        sc[name] = {"score": float(m["score"]), "violations": m.get("violations"), "run_id": m.get("run_id")}
        if name.startswith("crew") and "static" not in sc and m.get("score_vs_static") is not None:
            sc["static"] = {"score": float(m["score"]) - float(m["score_vs_static"]), "derived": True, "run_id": None}
    scen = [{"scenario": k, "scores": {c: v["score"] for c, v in cs.items()},
             "runs": {c: v.get("run_id") for c, v in cs.items() if v.get("run_id")},
             "violations": {c: v.get("violations") for c, v in cs.items()},
             "static_derived": bool((cs.get("static") or {}).get("derived"))} for k, cs in sorted(by.items())]
    return {"rules": rules, "scenarios": scen, "gate": final, "earlier_gates": gates[:-1],
            "rules_rule": "retro: the fork of the regret day must beat the parent (delta > 0; later runs: > 0.01, no LLM fallbacks)"}


CHAT_URL = Path(__file__).resolve().parent / "designer" / "chat_url.txt"


@app.get("/designer")
def designer() -> dict:
    """Where a human talks to Gemba: the NemoClaw sandbox's chat UI (OpenClaw with the gemba skill).
    The URL carries a per-machine token, so it comes from `nemoclaw gemba dashboard-url --quiet`
    saved to console/designer/chat_url.txt (gitignored) or GEMBA_CHAT_URL."""
    url = (os.environ.get("GEMBA_CHAT_URL") or "").strip()
    if not url:
        try:
            url = CHAT_URL.read_text(encoding="utf-8").strip().splitlines()[0]
        except (OSError, IndexError):
            url = ""
    return {"chat_url": url or None, "sandbox": "gemba", "sandbox_seen": _sandbox_seen(),
            "setup": "nemoclaw gemba dashboard-url --quiet > console/designer/chat_url.txt"}


class AskIn(BaseModel):
    text: str
    session: str = "gemba-console"


def _nemoclaw_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = "/Applications/Docker.app/Contents/Resources/bin:" + str(Path.home() / ".local" / "bin") + ":" + env.get("PATH", "")
    return env


def _agent_reply(raw: str) -> str:
    """OpenClaw --json prints one JSON document: result.payloads[*].text is the reply."""
    try:
        d = json.loads(raw)
        texts = [p.get("text") for p in ((d.get("result") or {}).get("payloads") or []) if isinstance(p, dict) and p.get("text")]
        if texts:
            return "\n\n".join(t.strip() for t in texts)
    except ValueError:
        pass
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        for k in ("text", "content", "reply", "message", "output"):
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
            elif isinstance(v, dict) and isinstance(v.get("text"), str):
                out.append(v["text"].strip())
    return out[-1] if out else raw.strip()


@app.post("/designer/ask", dependencies=[Depends(require_token)])
def designer_ask(body: AskIn) -> dict:
    """Talk to Gemba inside the console: one turn of the Scenario Designer agent in the NemoClaw
    sandbox (`nemoclaw gemba agent --agent main --session-id <s> -m <text> --json`). The agent's
    own calls come back to this console through the sandbox bridge (tagged "via NemoClaw")."""
    import shutil
    import subprocess
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "empty message")
    env = _nemoclaw_env()
    exe = shutil.which("nemoclaw", path=env["PATH"])
    if not exe:
        raise HTTPException(503, "nemoclaw is not installed on this machine")
    session = re.sub(r"[^A-Za-z0-9_-]", "-", body.session)[:64] or "gemba-console"
    # The sandbox agent is a general OpenClaw assistant; the steer keeps it in its Gemba role.
    steer = ("You are Gemba's Scenario Designer (skill `gemba` in your workspace: read skills/gemba/SKILL.md and "
             "scenario_schema.md before acting). Answer as Gemba, in plain warehouse language, briefly. "
             "If asked to rehearse something, write the scenario and start the run through the console API as the skill says. "
             "Message from the operator at the console: ")
    t0 = time.time()
    reply, raw, err = "", "", ""
    try:
        proc = subprocess.run([exe, "gemba", "agent", "--agent", "main", "--session-id", session, "-m", steer + text, "--json"],
                              capture_output=True, text=True, timeout=float(os.environ.get("GEMBA_TALK_TIMEOUT_S") or 20), env=env)
        raw = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        reply = _agent_reply(proc.stdout or "") if proc.stdout.strip() else ""
        if proc.returncode != 0 and not proc.stdout.strip():
            err = f"nemoclaw exited {proc.returncode}"
    except subprocess.TimeoutExpired:
        err = "agent timeout"
    # Demo fallback: a slow or empty agent turn gets the scripted Gemba answer (marked as such).
    bad = (not reply) or reply.startswith("{") or "no final summary" in reply.lower()
    if bad:
        scripted = _talk_script()
        key = next((k for k in scripted if k != "default" and k != "_about" and k in text.lower()), "default")
        return {"ok": True, "session": session, "reply": scripted.get(key, scripted.get("default", "")), "scripted": True,
                "why": err or "agent gave no text", "seconds": round(time.time() - t0, 1), "raw": raw[-2000:], "sandbox_seen": _sandbox_seen()}
    return {"ok": True, "session": session, "reply": reply, "scripted": False, "seconds": round(time.time() - t0, 1),
            "raw": raw[-4000:], "sandbox_seen": _sandbox_seen()}


def _talk_script() -> dict:
    try:
        return json.loads((Path(__file__).resolve().parent / "designer" / "talk_script.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"default": "I can rehearse any month you describe; tell me the events and the days."}


@app.get("/forks/presets")
def fork_presets() -> dict:
    return {"presets": PRESETS}


@app.post("/runs/{run_id}/fork")
def fork(run_id: str, body: ForkIn) -> dict:
    _run_or_404(run_id)
    _read_only("fork a run")
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
    try:
        new_id = api.fork_run(run_id, body.day, typed, cons, store=store())
    except FileNotFoundError as exc:  # the parent's day snapshot is not on this machine
        raise HTTPException(409, f"No snapshot of {run_id} for day {body.day - 1} on this machine, so it cannot be forked here. "
                                 "Fork a run that was executed by this console.") from exc
    _LIVE.add(new_id)
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


@app.post("/runs", dependencies=[Depends(require_token)])
def start_run(body: RunIn, request: Request, authorization: str | None = Header(default=None)) -> dict:
    _read_only("start a new run")
    api = _engine_api()
    run_id = api.start_run(body.scenario, body.profile, body.playbook, body.policy, body.seed, store=store())
    _LIVE.add(run_id)
    if authorization and request.client and request.client.host not in _LOOPBACK:
        _note_sandbox_run(run_id)  # the Scenario Designer sandbox: the only non-local caller with the token
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
            first_div = {"day": day, "a": {"decision_id": xa[day]["decision_id"], "levers": la, "effect": ea, "rationale": plain_text(humanize_rationale(xa[day]["rationale"]))},
                         "b": {"decision_id": xb[day]["decision_id"], "levers": lb, "effect": eb, "rationale": plain_text(humanize_rationale(xb[day]["rationale"]))}}
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
                       rationale=plain_text(humanize_rationale(d["rationale"])), by=d["decided_by"], phrase=f"on day {d['day']} {decider_name(d['decided_by'])} chose {effects(d['levers'])}")
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
            word = _METRIC.get(name, name.replace("_", " "))
            out.update(day=day, label=f"{word} day {day}", detail="KPI", phrase=f"{word} on day {day}")
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


# --------------------------------------------------------------------------- scenarios (META_HARNESS 4)

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios"
PROTECTED_SCENARIOS = {"reslot_default", "train_1", "train_2", "train_3", "test_1", "test_2", "test_3"}
EXOGENOUS_KINDS = list(c.ExogenousKind.__args__)  # type: ignore[attr-defined]
DEFAULT_HEADCOUNTS = {
    "day": {"pm": 12, "forklift": 14, "replen": 4, "pallet": 3, "consolidation": 10, "loading": 6, "night_move": 0, "temp": 0},
    "swing": {"pm": 12, "forklift": 14, "replen": 4, "pallet": 3, "consolidation": 10, "loading": 6, "night_move": 0, "temp": 0},
    "night": {"pm": 0, "forklift": 0, "replen": 0, "pallet": 0, "consolidation": 0, "loading": 0, "night_move": 4, "temp": 0},
}
ARG_HINTS = {  # field -> (input type, placeholder) for the builder
    "pool": ("pool", "forklift"), "pct": ("int", "15"), "days": ("int", "3"), "skus": ("csv", "S0412, S0418"),
    "family": ("text", "seasonal or F042"), "customer": ("text", "C001"), "factor": ("float", "1.6"),
    "aisle": ("int", "1–28"), "zone": ("int", "1–6"), "hours": ("int", "4"), "no_moves_in_aisles": ("csv", "12, 13, 14"),
    "n": ("int", "40"), "count": ("int", "2"),
}


def _scenario_summary(raw: dict) -> str:
    evs = raw.get("events") or []
    if not evs:
        return "no scripted events"
    return "; ".join(event_plain({"kind": e.get("kind"), "args": e.get("args") or {}}) + f" (day {e.get('day')})" for e in evs[:4]) + \
        (f"; +{len(evs) - 4} more" if len(evs) > 4 else "")


@app.get("/scenarios/meta")
def scenarios_meta() -> dict:
    """Everything the builder needs to generate its fields from the contract."""
    return {"kinds": EXOGENOUS_KINDS, "args": {k: c.EVENT_ARGS[k] for k in EXOGENOUS_KINDS}, "hints": ARG_HINTS,
            "pools": list(c.Pool.__args__), "shifts": list(c.Shift.__args__), "headcounts": DEFAULT_HEADCOUNTS,  # type: ignore[attr-defined]
            "protected": sorted(PROTECTED_SCENARIOS), "labels": {k: EVENT_LABEL.get(k, k) for k in EXOGENOUS_KINDS},
            "lead_days": {k: c.ANNOUNCE_LEAD_DAYS.get(k, 0) for k in EXOGENOUS_KINDS},
            "profiles": sorted(p.stem for p in (SCENARIO_DIR.parent / "profiles").glob("*.yaml")) or ["customer_first"],
            "auth_required": bool(os.environ.get("GEMBA_API_TOKEN"))}


@app.get("/scenarios")
def list_scenarios() -> dict:
    out = []
    for f in sorted(SCENARIO_DIR.glob("*.yaml")):
        try:
            raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # a broken file should not hide the rest
            out.append({"id": f.stem, "file": f.name, "error": str(exc)})
            continue
        out.append({"id": raw.get("id", f.stem), "file": f.name, "days": raw.get("days"), "seed": raw.get("seed"),
                    "n_events": len(raw.get("events") or []), "summary": _scenario_summary(raw),
                    "protected": f.stem in PROTECTED_SCENARIOS})
    return {"scenarios": out}


@app.get("/scenarios/{sid}")
def get_scenario(sid: str) -> dict:
    if "/" in sid or "\\" in sid or ".." in sid:
        raise HTTPException(422, "bad scenario id")
    f = SCENARIO_DIR / f"{sid}.yaml"
    if not f.exists():
        raise HTTPException(404, f"scenario {sid!r} not found")
    raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return {"id": sid, "scenario": raw, "yaml": f.read_text(encoding="utf-8"), "protected": sid in PROTECTED_SCENARIOS}


class ScenarioIn(BaseModel):
    yaml: str


class EventsIn(BaseModel):
    events: list[dict] = []


@app.post("/scenarios/phrases")
def scenario_phrases(body: EventsIn) -> dict:
    """Plain-language preview for the builder rows (same wording as the console)."""
    return {"phrases": [event_plain({"kind": e.get("kind"), "args": e.get("args") or {}}) for e in body.events]}


@app.post("/scenarios", dependencies=[Depends(require_token)])
def save_scenario(body: ScenarioIn) -> dict:
    """Validate (A's loader rules, then contracts.Scenario) and write scenarios/<id>.yaml.
    HTTP 200 with ok=false on validation errors so an agent can fix and retry."""
    from scenarios.loader import validate_scenario
    try:
        raw = yaml.safe_load(body.yaml)
    except yaml.YAMLError as exc:
        return {"ok": False, "id": None, "path": None, "errors": [f"yaml: {exc}"]}
    errors = validate_scenario(raw)
    if isinstance(raw, dict):  # only exogenous kinds may be scripted; consequences come from the floor
        for i, e in enumerate(raw.get("events") or []):
            if isinstance(e, dict) and e.get("kind") in c.EVENT_KINDS and e.get("kind") not in EXOGENOUS_KINDS:
                errors.append(f"event {i}: {e.get('kind')!r} is a consequence the floor produces, not a scripted event")
    sid = str((raw or {}).get("id", "")) if isinstance(raw, dict) else ""
    if isinstance(raw, dict):
        if not sid:
            errors.append("`id` is required")
        elif "/" in sid or "\\" in sid or ".." in sid or not all(ch.isalnum() or ch in "_-" for ch in sid):
            errors.append("`id` must be a slug: letters, digits, `_` and `-` only")
        elif sid in PROTECTED_SCENARIOS:
            errors.append(f"`id` {sid!r} is protected (train/test/default); choose another name")
    if not errors:
        try:
            c.Scenario.model_validate({**raw, "days": tuple(raw["days"])})
        except Exception as exc:
            errors.append(f"contract: {exc}")
    if errors:
        return {"ok": False, "id": sid or None, "path": None, "errors": errors}
    path = SCENARIO_DIR / f"{sid}.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"ok": True, "id": sid, "path": str(path.relative_to(SCENARIO_DIR.parent)), "errors": []}


class DescribeIn(BaseModel):
    text: str


@app.post("/scenarios/describe", dependencies=[Depends(require_token)])
def describe_scenario(body: DescribeIn) -> dict:
    """Plain words -> scenario draft via B's org.scenario_designer (lazy; 503 if absent)."""
    try:
        from llm.client import LLM
        from org import scenario_designer as sd
    except Exception as exc:
        raise HTTPException(503, {"error": "the scenario designer is not available in this checkout",
                                  "detail": str(exc), "hint": "build the scenario by hand in the dialog"})
    if hasattr(sd, "describe"):  # richer shape: dropped-event notes, yaml, calls
        r = sd.describe(body.text, llm=LLM())
        return {"ok": bool(r.get("ok")), "scenario": r.get("scenario") or {}, "yaml": r.get("yaml", ""),
                "errors": list(r.get("errors") or []), "dropped": list(r.get("dropped") or []),
                "confidence": r.get("confidence"), "summary": r.get("summary") or "", "calls": r.get("calls")}
    raw, errors, draft = sd.design_scenario(LLM(), body.text)
    return {"ok": not errors, "scenario": raw, "errors": errors, "dropped": [],
            "confidence": getattr(draft, "confidence", None), "summary": getattr(draft, "summary", None)}


@app.get("/health")
def health() -> dict:
    return {"ok": True, "store": store().url, "recorded": recorded(), "t": time.time(), "sandbox_seen": _sandbox_seen()}
