"""`Twin`: the deterministic floor twin implementing `contracts.FloorTwin` (CONTRACT section 4)."""

from __future__ import annotations

import copy
import os
from statistics import mean

import numpy as np

import contracts as c
from floor import constants as K
from floor import consequences as cons
from floor import events as ev
from floor import forecast as fc
from floor import levers as lv
from floor import phaser, sim
from floor.inputs import Inputs, load_inputs
from floor.model import ActiveEffect, FloorState
from floor.perturb import Perturbation, scale_lines

POOLS: tuple[str, ...] = c.Pool.__args__  # type: ignore[attr-defined]
SHIFTS: tuple[str, ...] = c.Shift.__args__  # type: ignore[attr-defined]


def aggregate_kpis(days: list[c.Kpis]) -> c.Kpis:
    """Aggregate per the `contracts.WhatIf.kpis_total` docstring (mean / sum / max / end-of-horizon)."""
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
    out["mispick_rate_by_customer"] = {cu: mean(d.mispick_rate_by_customer.get(cu, 0.0) for d in days) for cu in custs}
    return c.Kpis(**out)


class Twin:
    """Deterministic floor twin over `inputs.db`. One instance loads the inputs once."""

    def __init__(self, inputs_url: str | None = None, inputs: Inputs | None = None):
        self.inputs: Inputs = inputs if inputs is not None else load_inputs(inputs_url or os.environ.get("INPUTS_DB_URL"))
        self.seed = 1
        self.pert: Perturbation | None = None

    # ------------------------------------------------------------------ protocol
    def initial_state(self, scenario: c.Scenario) -> FloorState:
        inp = self.inputs
        self.seed = scenario.seed
        n = len(inp.sku_index)
        labor = {s: dict(p) for s, p in inp.headcount.items()}
        for s in SHIFTS:
            labor.setdefault(s, {p: 0 for p in POOLS})
            for p in POOLS:
                labor[s].setdefault(p, 0)
        for shift, pools in (scenario.overrides.get("headcounts") or {}).items():  # partial, merged over the roster
            for p, n_p in (pools or {}).items():
                if shift in labor and p in labor[shift]:
                    labor[shift][p] = int(n_p)
        st = FloorState(seed=scenario.seed, day=scenario.days[0], scenario_id=scenario.id,
                        sku_loc=inp.current_loc.astype(np.int32).copy(), sku_cap=inp.current_cap.astype(float).copy(),
                        inventory=inp.current_cap.astype(float).copy(), labor=labor,
                        dip_day=np.zeros(n, dtype=np.int32), dip_mult=np.ones(n, dtype=float), key_customer=(inp.key_customers[0] if inp.key_customers else ""))
        st.loc_occupant = {int(l): i for i, l in enumerate(st.sku_loc) if l >= 0}
        budget = (scenario.overrides.get("budgets") or {}).get("legs_per_night")
        st.legs_per_night = int(budget) if budget is not None else None
        velocity = inp.velocity_hist if inp.velocity_hist is not None else inp.sku_velocity(range(1, 61))
        plan = phaser.build_plan(inp, st, inp.target_loc, velocity)
        plan.sort(key=lambda m: (-(m.value / m.legs), m.sku))
        st.moves = plan
        st.moves_target_total = sum(m.legs for m in plan)
        self.pert = Perturbation.build(inp, scenario.seed, scenario.perturbation)
        if self.pert.surge_shift_days:
            st.surge_shift = self.pert.surge_shift_days
            for i, e in enumerate(scenario.events):
                if e.kind == "demand_surge":
                    d0 = e.day + st.surge_shift
                    st.effects.append(ActiveEffect(kind="demand_surge", event_id=f"shifted-surge-{i}", start_day=d0,
                                                   until_day=d0 + int(e.args.get("days", 1)) - 1, args=dict(e.args), start_hour=e.hour))
        return st

    def view(self, state: FloorState) -> c.FloorView:
        d = state.day
        labor = {s: {p: int(state.labor.get(s, {}).get(p, 0)) for p in POOLS} for s in SHIFTS}
        hc = sim._headcount(state, d)
        for s in SHIFTS:
            for p in POOLS:
                labor[s][p] = int(round(hc[s][p]))
        ot_days = {p: len({dd for dd, pp, hrs, _ in state.overtime_history if pp == p and hrs > 0 and d - 4 <= dd < d})
                   for p in POOLS}
        queue = sum(m.legs - m.legs_done for m in state.moves if m.legs_done < m.legs)
        return c.FloorView(
            day=d, frozen_aisles=state.frozen_now(d), closed_aisles=state.closed_now(d),
            protected=state.protections_active(d), staging_occupancy=round(state.staging_pct(), 2),
            move_queue=int(queue), moves_done_total=int(state.moves_done_total),
            moves_target_total=int(state.moves_target_total),
            reslot_progress_pct=state.progress_pct(),
            labor_available=labor, temps_active=state.temps_active(d),
            learning_dip_skus=int(state.dip_mask(d).sum()), overtime_days_last4=ot_days,
        )

    def apply_levers(self, state: FloorState, levers: list[c.Lever], day: int) -> tuple[FloorState, list[c.LeverRejection]]:
        return lv.apply_levers(self.inputs, state, levers, day)

    def actual_demand(self, day: int) -> c.DayDemand:
        return self.perturb(self.inputs.demand(day), day)

    def perturb(self, demand: c.DayDemand, source_day: int) -> c.DayDemand:
        """Apply the scenario perturbation to lines taken from `source_day` (no-op when empty)."""
        if self.pert is None or self.pert.empty or len(demand.lines) == 0:
            return demand
        f = self.pert.factor_for(source_day)
        idx = demand.lines["sku_id"].map(self.inputs.sku_index).fillna(-1).to_numpy(dtype=np.int64)
        per_line = np.where(idx >= 0, f[np.where(idx >= 0, idx, 0)], 1.0)
        lines = scale_lines(demand.lines, per_line, self.pert.seed, demand.day, 13, sim.order_codes(demand.lines["order_id"]))
        return c.DayDemand(day=demand.day, orders=demand.orders, lines=lines)

    def step_day(self, state: FloorState, day: int, demand: c.DayDemand, events: list[c.Event]) -> tuple[FloorState, c.DayResult]:
        s = state.copy()
        ev.apply_events(s, events, day)
        result = sim.simulate_day(self.inputs, s, day, demand, expected=False)
        return s, result

    def consequences(self, state: FloorState, result: c.DayResult, run_id: str) -> list[c.Event]:
        return cons.consequences(state, result, run_id)

    def forecast_demand(self, state: FloorState, day: int, horizon: int, known_events: list[c.Event]) -> list[c.DayDemand]:
        known = known_events
        if state.surge_shift:
            known = [e.model_copy(update={"day": e.day + state.surge_shift}) if e.kind == "demand_surge" and e.source == "scripted" else e
                     for e in known_events]
        return fc.forecast_demand(self.inputs, state, day, horizon, known, perturb=self.perturb if self.pert and not self.pert.empty else None)

    def whatif(self, state: FloorState, levers: list[c.Lever], days: int, known_events: list[c.Event]) -> c.WhatIf:
        """Levers apply on the first day of the horizon; forecast demand only; expected values, no draws."""
        day0 = state.day
        known = c.visible(known_events, day0)
        s, _ = self.apply_levers(state, levers, day0)
        by_day: list[c.Kpis] = []
        for dem in self.forecast_demand(s, day0, days, known):
            s = s.copy()
            ev.apply_events(s, c.active(known, dem.day), dem.day)
            r = sim.simulate_day(self.inputs, s, dem.day, dem, expected=True)
            by_day.append(r.kpis)
        total = aggregate_kpis(by_day)
        return c.WhatIf(levers=levers, days=days, kpis_by_day=by_day, kpis_total=total,
                        expected_near_misses=total.near_misses, safety_risk_max=total.safety_risk_max,
                        pm_peak_zone_load=total.pm_peak_zone_load)

    def recalibrate(self, state: FloorState, actual: list[c.DayResult], predicted: list[c.Kpis]) -> FloorState:
        return fc.recalibrate(self.inputs, state, actual, predicted)


def make_twin(inputs_url: str | None = None) -> Twin:
    """Factory used by B's `engine.run.make_twin("real")`. Loads inputs.db once."""
    return Twin(inputs_url)
