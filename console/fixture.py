"""Hand-made run store for the console (CONTRACT section 9 stub).

    python -m console.fixture --out fixtures/demo_runs.db

Builds, with B's `RunStore` writers only, one 30-day hero run (days 61-90) plus a fork of it
at day 69. Synthetic but plausible: hourly payloads per floor/HOURLY.md, the spec 9.5 event
calendar, a hero cascade chain, a Safety veto, a Quality lock, scored options, hires, checks
and a drift flag. Deterministic (seeded). No LLM, no twin.
"""

from __future__ import annotations

import argparse
import math
import os
import random
from pathlib import Path

import contracts as c
from engine.store import RunStore

HERO_RUN = "bt-hero0001"
FORK_RUN = "fk-pm000069"
DAYS = range(61, 91)
HOURS = list(range(6, 24)) + list(range(0, 6))  # the day runs 06..23 then 00..05

ROLES = {
    "program_manager": "Megan", "ops_manager": "Ryan", "reslot_engineer": "Luke", "analytics": "Paige",
    "director": "Dana", "safety": "Sam", "quality": "Ellen", "shift_lead": "Marcus", "replenishment": "Tom",
}
CORE = ["program_manager", "ops_manager", "reslot_engineer", "analytics"]

# spec 9.5 default calendar
CALENDAR = [
    (63, "labor_shortage", {"pool": "forklift", "pct": 15, "days": 3}),
    (66, "rack_damage", {"aisle": 7, "days": 3}),
    (69, "demand_surge", {"family": "F042", "factor": 1.6, "days": 5}),
    (72, "key_customer_week", {"customer": "C001", "days": 5}),
    (75, "pm_zone_down", {"zone": 2, "hours": 4}),
    (78, "leadership_visit", {"no_moves_in_aisles": [12, 13, 14, 15]}),
    (82, "wms_sync_delay", {"days": 2}),
    (86, "returns_spike", {"pct": 20, "days": 3}),
]


# --------------------------------------------------------------------------- KPI trajectories

def _curve(day: int, fork: bool) -> dict:
    """Smooth hand-shaped KPI paths. The fork (protect the PM from day 69) keeps on-time up
    and mis-picks down at the cost of slower reslot progress."""
    t = day - 61
    on_time = 96.4 + 0.9 * math.sin(t / 5)
    mispick = 0.42 + 0.05 * math.sin(t / 3)
    lph = 94 + 15 * (1 - math.exp(-t / 14))
    progress = min(100.0, 100 * (1 - math.exp(-t / 11)) * 1.08)
    staging = 55 + 20 * math.sin(t / 4)
    # the hero dip: aggressive night on 64 -> learning dip -> mis-picks -> escalation 67
    if 65 <= day <= 69:
        k = 1 - abs(day - 67) / 3
        mispick += 0.55 * k
        on_time -= 3.6 * k
        staging += 30 * k
    if 69 <= day <= 73:  # surge
        on_time -= 1.2
        lph -= 3
    if 75 == day:
        lph -= 6
    if 82 <= day <= 83:
        on_time -= 1.5
    if 86 <= day <= 88:
        mispick += 0.12
    if fork and day >= 69:
        on_time += 1.6
        mispick -= 0.14
        progress = min(100.0, progress * 0.82 + 4)
        lph -= 2.5
    return dict(on_time=round(on_time, 2), mispick=round(max(0.2, mispick), 3), lph=round(lph, 1),
                progress=round(progress, 1), staging=round(min(98, max(20, staging)), 1))


def _frozen(day: int, fork: bool) -> list[int]:
    out = []
    if 66 <= day <= 70:
        out.append(7)
    if day == 78:
        out += [12, 13, 14, 15]
    return out


def _moves_for(day: int, fork: bool) -> int:
    if day == 64:
        return 280
    if 66 <= day <= 68:
        return 120
    if day == 78:
        return 0
    if fork and day >= 69:
        return 150
    return 200


# --------------------------------------------------------------------------- hourly payload

