"""Consequence engine C1-C8 (spec 9.4). Runs after `step_day`; emits future-dated events with `caused_by`.

C1 (learning dip) and C8 (temp ramp) are pure state, applied inside `floor/sim.py`.
"""

from __future__ import annotations

import contracts as c
from floor import constants as K
from floor.model import FloorState


def consequences(state: FloorState, result: c.DayResult, run_id: str) -> list[c.Event]:
    d = result.day
    h = {x["day"]: x for x in state.kpi_history}
    out: list[c.Event] = []

    def ev(rule: str, kind: str, args: dict, caused_by: str | None, day: int = d + 1) -> c.Event:
        return c.Event(event_id=f"{rule}-{run_id}-{day}", kind=kind, day=day, args=args,
                       announced_day=c.announced_day(kind, day), source="consequence", caused_by=caused_by)

    def streak2(key: str, bad) -> bool:
        """Bad today and yesterday but not the day before: fire once per streak."""
        return all(i in h and bad(h[i][key]) for i in (d, d - 1)) and not (d - 2 in h and bad(h[d - 2][key]))

    # C2: key customer mis-picks > 0.8% for 2 consecutive days -> customer_escalation next day
    if state.key_customer and streak2("key_mispick", lambda v: v > K.C2_KEY_MISPICK_PCT):
        dec = h[d].get("dip_decision") or h[d - 1].get("dip_decision")
        out.append(ev("c2", "customer_escalation", {"customer": state.key_customer},
                      c.node_id(c.DEC, dec) if dec else c.kpi_node_id(run_id, d, "mispick_rate_by_customer")))

    # C3: a pool worked overtime on 3 of the last 4 days -> absenteeism +15% for 3 days
    pools = sorted({p for _, p, _, _ in state.overtime_history})
    for pool in pools:
        days = {dd for dd, p, hrs, _ in state.overtime_history if p == pool and hrs > 0}
        last4 = lambda end: sum(1 for x in days if end - 4 < x <= end)  # noqa: E731
        if d in days and last4(d) >= 3 and last4(d - 1) < 3:
            dec = next((dec for dd, p, hrs, dec in reversed(state.overtime_history) if p == pool and dec), None)
            out.append(ev(f"c3{pool}", "absenteeism", {"pool": pool, "pct": 15, "days": 3},
                          c.node_id(c.DEC, dec) if dec else c.kpi_node_id(run_id, d, "overtime_hours")))

    # C4: staging > 90% at 06:00 -> staging_congestion (attributed to the reslot decisions holding staging)
    occ = state.staging_pct()
    prev_occ = h[d - 1]["staging_0600"] if d - 1 in h else 0.0
    if occ > K.C4_STAGING and prev_occ <= K.C4_STAGING:
        decs = [dec for _, dec in state.staging.values() if dec]
        cause = c.node_id(c.DEC, max(set(decs), key=decs.count)) if decs else c.kpi_node_id(run_id, d, "staging_occupancy_max")
        out.append(ev("c4", "staging_congestion", {"occupancy": round(occ, 1)}, cause))

    # C5: near-miss in an aisle -> safety_review; a second one within 5 days -> aisle_closure 3 days
    today_nm = [(dd, a, cause) for dd, a, cause in state.near_misses if dd == d]
    seen: set[int] = set()
    for dd, a, cause in today_nm:
        if a in seen:
            continue
        seen.add(a)
        cb = cause or c.kpi_node_id(run_id, d, "near_misses")
        earlier = [x for x in state.near_misses if x[1] == a and d - K.C5_WINDOW_DAYS <= x[0] < d]
        if earlier:
            out.append(ev(f"c5close{a}", "aisle_closure", {"aisle": a, "days": 3}, cb))
        else:
            out.append(ev(f"c5{a}", "safety_review", {"aisle": a}, cb))

    # C6: on-time below target - 2 for 2 consecutive days -> director_escalation
    if streak2("on_time", lambda v: v < K.C6_ON_TIME):
        out.append(ev("c6", "director_escalation", {"metric": "on_time_pct"}, c.kpi_node_id(run_id, d, "on_time_pct")))

    # C7: stockout lines > 1% of lines -> replen_alert
    if streak2("stockout_pct", lambda v: v > K.C7_STOCKOUT_PCT) or (
            d in h and h[d]["stockout_pct"] > K.C7_STOCKOUT_PCT and not (d - 1 in h and h[d - 1]["stockout_pct"] > K.C7_STOCKOUT_PCT)):
        if not any(e.kind == "replen_alert" for e in out):
            out.append(ev("c7", "replen_alert", {"stockout_pct": round(h[d]["stockout_pct"], 2)},
                          c.kpi_node_id(run_id, d, "stockout_lines")))
    return out
