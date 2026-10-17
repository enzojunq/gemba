"""`apply_levers(state, levers, day)`: physical validation and state mutation (CONTRACT 3.2/3.3).

Every SKU move, overtime hour and staging slot carries the lever's `decision_id`.
"""

from __future__ import annotations

import contracts as c
from floor import constants as K
from floor import phaser
from floor.inputs import Inputs
from floor.model import FloorState

POOLS: tuple[str, ...] = c.Pool.__args__  # type: ignore[attr-defined]


def invalid_reason(inputs: Inputs, state: FloorState, lv: c.Lever) -> str | None:
    """Physical invalidity only: unknown aisle/zone/SKU, headcount above 2x roster, temps > 8."""
    if lv.kind in ("close_aisle", "reopen_aisle") and not 1 <= lv.aisle <= K.N_AISLES:
        return f"unknown aisle {lv.aisle} (1-{K.N_AISLES})"
    zones: list = []
    if lv.kind == "reslot_tonight":
        zones = list(lv.include_zones) + list(lv.exclude_zones)
    elif lv.kind == "protect" and lv.target == "zone":
        zones = list(lv.ids)
    bad = [z for z in zones if not (str(z).isdigit() and 1 <= int(z) <= K.N_ZONES)]
    if bad:
        return f"unknown zone(s) {bad} (1-{K.N_ZONES})"
    if lv.kind in ("protect", "resize_slots"):
        ids = lv.ids if lv.kind == "protect" and lv.target == "skus" else (lv.skus if lv.kind == "resize_slots" else [])
        unknown = [s for s in ids if s not in inputs.sku_index]
        if unknown:
            return f"unknown sku(s) {unknown[:5]}"
    if lv.kind == "protect" and lv.target == "customer":
        known = set(inputs.customers["customer_id"].tolist()) if len(inputs.customers) else set()
        unknown = [x for x in lv.ids if known and x not in known]
        if unknown:
            return f"unknown customer(s) {unknown[:5]}"
    if lv.kind == "labor_allocate":
        roster = inputs.headcount.get(lv.shift, {}).get(lv.pool, 0)
        if lv.headcount > max(2 * roster, 2):
            return f"headcount {lv.headcount} above 2x roster ({roster}) for {lv.shift}/{lv.pool}"
    if lv.kind == "hire_temps":
        active = max((state.temps_active(d) for d in range(lv.start_day, lv.start_day + lv.days)), default=0)
        if lv.count + active > K.TEMP_POOL_MAX:
            return f"temp pool allows at most {K.TEMP_POOL_MAX} ({active} already active)"
    if lv.kind == "staging_clearance" and lv.n_movers > 2 * max(inputs.headcount.get("night", {}).get("night_move", 4), 1):
        return "more movers than the night crew allows"
    return None


def apply_levers(inputs: Inputs, state: FloorState, levers: list[c.Lever], day: int
                 ) -> tuple[FloorState, list[c.LeverRejection]]:
    s = state.copy()
    rejections: list[c.LeverRejection] = []
    for lv in levers:
        reason = invalid_reason(inputs, s, lv)
        if reason:
            rejections.append(c.LeverRejection(lever=lv, reason=reason))
            continue
        match lv.kind:
            case "reslot_tonight":
                if s.paused_until >= day:
                    continue
                _approve_tonight(inputs, s, lv, day)
            case "pause_reslot":
                s.paused_until = day + lv.days - 1
                for m in s.moves:
                    if m.approved and m.legs_done == 0:
                        m.approved = False
            case "close_aisle":
                s.closed_aisles[lv.aisle] = (day + lv.days - 1, c.node_id(c.DEC, lv.decision_id) if lv.decision_id else None)
            case "reopen_aisle":
                s.closed_aisles.pop(lv.aisle, None)
            case "protect":
                s.protections.append(c.Protection(target=lv.target, ids=[str(i) for i in lv.ids],
                                                  until_day=day + lv.days - 1, decision_id=lv.decision_id))
            case "overtime":
                prev = s.overtime_today.get((lv.shift, lv.pool), (0.0, ""))[0]
                s.overtime_today[(lv.shift, lv.pool)] = (prev + float(lv.hours), lv.decision_id)
            case "hire_temps":
                s.temps.append((lv.count, lv.start_day, lv.start_day + lv.days - 1, lv.decision_id))
            case "labor_allocate":
                s.labor.setdefault(lv.shift, {})[lv.pool] = int(lv.headcount)
            case "staging_clearance":
                s.staging_movers = int(lv.n_movers)
                for m in s.moves:
                    if m.legs_done > 0 and m.legs_done < m.legs:
                        m.approved = True
                        m.seq = -1
            case "resize_slots":
                units = inputs.sku_daily_units(range(max(1, day - 14), day))
                for sid in lv.skus:
                    i = inputs.sku_index[sid]
                    s.sku_cap[i] = max(10.0, round(float(units[i]) * lv.days_cover))
                    s.inventory[i] = min(float(s.inventory[i]), float(s.sku_cap[i]))
            case "wave_priority":
                s.wave_mode = lv.mode
    return s, rejections


def _approve_tonight(inputs: Inputs, s: FloorState, lv: c.ReslotTonight, day: int) -> None:
    protect: set[str] = set()
    sku_ids = inputs.skus["sku_id"]
    for p in s.protections_active(day):
        if p.target == "skus":
            protect.update(p.ids)
        elif p.target == "customer":
            protect.update(_customer_skus(inputs, p.ids, day))
    pz = [int(z) for p in s.protections_active(day) if p.target == "zone" for z in p.ids if str(z).isdigit()]
    exclude = list(lv.exclude_zones) + pz
    ranked = phaser.rank_moves(inputs, s)
    frozen = s.frozen_now(day) + s.closed_now(day)
    chosen = phaser.select_tonight(ranked, lv.n_moves, lv.include_zones, exclude, protect, frozen, lv.order, inputs)
    seq0 = max((m.seq for m in s.moves), default=0) + 1
    for i, r in enumerate(chosen):
        m = r["_move"]
        m.approved = True
        m.decision_id = lv.decision_id or m.decision_id
        m.seq = seq0 + i
    del sku_ids


def _customer_skus(inputs: Inputs, customers: list[str], day: int) -> set[str]:
    """SKUs the customers ordered in the 14 days before `day` (history only)."""
    out: set[str] = set()
    for d in range(max(1, day - 14), day):
        if d not in inputs.day_slices:
            continue
        so, sl = inputs.day_slices[d]
        orders = inputs.orders.iloc[so]
        oids = set(orders.loc[orders["customer_id"].isin(customers), "order_id"])
        if oids:
            lines = inputs.lines.iloc[sl]
            out.update(lines.loc[lines["order_id"].isin(oids), "sku_id"].tolist())
    return out