def _hourly(day: int, kp: dict, rng: random.Random, fork: bool) -> dict:
    frozen = _frozen(day, fork)
    heavy = day in (64, 67, 69, 70, 72, 73)
    moves_budget = _moves_for(day, fork)
    hours = []
    open_orders = 120
    served = {p: 0 for p in ("pm", "forklift", "pallet", "replen", "consolidation", "loading")}
    loaded = 0
    late = 0
    jam_hour = 16 if (day in (67, 70)) else None
    for h in HOURS:
        phase = "day" if 6 <= h < 14 else ("swing" if 14 <= h < 22 else "night")
        # demand shape: release 05-19, peaks before the 13:00 and 17:00 cutoffs
        if phase == "night":
            load = 0.05
        else:
            load = 0.55 + 0.45 * math.exp(-((h - 11.5) ** 2) / 18) + (0.25 if h in (12, 16) else 0)
        if day in (69, 70, 71, 72, 73):
            load *= 1.25
        if day == 75 and 9 <= h < 13:
            pm_down = 1
        else:
            pm_down = None
        night = phase == "night"
        pm_av = 0 if night else (12 if phase == "day" else 10)
        fk_av = 0 if night else (14 if phase == "day" else 11)
        if 63 <= day <= 65:
            fk_av = int(fk_av * 0.85)
        temps = 6 if (fork and day >= 70 and not night) else 0
        busy_frac = min(1.0, load)
        pools = {
            "pm": {"available": pm_av, "busy": round(pm_av * busy_frac), "queue": int(420 * load), "served": 0},
            "forklift": {"available": fk_av, "busy": round(fk_av * busy_frac), "queue": int(900 * load), "served": 0},
            "pallet": {"available": 0 if night else 3, "busy": 0 if night else 2, "queue": int(10 * load), "served": 0},
            "replen": {"available": 0 if night else 4, "busy": 0 if night else round(4 * busy_frac), "queue": int(6 * load), "served": 0},
            "consolidation": {"available": 0 if night else 10, "busy": 0 if night else round(10 * busy_frac), "queue": int(180 * load), "served": 0},
            "loading": {"available": 0 if night else 6, "busy": 0 if night else round(6 * busy_frac), "queue": int(90 * load), "served": 0},
            "night_move": {"available": 8 if night else 0, "busy": (8 if moves_budget else 0) if night else 0, "queue": 0, "served": 0},
            "temp": {"available": temps, "busy": round(temps * busy_frac), "queue": 0, "served": 0},
        }
        per_hour = {"pm": 1700, "forklift": 1250, "pallet": 55, "replen": 38, "consolidation": 160, "loading": 140}
        for p, base in per_hour.items():
            n = int(base * load * (0.9 + 0.2 * rng.random()))
            served[p] += n
            pools[p]["served"] = served[p]
        zone_base = [0.92, 1.02, 0.88, 0.97, 0.80, 0.91]
        pm_zones = [round(min(1.2, z * load * 0.78 * (0.92 + 0.16 * rng.random())), 2) for z in zone_base]
        if day in (64, 65, 66) and not fork:
            pm_zones[1] = round(pm_zones[1] * 1.12, 2)
        if fork and day >= 69:
            pm_zones = [round(z * 0.9, 2) for z in pm_zones]
        if pm_down:
            pm_zones[1] = 0.0
            pm_zones[0] = round(pm_zones[0] * 1.2, 2)
            pm_zones[2] = round(pm_zones[2] * 1.2, 2)
        aisles = []
        for a in range(1, 29):
            dist = abs(a - 14.5)
            base = 0.75 * math.exp(-dist / 9) + 0.15
            v = base * load * (0.8 + 0.4 * rng.random())
            if a in frozen:
                v = 0.0
            aisles.append(round(min(1.4, v), 2))
        aisle_people = [0 if a + 1 in frozen else min(3, int(round(aisles[a] * 2.6 + rng.random()))) for a in range(28)]
        if night:
            aisle_people = [0] * 28
            if moves_budget:
                for _ in range(10):
                    aisle_people[rng.randrange(28)] += 1
        near_miss = None
        if day == 66 and h == 9:
            near_miss = 7
        if day == 70 and h == 15 and not fork:
            near_miss = 9
        if near_miss:
            aisle_people[near_miss - 1] = 4
        safety = [round(p / 3, 2) for p in aisle_people]
        open_orders = int(max(40, open_orders + pools["pm"]["served"] * 0.001 + 90 * load - 110 * (0.6 if night else 1.0) * (0.9 + 0.2 * rng.random())))
        if heavy and 12 <= h <= 17:
            open_orders += 55
        if jam_hour is not None and h == jam_hour:
            open_orders = max(open_orders, 430)
        if night:
            open_orders = max(30, int(open_orders * 0.7))
        jam = open_orders > 400
        loaded = served["loading"] // 90  # about 90 orders per truck
        if h in (13, 17) and kp["on_time"] < 96:
            late += 1 + int((96 - kp["on_time"]))
        next_cutoff = 13 if h < 13 else (17 if h < 17 else (21 if h < 22 else None))
        if night:
            next_cutoff = None
        stag = kp["staging"] + (12 if night else 0) + 6 * math.sin(h / 3)
        moves = {"done": 0, "sample": []}
        if night and moves_budget:
            done = int(moves_budget / 8 * (0.9 + 0.2 * rng.random()))
            sample = []
            for _ in range(min(10, max(3, done // 3))):
                a = rng.choice([x for x in range(1, 29) if x not in frozen])
                sample.append({"sku": f"S{rng.randrange(2000):04d}",
                               "from": f"A{a:02d}-{rng.randrange(1, 31):02d}-{rng.randrange(1, 3)}",
                               "to": f"PM{rng.randrange(1, 7)}-{rng.randrange(1, 121):03d}"})
            moves = {"done": done, "sample": sample}
        hours.append({
            "hour": h, "phase": phase, "pools": pools, "pm_zones": pm_zones, "aisles": aisles,
            "aisle_people": aisle_people, "safety_risk": safety, "frozen_aisles": list(frozen),
            "consolidator_open": open_orders, "jam": jam,
            "trucks": {"loaded": loaded, "late": late, "next_cutoff": next_cutoff},
            "staging_occupancy": round(min(99.0, max(10.0, stag)), 1),
            "stockouts": int(12 * load * (1.5 if day in (69, 70, 71) else 1)),
            "mispicks": int(kp["mispick"] / 100 * 1000 * load),
            "near_miss_aisle": near_miss, "moves": moves,
        })
    return {"version": 1, "day": day, "hours": hours}


def _kpis(day: int, kp: dict, hourly: dict, fork: bool) -> c.Kpis:
    hs = hourly["hours"]
    lines = sum(h["pools"]["pm"]["served"] for h in hs[-1:]) + hs[-1]["pools"]["forklift"]["served"]
    moves = sum(h["moves"]["done"] for h in hs)
    return c.Kpis(
        lines=int(lines), lph=kp["lph"], throughput_lph_site=round(lines / 24, 1), on_time_pct=kp["on_time"],
        mispick_rate=kp["mispick"], pm_peak_zone_load=max(max(h["pm_zones"]) for h in hs),
        consolidator_queue_max=max(h["consolidator_open"] for h in hs), jam_hours=sum(1 for h in hs if h["jam"]),
        replen_trips=int(38 * 18 * 0.9), stockout_lines=sum(h["stockouts"] for h in hs),
        overtime_hours=(24.0 if day in (64, 67, 69, 70) and not fork else 0.0),
        labor_cost_usd=round(41000 + 1800 * (1 if day in (64, 67, 69, 70) else 0) + (2400 if fork and day >= 70 else 0), 0),
        moves_done=moves, moves_queued=max(0, 2400 - int(kp["progress"] * 24)), reslot_progress_pct=kp["progress"],
        staging_occupancy_max=max(h["staging_occupancy"] for h in hs),
        safety_risk_max=max(max(h["safety_risk"]) for h in hs),
        near_misses=sum(1 for h in hs if h["near_miss_aisle"] is not None),
        mispick_rate_by_customer={"C001": round(kp["mispick"] * (1.9 if 65 <= day <= 68 else 1.1), 3),
                                  "C002": round(kp["mispick"] * 0.9, 3)},
    )


# --------------------------------------------------------------------------- meetings (scripted talk)

def _lev(day: int, kind: str, **kw) -> dict:
    return {"kind": kind, "decision_id": "", **kw}


def _reslot(n: int, **kw) -> dict:
    base = dict(n_moves=n, order="value", include_zones=[], exclude_zones=[])
    base.update(kw)
    return _lev(0, "reslot_tonight", **base)


def meeting_script(day: int, fork: bool) -> dict | None:
    """Returns {kind, agenda, attendees, hires, releases, utterances, options, pick, rationale,
    constraints, evidence} or None for a quiet day."""
    E = lambda eid: f"ev-{day}-{eid}"  # noqa: E731
    M = {}
    if day == 61:
        M = dict(kind="standup", agenda=["Kick off the reslot: 2,400 legs, 30 nights"], hires=[], releases=[],
                 options=[("A", "program_manager", [_reslot(200)], E("a"), 0.71, True, None),
                          ("B", "reslot_engineer", [_reslot(240)], E("b"), 0.68, True, None)],
                 pick="A", rationale="Start at the 200-leg budget and bank a clean first week before pushing.",
                 utterances=[
                     ("program_manager", "position", "Day one. Target layout is 1,100 slots, about 2,400 legs. We ship 24k lines a day while we do it, so on-time stays above 95 or we slow down."),
                     ("reslot_engineer", "proposal", "Value order, 240 legs tonight. The top 300 SKUs by seconds-saved pay back in under 9 days each.", "B"),
                     ("ops_manager", "position", "Not 240 on night one. The night crew has never run above 200 and Pick Module zone 2 is already at 1.02 on Tuesdays."),
                     ("analytics", "position", "Three-day what-if: 200 legs holds on-time at 96.1, 240 legs at 95.4 with zone 2 peaking 1.07. Both inside limits, 240 is tighter.", "A"),
                     ("program_manager", "decision", "Option A, 200 legs, value order. We revisit the budget Thursday with real numbers.")])
    elif day == 63:
        M = dict(kind="standup", agenda=["labor_shortage: forklift pool -15% for 3 days"], hires=["shift_lead"], releases=[],
                 options=[("A", "ops_manager", [_lev(0, "labor_allocate", shift="day", pool="forklift", headcount=12), _reslot(200)], E("a"), 0.66, True, None),
                          ("B", "shift_lead", [_lev(0, "overtime", shift="swing", pool="forklift", hours=16), _reslot(200)], E("b"), 0.62, True, None)],
                 pick="A", rationale="Reallocate pallet pickers to forklifts instead of overtime; keep the 200-leg night.",
                 utterances=[
                     ("program_manager", "position", "Forklift pool is down 15 percent through Friday. Marcus, I need the real headcount, not the roster."),
                     ("shift_lead", "position", "Eleven trucks on days, nine on swing. I can give you sixteen overtime hours on swing but not three days running.", "B"),
                     ("ops_manager", "proposal", "Move two pallet pickers onto forklifts for the day shift. Pallet queue is ten, it can wait.", "A"),
                     ("analytics", "position", "Reallocation what-if: on-time 96.0, LPH flat. Overtime what-if: on-time 96.3 but it trips the fatigue counter by Thursday."),
                     ("program_manager", "decision", "Reallocate. No overtime while the pool is short. Night stays at 200.")])
    elif day == 64:
        M = dict(kind="standup", agenda=["Progress check: 11% done, 3 nights behind plan"], hires=[], releases=[],
                 options=[("A", "reslot_engineer", [_reslot(280, order="value")], E("a"), 0.74, True, None),
                          ("B", "ops_manager", [_reslot(200)], E("b"), 0.70, True, None)],
                 pick="A", rationale="Push to 280 legs tonight to recover the plan; the what-if says mis-picks stay under 0.6.",
                 utterances=[
                     ("reslot_engineer", "proposal", "We are three nights behind. 280 legs tonight, all zone 2 and 3 fast movers, gets us back on the curve.", "A"),
                     ("ops_manager", "position", "That is 280 SKUs in a learning dip tomorrow morning. Pickers will hunt for them. I want 200.", "B"),
                     ("analytics", "position", "Two-day what-if on 280: mis-pick 0.58, on-time 95.6. It passes, barely. The model has not seen 280 in one night before, so the error band is wide.", "A"),
                     ("program_manager", "decision", "Option A. 280 tonight. Paige, flag me if tomorrow's mis-picks cross 0.6 by noon.")])
    elif day == 66:
        M = dict(kind="standup", agenda=["rack_damage: aisle 7, near-miss at 09:00"], hires=["safety"], releases=["shift_lead"],
                 options=[("A", "reslot_engineer", [_reslot(200)], E("a"), 0.69, False, "safety"),
                          ("B", "safety", [_lev(0, "close_aisle", aisle=7, days=5), _reslot(120, exclude_zones=[])], E("b"), 0.61, True, None)],
                 pick="B", rationale="Safety veto stands. Aisle 7 closed five days; night budget down to 120 while picks reroute.",
                 constraints=[("aisle_closure", {"aisle": 7, "days": 5}, "safety")],
                 utterances=[
                     ("program_manager", "position", "Upright in aisle 7 took a forklift hit at nine. Sam, you are in the room."),
                     ("safety", "position", "Rack is out of plumb at bay 12. Nobody picks under it until the engineer signs it off. That aisle closes."),
                     ("reslot_engineer", "proposal", "Keep the night at 200 and route the moves around 7. We cannot lose another night.", "A"),
                     ("safety", "veto", "Vetoed. Aisle 7 at night with movers and a damaged upright is a risk index above 1.0. Closed five days, moves rerouted.", "A"),
                     ("ops_manager", "position", "Picks from 7 go to 6 and 8. Those two will run hot through swing."),
                     ("analytics", "position", "What-if with 7 closed: on-time 94.8 for two days, mis-pick 0.71 as yesterday's moves settle.", "B"),
                     ("program_manager", "decision", "Option B. Aisle 7 closed, night at 120. Marcus, thanks, you are released.")])
    elif day == 67:
        M = dict(kind="escalation", agenda=["customer_escalation: C001 mis-picks 0.9% two days running"], hires=["quality"], releases=[],
                 options=[("A", "quality", [_lev(0, "protect", target="customer", ids=["C001"], days=3), _reslot(120)], E("a"), 0.64, True, None),
                          ("B", "reslot_engineer", [_reslot(200)], E("b"), 0.58, True, None)],
                 pick="A", rationale="Quality lock on C001 SKUs for 3 days. The learning dip from the 280-leg night is the root cause; slow the reslot until it clears.",
                 constraints=[("key_customer_week", {"customer": "C001", "days": 3}, "quality")],
                 utterances=[
                     ("program_manager", "position", "C001 called the director. Their mis-picks were 0.9 yesterday and 0.9 again today. Ellen, what happened?"),
                     ("quality", "position", "Half of C001's lines sit in the 280 SKUs we moved on the 64th. Pickers go to the old slot, grab the neighbour. Classic dip."),
                     ("quality", "lock", "I am locking every C001 SKU for three days. No moves touch them, and they stay where the pickers learned them.", "A"),
                     ("reslot_engineer", "position", "That freezes 140 of my best moves. I can work around it with 200 in zones 4 to 6.", "B"),
                     ("analytics", "position", "Lock plus 120 legs: mis-pick back under 0.6 by day 69. Lock plus 200: 0.66 and on-time 94.9.", "A"),
                     ("program_manager", "decision", "Option A. Lock holds, 120 tonight. The 280 night cost us this week. Noted for the retro.")])
    elif day == 69:
        if fork:
            M = dict(kind="standup", agenda=["demand_surge: family F042 x1.6 for 5 days", "fork: protect the Pick Module all week"], hires=["replenishment"], releases=["quality"],
                     options=[("A", "program_manager", [_lev(0, "protect", target="zone", ids=["1", "2", "3", "4", "5", "6"], days=7), _reslot(150)], E("a"), 0.72, True, None),
                              ("B", "reslot_engineer", [_reslot(200)], E("b"), 0.63, True, None)],
                     pick="A", rationale="Fork decision: protect all six PM zones for the week. Surge demand is served from slots pickers already know.",
                     constraints=[("key_customer_week", {"customer": "F042", "days": 7}, "program_manager")],
                     utterances=[
                         ("program_manager", "position", "Different call today. F042 surges 1.6x from tomorrow. I am protecting the whole Pick Module for the week."),
                         ("ops_manager", "position", "Finally. Zone loads stay under 1.0 and nobody hunts for a slot on a surge day."),
                         ("reslot_engineer", "position", "That pushes the reslot out four nights. I can still run 150 legs in the aisles.", "B"),
                         ("replenishment", "position", "F042 is 11 SKUs. I resize their slots to five days of cover and we stop the stockouts before they start."),
                         ("analytics", "position", "Protect what-if: on-time 97.1 through the surge, mis-pick 0.38, progress 100 on day 88 instead of 84.", "A"),
                         ("program_manager", "decision", "Option A. Protect the PM, 150 legs in the aisles, Tom resizes F042.")])
        else:
            M = dict(kind="standup", agenda=["demand_surge: family F042 x1.6 for 5 days"], hires=["replenishment"], releases=["quality"],
                     options=[("A", "reslot_engineer", [_reslot(200, include_zones=[4, 5, 6])], E("a"), 0.67, True, None),
                              ("B", "ops_manager", [_lev(0, "pause_reslot", days=3)], E("b"), 0.60, True, None),
                              ("C", "replenishment", [_lev(0, "resize_slots", skus=[f"S{i:04d}" for i in (412, 418, 1130)], days_cover=5.0), _reslot(200)], E("c"), 0.69, True, None)],
                     pick="C", rationale="Resize F042 slots and keep the night at 200; Ryan's pause costs four nights of progress.",
                     utterances=[
                         ("program_manager", "position", "F042 surges 1.6x from tomorrow for five days. Tom is in. Ellen, the lock cleared, thank you."),
                         ("replenishment", "proposal", "F042 slots stock out by 10 a.m. at 1.6x. Resize three SKUs to five days of cover. Twelve extra moves tonight.", "C"),
                         ("ops_manager", "proposal", "Pause the reslot three days. Surge week plus new slots is how we got last week's escalation.", "B"),
                         ("reslot_engineer", "proposal", "Keep 200 but only zones 4 to 6, away from the surge family.", "A"),
                         ("analytics", "position", "Resize plus 200: on-time 95.4, stockouts down 60 percent. Pause: on-time 95.9 but progress stalls at 48.", "C"),
                         ("program_manager", "decision", "Option C. Resize F042, 200 legs in zones 4 to 6. Ryan, if zone 2 peaks over 1.05 we pause.")])
    elif day == 72:
        M = dict(kind="standup", agenda=["key_customer_week: C001 for 5 days"], hires=["quality"], releases=["replenishment"],
                 options=[("A", "quality", [_lev(0, "protect", target="customer", ids=["C001"], days=5), _reslot(200)], E("a"), 0.70, True, None),
                          ("B", "reslot_engineer", [_reslot(220)], E("b"), 0.62, True, None)],
                 pick="A", rationale="C001 week: protect their SKUs, run 200 elsewhere.",
                 constraints=[("key_customer_week", {"customer": "C001", "days": 5}, "quality")],
                 utterances=[
                     ("quality", "lock", "C001 week. Their SKUs are locked for five days with the chair's sign-off. We are not repeating day 67.", "A"),
                     ("reslot_engineer", "position", "Fine. 200 in zones 3 to 6 and the back aisles. We are at 62 percent."),
                     ("program_manager", "decision", "Option A. Locked and 200 elsewhere.")])
    elif day == 75:
        M = dict(kind="standup", agenda=["pm_zone_down: zone 2 for 4 hours"], hires=[], releases=[],
                 options=[("A", "ops_manager", [_lev(0, "wave_priority", mode="by_cutoff"), _lev(0, "labor_allocate", shift="day", pool="pm", headcount=14)], E("a"), 0.66, True, None)],
                 pick="A", rationale="Zone 2 conveyor down until 13:00; wave by cutoff and two extra pickers in zones 1 and 3.",
                 utterances=[
                     ("ops_manager", "proposal", "Zone 2 conveyor is down until one. Wave by cutoff and I put two more pickers on zones 1 and 3.", "A"),
                     ("analytics", "position", "Four hours down: on-time 95.1 if we wave by cutoff, 93.8 if we do not.", "A"),
                     ("program_manager", "decision", "Do it. Night stays at 200.")])
    elif day == 78:
        M = dict(kind="standup", agenda=["leadership_visit: no moves in aisles 12-15"], hires=[], releases=[],
                 options=[("A", "program_manager", [_lev(0, "pause_reslot", days=1)], E("a"), 0.60, True, None),
                          ("B", "reslot_engineer", [_reslot(160, exclude_zones=[])], E("b"), 0.63, True, None)],
                 pick="A", rationale="One quiet night for the visit; staging gets cleared instead.",
                 utterances=[
                     ("program_manager", "position", "Dana walks the floor with the VP at ten. Aisles 12 to 15 stay untouched and I want staging under 50 percent."),
                     ("reslot_engineer", "proposal", "160 legs in the far aisles will not be visible from the tour route.", "B"),
                     ("program_manager", "decision", "No. One night off, movers clear staging. We are at 78 percent and can afford it.")])
    elif day == 82:
        M = dict(kind="standup", agenda=["wms_sync_delay: 2 days"], hires=["quality"], releases=[],
                 options=[("A", "quality", [_reslot(100), _lev(0, "protect", target="customer", ids=["C001", "C002"], days=2)], E("a"), 0.64, True, None),
                          ("B", "reslot_engineer", [_reslot(200)], E("b"), 0.57, True, None)],
                 pick="A", rationale="WMS lagging: slot changes may not reach the pick lists. Half-speed night, key customers protected.",
                 constraints=[("key_customer_week", {"customer": "C001", "days": 2}, "quality")],
                 utterances=[
                     ("quality", "position", "If the WMS is two days behind, every move tonight is a mis-pick tomorrow. Half the night and lock the key customers."),
                     ("reslot_engineer", "position", "We have 300 legs left. Fine, 100 tonight.", "A"),
                     ("program_manager", "decision", "Option A. 100 legs, key customers protected until the sync clears.")])
    elif day == 86:
        M = dict(kind="standup", agenda=["returns_spike: +20% for 3 days"], hires=[], releases=["quality"],
                 options=[("A", "ops_manager", [_lev(0, "labor_allocate", shift="swing", pool="consolidation", headcount=12), _reslot(60)], E("a"), 0.65, True, None)],
                 pick="A", rationale="Returns land in consolidation; two more people there on swing. Reslot is nearly done.",
                 utterances=[
                     ("ops_manager", "proposal", "Returns are up 20 percent and they all land on the consolidator. Two more on swing.", "A"),
                     ("program_manager", "decision", "Agreed. The last 60 legs tonight.")])
    elif day == 88:
        M = dict(kind="standup", agenda=["Reslot complete: close-out and done-checks"], hires=[], releases=[],
                 options=[("A", "program_manager", [_lev(0, "pause_reslot", days=3)], E("a"), 0.75, True, None)],
                 pick="A", rationale="Target layout reached. Final checks: on-time 97.3, mis-pick 0.41, LPH lift 11 percent.",
                 utterances=[
                     ("reslot_engineer", "position", "Last move landed at 03:40. 1,100 slots, 2,412 legs, 26 nights."),
                     ("analytics", "position", "Final checks: on-time 97.3 against 97, mis-pick 0.41 against 0.5, LPH lift 11.2 percent against 8. All pass."),
                     ("program_manager", "decision", "Done. Retro Thursday: the 280-leg night and what it cost us is item one.")])
    else:
        return None
    M.setdefault("constraints", [])
    M["attendees"] = CORE + [r for r in M["hires"]] + (["director"] if M["kind"] == "escalation" else [])
    return M


QUIET = [
    "No new events, KPIs inside their bands. 200 legs tonight in value order.",
    "Quiet day: nothing on the agenda, yesterday's checks all passed. Night crew continues at budget.",
    "No new events and no drift. Holding the plan.",
]


# --------------------------------------------------------------------------- build

def build_run(store: RunStore, run_id: str, *, fork: bool, start_day: int, seed: int,
              parent: str | None = None) -> None:
    rng = random.Random(seed)
    hired: dict[str, int] = {}
    cascade_dec_64 = f"{HERO_RUN}-d64" if not fork else f"{HERO_RUN}-d64"
    for day in DAYS:
        if day < start_day:
            continue
        kp = _curve(day, fork)
        hourly = _hourly(day, kp, rng, fork)
        kpis = _kpis(day, kp, hourly, fork)
        events: list[c.Event] = []
        for (d, kind, args) in CALENDAR:
            if d == day:
                events.append(c.Event(event_id=f"cal-{d}-{kind}", kind=kind, day=d, hour=6, args=args,
                                      announced_day=c.announced_day(kind, d), source="scripted"))
        consequences: list[c.Event] = []
        if day == 66:
            consequences.append(c.Event(event_id="c5-66-safety", kind="safety_review", day=66, hour=9, args={"aisle": 7},
                                        announced_day=66, source="consequence",
                                        caused_by=c.kpi_node_id(run_id, 66, "safety_risk_max")))
        if day == 66 and not fork:
            # the hero chain: 280-leg night (dec 64) -> learning dip -> C001 mis-picks -> escalation on 67
            consequences.append(c.Event(event_id="c2-67-C001", kind="customer_escalation", day=67, hour=6,
                                        args={"customer": "C001"}, announced_day=67, source="consequence",
                                        caused_by=c.node_id(c.DEC, cascade_dec_64)))
        if day == 67 and not fork:
            consequences.append(c.Event(event_id="c6-68-ontime", kind="director_escalation", day=68, hour=6,
                                        args={"metric": "on_time_pct"}, announced_day=68, source="consequence",
                                        caused_by=c.kpi_node_id(run_id, 67, "on_time_pct")))
        if day == 70 and not fork:
            consequences.append(c.Event(event_id="c4-71-staging", kind="staging_congestion", day=71, hour=6,
                                        args={"occupancy": 92.0}, announced_day=71, source="consequence",
                                        caused_by=c.node_id(c.DEC, f"{run_id}-d69")))
        M = meeting_script(day, fork)
        meeting_id = None
        if M:
            meeting_id = f"m-{run_id}-{day}"
            if day == 67 and not fork:
                agenda_causes = ["evt:c2-67-C001"]
            else:
                agenda_causes = [c.node_id(c.EVT, e.event_id) for e in events]
            store.write_meeting(run_id, meeting_id, day, M["kind"], [ROLES[r] for r in M["attendees"]],
                                M["agenda"], outcome=None, caused_by=agenda_causes or None)
            for role in M["hires"]:
                store.write_hire(run_id, day, ROLES[role], trigger=M["agenda"][0])
                hired[role] = day
            for role in M["releases"]:
                store.release_hire(run_id, ROLES[role], day)
                hired.pop(role, None)
            for (oid, by, levers, eid, score, feasible, vetoed) in M["options"]:
                whatif_k = c.Kpis(on_time_pct=round(kp["on_time"] + (score - 0.6) * 10, 1),
                                  mispick_rate=round(kp["mispick"] - (score - 0.6) * 0.5, 2), lph=kp["lph"])
                store.write_evidence(run_id, eid, spec_hash=f"h{abs(hash((day, oid))) % 10**6:06d}",
                                     spec={"levers": levers, "days": 3}, horizon_days=3, kpis=whatif_k,
                                     scorecard={"total": score, "by": ROLES["analytics"]}, created_day=day)
                store.write_option(run_id, meeting_id, f"{meeting_id}-{oid}", levers, ROLES[by], eid, score, feasible,
                                   ROLES[vetoed] if vetoed else None)
            for seq, u in enumerate(M["utterances"]):
                role, kind, text = u[0], u[1], u[2]
                opt = f"{meeting_id}-{u[3]}" if len(u) > 3 else None
                ev_ids = [o[3] for o in M["options"] if opt and o[0] == u[3]] if role == "analytics" else []
                store.write_utterance(run_id, meeting_id, seq, ROLES[role], kind, text, ev_ids, opt)
            store.set_meeting_outcome(run_id, meeting_id, M["rationale"])
            pick = next(o for o in M["options"] if o[0] == M["pick"])
            levers = pick[2]
            rationale = M["rationale"]
            option_id = f"{meeting_id}-{M['pick']}"
            decided_by = ROLES["program_manager"]
            cons = [c.Event(event_id=f"con-{run_id}-{day}-{k}", kind=k, day=day, hour=6, args=a, announced_day=day,
                            source="consequence") for (k, a, _owner) in M["constraints"]]
            top = max(M["options"], key=lambda o: o[4])
            deviates = top[0] != M["pick"]
        else:
            # spec 10.5 quiet day: no standup; the chair writes one line and the standing plan continues
            levers = [_reslot(_moves_for(day, fork))] if _moves_for(day, fork) else [_lev(0, "pause_reslot", days=1)]
            rationale = QUIET[day % 3]
            option_id, decided_by, cons, deviates = None, ROLES["program_manager"], [], False
            meeting_id = f"m-{run_id}-{day}"
            store.write_meeting(run_id, meeting_id, day, "quiet", [ROLES[r] for r in CORE], [], outcome=rationale)
            store.write_utterance(run_id, meeting_id, 0, ROLES["program_manager"], "decision", rationale)
        decision_id = f"{run_id}-d{day}"
        decision = c.Decision(decision_id=decision_id, day=day,
                              levers=[dict(l, decision_id=decision_id) for l in levers], constraints=cons,
                              decided_by=decided_by, rationale=rationale, option_id=option_id, meeting_id=meeting_id)
        predicted = c.Kpis(on_time_pct=round(kp["on_time"] + rng.uniform(-0.8, 0.8), 2),
                           mispick_rate=round(kp["mispick"] + rng.uniform(-0.1, 0.1), 3), lph=round(kp["lph"] + rng.uniform(-3, 3), 1))
        if day == 70 and not fork:
            predicted = c.Kpis(on_time_pct=round(kp["on_time"] + 2.7, 2), mispick_rate=round(kp["mispick"] - 0.3, 3), lph=kp["lph"])
        checks = [
            {"check_name": "daily.on_time_pct", "scope": "daily", "passed": kpis.on_time_pct >= 95.0, "value": kpis.on_time_pct, "threshold": 95.0},
            {"check_name": "daily.mispick_rate", "scope": "daily", "passed": kpis.mispick_rate <= 0.5, "value": kpis.mispick_rate, "threshold": 0.5},
            {"check_name": "daily.safety_risk_max", "scope": "daily", "passed": kpis.safety_risk_max <= 1.0, "value": kpis.safety_risk_max, "threshold": 1.0},
            {"check_name": "daily.moves_done", "scope": "daily", "passed": kpis.moves_done <= 280, "value": float(kpis.moves_done), "threshold": 200.0},
            {"check_name": "daily.jam_hours", "scope": "daily", "passed": kpis.jam_hours == 0, "value": float(kpis.jam_hours), "threshold": 0.0},
        ]
        if day == 90:
            checks += [
                {"check_name": "final.on_time_pct", "scope": "final", "passed": True, "value": 97.3, "threshold": 97.0},
                {"check_name": "final.mispick_rate", "scope": "final", "passed": True, "value": 0.41, "threshold": 0.5},
                {"check_name": "final.lph_lift_pct", "scope": "final", "passed": True, "value": 11.2, "threshold": 8.0},
                {"check_name": "final.reslot_progress_pct", "scope": "final", "passed": kpis.reslot_progress_pct >= 90, "value": kpis.reslot_progress_pct, "threshold": 90.0},
            ]
        drift = []
        if day == 70 and not fork:
            drift.append({"kind": "prediction_error", "detail": "Predicted on-time 96.6, actual 93.9 (gap 2.7 over 3 days). Family F042 forecast rescaled by actual/forecast.", "response": "recalibrate"})
        violations = ["pm_peak_zone_load > 1.10"] if kpis.pm_peak_zone_load > 1.10 else []
        result = c.DayResult(day=day, kpis=kpis, hourly=hourly, violations=violations)
        store.write_day(run_id, day, decision, [], result, predicted=predicted, events=events,
                        new_consequences=consequences, checks=checks, drift_flags=drift,
                        snapshot_path=f"snapshots/{run_id}/{day}.pkl", deviates_from_top_score=deviates)
        if day == 61:
            store.write_agreement(run_id, f"ag-{run_id}-1", "Night budget stays at 200 legs unless the chair raises it in a standup with a what-if.", ["Megan", "Luke", "Ryan"], 61)
        if day == 67 and not fork:
            store.write_agreement(run_id, f"ag-{run_id}-2", "No more than 200 legs on any night that touches a key customer's SKUs.", ["Megan", "Ellen", "Luke"], 67)
    store.finish_run(run_id, "done", llm_usd=1.12 if not fork else 0.74)


def build(out: str, seed: int = 7) -> str:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    for suffix in ("-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            p.unlink()
    url = f"sqlite:///{path}"
    store = RunStore(url)
    store.create_schema()
    store.create_run("backtest", "reslot_default", "customer_first", "v0", seed, run_id=HERO_RUN)
    build_run(store, HERO_RUN, fork=False, start_day=61, seed=seed)
    store.create_run("fork", "reslot_default", "customer_first", "v0", seed, run_id=FORK_RUN,
                     parent_run_id=HERO_RUN, fork_day=69,
                     fork_decision={"levers": [{"kind": "protect", "target": "zone", "ids": ["1", "2", "3", "4", "5", "6"], "days": 7}],
                                    "label": "Protect the Pick Module all week"})
    store.copy_run_prefix(HERO_RUN, FORK_RUN, 69)
    build_run(store, FORK_RUN, fork=True, start_day=69, seed=seed + 1, parent=HERO_RUN)
    store.inbox_push(HERO_RUN, "Forklift 3 is down for the afternoon, maintenance says 4 hours.", day_hint=70)
    with store.engine.begin() as conn:
        from sqlalchemy import update
        from engine.store import inbox as inbox_t
        conn.execute(update(inbox_t).where(inbox_t.c.run_id == HERO_RUN).values(consumed_day=70))
    return url


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="fixtures/demo_runs.db")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    url = build(a.out, a.seed)
    print(f"built {url}: runs {HERO_RUN} (days 61-90) and {FORK_RUN} (fork at 69)")


if __name__ == "__main__":
    main()
