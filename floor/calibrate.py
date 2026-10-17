"""Calibration report (spec 9.3a targets 1-5) on the real inputs.db, default scenario days 61-90.

    uv run python -m floor.calibrate [--days 10] [--url sqlite:///inputs.db]

Prints do-nothing / oracle (target layout applied instantly at no cost) / naive big-bang numbers.
Tune `floor/constants.py` until targets 1-3 hold; never touch the traps.
"""

from __future__ import annotations

import argparse
from statistics import mean

import numpy as np

import contracts as c
from floor.twin import Twin
from scenarios.loader import load_scenario, scenario_events


def _loop(twin: Twin, sc: c.Scenario, policy, days: int, oracle: bool = False, run_id: str = "cal"):
    cal = scenario_events(sc)
    s = twin.initial_state(sc)
    if oracle:
        inp = twin.inputs
        s.sku_loc = np.where(inp.target_loc >= 0, inp.target_loc, s.sku_loc).astype(np.int32)
        s.sku_cap = np.where(inp.target_loc >= 0, inp.target_cap, s.sku_cap).astype(float)
        s.inventory = s.sku_cap.copy()
        s.loc_occupant = {int(l): i for i, l in enumerate(s.sku_loc) if l >= 0}
        for m in s.moves:
            m.legs_done = m.legs
        s.moves_done_total = s.moves_target_total
    pending: list[c.Event] = []
    rows = []
    for day in list(sc.day_range)[:days]:
        events = [e for e in cal if e.day == day] + [e for e in pending if e.day == day]
        dec = policy.decide(day, twin.view(s), c.visible(events, day), twin, s)
        s, _ = twin.apply_levers(s, dec.levers, day)
        s, r = twin.step_day(s, day, twin.actual_demand(day), c.active(events, day))
        pending += twin.consequences(s, r, run_id)
        fork = [h["pools"]["forklift"] for h in r.hourly["hours"]]
        util = sum(h["busy"] for h in fork) / max(sum(h["available"] for h in fork), 1)
        rows.append({"day": day, "events": [e.kind for e in events if e.day == day], "fork_util": util, **r.kpis.model_dump()})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--url", default=None)
    args = ap.parse_args()
    from eval.baselines import DoNothing, NaiveBigBang

    twin = Twin(args.url)
    sc = load_scenario("reslot_default")
    event_days = {e.day + k for e in sc.events for k in range(int(e.args.get("days", 1)))}
    dn = _loop(twin, sc, DoNothing("dn"), args.days)
    normal = [r for r in dn if r["day"] not in event_days]
    shortage = [r for r in dn if r["day"] in (63, 64)]
    print("== do-nothing (normal days = no scripted event active)")
    print(f"1. forklift utilization   : {mean(r['fork_util'] for r in normal):.2f}  (target 0.80-0.95)")
    print(f"2. PM peak zone load      : {mean(r['pm_peak_zone_load'] for r in normal):.2f}  (target 0.85-1.00)")
    print(f"3. on-time normal days    : {mean(r['on_time_pct'] for r in normal):.1f}%  (target 94-97);"
          f" labor-shortage days: {mean(r['on_time_pct'] for r in shortage) if shortage else float('nan'):.1f}% (target < 90)")
    print(f"   mispick {mean(r['mispick_rate'] for r in normal):.3f}%  jam h/day {mean(r['jam_hours'] for r in normal):.1f}"
          f"  replen trips {mean(r['replen_trips'] for r in normal):.0f}  stockout lines {mean(r['stockout_lines'] for r in normal):.0f}"
          f"  near-misses/day {mean(r['near_misses'] for r in dn):.2f}")
    orc = _loop(twin, sc, DoNothing("or"), args.days, oracle=True)
    lift = (mean(r["lph"] for r in orc) / mean(r["lph"] for r in dn) - 1) * 100
    print(f"4. oracle LPH lift        : {lift:+.1f}%  (target +15-30)  lph do-nothing {mean(r['lph'] for r in dn):.1f} oracle {mean(r['lph'] for r in orc):.1f}")
    bb = _loop(twin, sc, NaiveBigBang("bb"), min(args.days, 6))
    print(f"5. big bang               : jam hours {[r['jam_hours'] for r in bb]}  mispick {[round(r['mispick_rate'], 2) for r in bb]}"
          f"  staging max {[r['staging_occupancy_max'] for r in bb]}  near-misses {[r['near_misses'] for r in bb]}"
          f"  moves {[r['moves_done'] for r in bb]}")
    print("   (target: >= 1 jam hour, mis-picks > 1% for 2 days, staging overflow)")


if __name__ == "__main__":
    main()
