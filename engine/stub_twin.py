"""StubTwin: a tiny deterministic `contracts.FloorTwin` so the engine and org can run before
A's real twin lands (CONTRACT section 9). Formulas are crude but monotone so tests can
assert direction. Never reads FloorState from outside this module.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from statistics import mean

import pandas as pd

import contracts as c

POOLS: tuple[str, ...] = c.Pool.__args__  # type: ignore[attr-defined]
SHIFTS: tuple[str, ...] = c.Shift.__args__  # type: ignore[attr-defined]
PICK_DEFAULTS = {"pm": 12, "forklift": 14, "replen": 4, "pallet": 3, "consolidation": 10, "loading": 6,
                 "night_move": 0, "temp": 0}
DEFAULT_LABOR: dict[str, dict[str, int]] = {
    "day": dict(PICK_DEFAULTS), "swing": dict(PICK_DEFAULTS),
    "night": {p: (4 if p == "night_move" else 0) for p in POOLS},
}
MOVES_TARGET = 2400
MAX_LEGS_PER_NIGHT = 280
TEMP_POOL_MAX = 8
KEY_CUSTOMER = "C001"  # same id as the key customer in data/gen.py, so scenarios work on both twins
ON_TIME_TARGET = 95.0
"""C6 escalation threshold. Spec 9.4 says "< profile target" (97), but 9.3a calibrates normal
days at 94–97%, which would escalate the Director almost daily. Stub uses target − 2, the same
slack as the daily done-check; flagged in PROGRESS_B.md for the real twin and the spec."""
N_AISLES, N_ZONES = 28, 6


@dataclass
class StubState:
    seed: int
    day: int
    closed_aisles: dict[int, int] = field(default_factory=dict)  # aisle -> last closed day (inclusive)
    protections: list[c.Protection] = field(default_factory=list)
    move_queue: int = MOVES_TARGET  # legs still to move
    moves_done: int = 0
    tonight: int = 0
    tonight_decision: str = ""
    nights: dict[int, tuple[int, str]] = field(default_factory=dict)  # night day -> (moves, decision_id)
    staging_occupancy: float = 40.0
    labor: dict[str, dict[str, int]] = field(default_factory=lambda: copy.deepcopy(DEFAULT_LABOR))
    overtime_today: dict[str, float] = field(default_factory=dict)
    overtime_days: dict[str, list[int]] = field(default_factory=dict)  # pool -> days worked overtime
    overtime_decision: dict[str, str] = field(default_factory=dict)  # pool -> last approving decision
    temps: list[tuple[int, int, int]] = field(default_factory=list)  # (count, start_day, end_day)
    learning_dip_until: int = 0
    paused_until: int = 0
    shortage_until: int = 0
    surge_until: int = 0
    surge_factor: float = 1.0
    history: list[dict] = field(default_factory=list)  # per stepped day: day, on_time, key_mispick


def aggregate_kpis(days: list[c.Kpis]) -> c.Kpis:
    """Aggregate per `contracts.WhatIf` docstring (mean/sum/max/end-of-horizon)."""
    if not days:
        return c.Kpis()
    col = lambda k: [getattr(d, k) for d in days]  # noqa: E731
    out: dict = {}
    for k in ("on_time_pct", "mispick_rate", "lph", "throughput_lph_site"):
        out[k] = mean(col(k))
    for k in ("lines", "labor_cost_usd", "overtime_hours", "replen_trips", "stockout_lines", "jam_hours",
              "moves_done", "near_misses"):
        out[k] = sum(col(k))
    for k in ("consolidator_queue_max", "pm_peak_zone_load", "staging_occupancy_max", "safety_risk_max"):
        out[k] = max(col(k))
    for k in ("reslot_progress_pct", "moves_queued"):
        out[k] = getattr(days[-1], k)
    custs = sorted({k for d in days for k in d.mispick_rate_by_customer})
    out["mispick_rate_by_customer"] = {
        cu: mean(d.mispick_rate_by_customer.get(cu, 0.0) for d in days) for cu in custs}
    return c.Kpis(**out)


class StubTwin:
    def __init__(self, seed: int = 7):
        self.seed = seed

    # ------------------------------------------------------------------ protocol
    def initial_state(self, scenario: c.Scenario) -> StubState:
        self.seed = scenario.seed
        return StubState(seed=scenario.seed, day=scenario.days[0])

    def view(self, state: StubState) -> c.FloorView:
        d = state.day
        return c.FloorView(
            day=d, frozen_aisles=[],
            closed_aisles=sorted(a for a, until in state.closed_aisles.items() if until >= d),
            protected=[p for p in state.protections if p.until_day >= d],
            staging_occupancy=state.staging_occupancy, move_queue=state.move_queue,
            moves_done_total=state.moves_done, moves_target_total=MOVES_TARGET,
            reslot_progress_pct=state.moves_done / MOVES_TARGET * 100,
            labor_available=copy.deepcopy(state.labor), temps_active=self._temps(state, d),
            learning_dip_skus=self._dip_moves(state, d),
            overtime_days_last4={p: sum(1 for x in state.overtime_days.get(p, []) if d - 4 <= x < d) for p in POOLS},
        )

    def apply_levers(self, state: StubState, levers: list[c.Lever], day: int) -> tuple[StubState, list[c.LeverRejection]]:
        s = copy.deepcopy(state)
        rejections: list[c.LeverRejection] = []
        for lv in levers:
            reason = self._invalid(lv)
            if reason:
                rejections.append(c.LeverRejection(lever=lv, reason=reason))
                continue
            match lv.kind:
                case "reslot_tonight":
                    if s.paused_until < day:
                        s.tonight = min(lv.n_moves, MAX_LEGS_PER_NIGHT, s.move_queue)
                        s.tonight_decision = lv.decision_id
                case "pause_reslot":
                    s.paused_until = day + lv.days - 1
                    s.tonight = 0
                case "close_aisle":
                    s.closed_aisles[lv.aisle] = day + lv.days - 1
                case "reopen_aisle":
                    s.closed_aisles.pop(lv.aisle, None)
                case "protect":
                    s.protections.append(c.Protection(target=lv.target, ids=lv.ids, until_day=day + lv.days - 1,
                                                      decision_id=lv.decision_id))
                case "overtime":
                    s.overtime_today[lv.pool] = s.overtime_today.get(lv.pool, 0.0) + lv.hours
                    s.overtime_decision[lv.pool] = lv.decision_id
                case "hire_temps":
                    s.temps.append((lv.count, lv.start_day, lv.start_day + lv.days - 1))
                case "labor_allocate":
                    s.labor[lv.shift][lv.pool] = lv.headcount
                case "staging_clearance":
                    s.staging_occupancy = max(20.0, s.staging_occupancy - 10.0 * lv.n_movers)
        return s, rejections

    def actual_demand(self, day: int) -> c.DayDemand:
        return self._demand(day, n_orders=20, salt="actual")

    def step_day(self, state: StubState, day: int, demand: c.DayDemand, events: list[c.Event]) -> tuple[StubState, c.DayResult]:
        return self._step(state, day, demand, events, expected=False)

    def consequences(self, state: StubState, result: c.DayResult, run_id: str) -> list[c.Event]:
        d, h = result.day, {x["day"]: x for x in state.history}
        out: list[c.Event] = []

        def ev(rule: str, kind: str, args: dict, caused_by: str | None) -> c.Event:
            return c.Event(event_id=f"{rule}-{run_id}-{d + 1}", kind=kind, day=d + 1, args=args,
                           announced_day=c.announced_day(kind, d + 1), source="consequence", caused_by=caused_by)

        def streak2(key: str, bad) -> bool:  # bad today and yesterday, but not the day before (fire once)
            return all(i in h and bad(h[i][key]) for i in (d, d - 1)) and not (d - 2 in h and bad(h[d - 2][key]))

        # C1 (learning dip) is pure state, applied in _step.
        if streak2("key_mispick", lambda v: v > 0.8):  # C2
            dec = h[d].get("dip_decision") or h[d - 1].get("dip_decision")
            out.append(ev("c2", "customer_escalation", {"customer": KEY_CUSTOMER},
                          c.node_id(c.DEC, dec) if dec else c.kpi_node_id(run_id, d, "mispick_rate_by_customer")))
        for pool, days in sorted(state.overtime_days.items()):  # C3
            last4 = lambda end: sum(1 for x in days if end - 4 < x <= end)  # noqa: E731
            if d in days and last4(d) >= 3 and last4(d - 1) < 3:
                dec = state.overtime_decision.get(pool)
                out.append(ev(f"c3{pool}", "absenteeism", {"pool": pool, "pct": 15, "days": 3},
                              c.node_id(c.DEC, dec) if dec else None))
        if streak2("on_time", lambda v: v < ON_TIME_TARGET):  # C6
            out.append(ev("c6", "director_escalation", {"metric": "on_time_pct"},
                          c.kpi_node_id(run_id, d, "on_time_pct")))
        return out

    def forecast_demand(self, state: StubState, day: int, horizon: int, known_events: list[c.Event]) -> list[c.DayDemand]:
        out = []
        for d in range(day, day + horizon):
            factor = max([e.args.get("factor", 1.0) for e in c.visible(known_events, state.day)
                          if e.kind == "demand_surge" and e.day <= d < e.day + e.args.get("days", 1)] or [1.0])
            out.append(self._demand(d, n_orders=round(20 * factor), salt="forecast"))
        return out

    def whatif(self, state: StubState, levers: list[c.Lever], days: int, known_events: list[c.Event]) -> c.WhatIf:
        """Levers apply on the first day only (durations persist); forecast demand only."""
        known = c.visible(known_events, state.day)
        s, _ = self.apply_levers(state, levers, state.day)
        by_day: list[c.Kpis] = []
        for dem in self.forecast_demand(s, state.day, days, known):
            s, r = self._step(s, dem.day, dem, c.active(known, dem.day), expected=True)
            by_day.append(r.kpis)
        total = aggregate_kpis(by_day)
        return c.WhatIf(levers=levers, days=days, kpis_by_day=by_day, kpis_total=total,
                        expected_near_misses=total.near_misses, safety_risk_max=total.safety_risk_max,
                        pm_peak_zone_load=total.pm_peak_zone_load)

    def recalibrate(self, state: StubState, actual: list[c.DayResult], predicted: list[c.Kpis]) -> StubState:
        return state

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _invalid(lv: c.Lever) -> str | None:
        if lv.kind in ("close_aisle", "reopen_aisle") and not 1 <= lv.aisle <= N_AISLES:
            return f"unknown aisle {lv.aisle} (1-{N_AISLES})"
        zones: list = []
        if lv.kind == "reslot_tonight":
            zones = lv.include_zones + lv.exclude_zones
        elif lv.kind == "protect" and lv.target == "zone":
            zones = lv.ids
        bad = [z for z in zones if not (str(z).isdigit() and 1 <= int(z) <= N_ZONES)]
        if bad:
            return f"unknown zone(s) {bad} (1-{N_ZONES})"
        if lv.kind == "labor_allocate" and lv.headcount > 2 * DEFAULT_LABOR[lv.shift][lv.pool]:
            return f"headcount {lv.headcount} above 2x default for {lv.shift}/{lv.pool}"
        if lv.kind == "hire_temps" and lv.count > TEMP_POOL_MAX:
            return f"temp pool allows at most {TEMP_POOL_MAX}"
        return None

    @staticmethod
    def _temps(s: StubState, day: int) -> int:
        return min(TEMP_POOL_MAX, sum(n for n, a, b in s.temps if a <= day <= b))

    @staticmethod
    def _dip_night(s: StubState, day: int) -> tuple[int, str]:
        """C1: moves from the last two nights put those SKUs in a learning dip."""
        nights = [s.nights[n] for n in (day - 1, day - 2) if n in s.nights]
        return max(nights, default=(0, ""))

    def _dip_moves(self, s: StubState, day: int) -> int:
        return self._dip_night(s, day)[0]

    def _demand(self, day: int, n_orders: int, salt: str) -> c.DayDemand:
        rng = random.Random(f"{self.seed}:{salt}:{day}")
        orders = pd.DataFrame({
            "order_id": [f"o{day}-{i}" for i in range(n_orders)],
            "release_hour": [6 + i % 12 for i in range(n_orders)],
            "customer_id": [KEY_CUSTOMER if i % 4 == 0 else f"C{i % 7}" for i in range(n_orders)],
            "priority": ["rush" if i % 10 == 0 else "normal" for i in range(n_orders)],
            "cutoff_hour": [20] * n_orders,
        })
        lines = pd.DataFrame([{"order_id": o, "line_no": j, "sku_id": f"S{rng.randrange(500):04d}",
                               "qty": rng.randint(1, 5)} for o in orders.order_id for j in range(3)])
        return c.DayDemand(day=day, orders=orders, lines=lines)

    def _step(self, state: StubState, day: int, demand: c.DayDemand, events: list[c.Event], expected: bool):
        s = copy.deepcopy(state)
        for e in events:  # multi-day events arrive once, on their start day; hold the effect
            n = int(e.args.get("days", 1))
            if e.kind in ("labor_shortage", "absenteeism"):
                s.shortage_until = max(s.shortage_until, day + n - 1)
            elif e.kind == "rack_damage" and "aisle" in e.args:
                s.closed_aisles[int(e.args["aisle"])] = max(s.closed_aisles.get(int(e.args["aisle"]), 0), day + n - 1)
            elif e.kind == "demand_surge":
                s.surge_until, s.surge_factor = day + n - 1, float(e.args.get("factor", 1.2))

        closed = [a for a, until in s.closed_aisles.items() if until >= day]
        dip_moves, dip_dec = self._dip_night(s, day)
        temps = self._temps(s, day)
        ot_hours = sum(s.overtime_today.values())
        shortage, surge = s.shortage_until >= day, s.surge_until >= day

        on_time = 95.5 - 0.3 * dip_moves / 100 - (6 if shortage else 0) - (2 if surge else 0)
        on_time += min(2.0, ot_hours / 8 * 0.5) + 0.25 * temps
        lph = 100.0 - (10 if shortage else 0) - 3 * len(closed) - 0.7 * temps
        mispick = 0.4 * 1.15 ** (dip_moves / 100) * (1 + 0.05 * temps)
        key_protected = any(p.target == "customer" and KEY_CUSTOMER in p.ids and p.until_day >= day
                            for p in s.protections)
        key_mispick = 0.4 * (1 + 0.05 * temps) if key_protected else mispick * 1.5
        lines = len(demand.lines) * 100
        headcount = sum(sum(v.values()) for v in s.labor.values())
        staging_start = s.staging_occupancy

        # night (22:00-06:00): execute tonight's moves
        moved = min(s.tonight, s.move_queue)
        if moved:
            s.nights[day] = (moved, s.tonight_decision)
            s.learning_dip_until = day + 2
        s.moves_done += moved
        s.move_queue -= moved
        s.staging_occupancy = min(100.0, 0.5 * s.staging_occupancy + 20 + 0.1 * moved)
        for pool, hrs in s.overtime_today.items():
            if hrs > 0:
                s.overtime_days.setdefault(pool, []).append(day)
        staging_max = max(staging_start, s.staging_occupancy)
        exp_nm = 0.1 * len(closed) + (0.2 if staging_max > 90 else 0.0)
        near = exp_nm if expected else float(random.Random(s.seed * 1000 + day).random() < exp_nm)

        kpis = c.Kpis(
            lines=lines, lph=round(lph, 3), throughput_lph_site=round(lines / 16, 3), on_time_pct=round(on_time, 3),
            mispick_rate=round(mispick, 4), pm_peak_zone_load=0.85 + (0.1 if surge else 0.0),
            consolidator_queue_max=10.0 + (5 if surge else 0), jam_hours=0, replen_trips=40, stockout_lines=0,
            overtime_hours=ot_hours, labor_cost_usd=round(headcount * 8 * 22 + temps * 8 * 20 + ot_hours * 33, 2),
            moves_done=moved, moves_queued=s.move_queue, reslot_progress_pct=s.moves_done / MOVES_TARGET * 100,
            staging_occupancy_max=staging_max, safety_risk_max=round(0.6 + 0.1 * len(closed) + (0.3 if staging_max > 90 else 0), 3),
            near_misses=near, mispick_rate_by_customer={KEY_CUSTOMER: round(key_mispick, 4), "OTHER": round(mispick, 4)},
        )
        s.history.append({"day": day, "on_time": kpis.on_time_pct, "key_mispick": key_mispick,
                          "dip_decision": dip_dec or None})
        s.tonight, s.tonight_decision, s.overtime_today = 0, "", {}
        s.day = day + 1
        return s, c.DayResult(day=day, kpis=kpis, hourly={}, violations=[])
