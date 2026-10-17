"""`apply_levers(state, levers, day)`: physical validation and state mutation (CONTRACT 3.2/3.3).

Every SKU move, overtime hour and staging slot carries the lever's `decision_id`.

Lever semantics (spec 10.3), as the twin applies them on `day`:
- `reslot_tonight(n_moves)`: approve up to `n_moves` legs for tonight from the ranked plan; unfinished
  approved legs carry to the next night under the same decision.
- `pause_reslot(days)`: nothing is approved for `days` nights and not-yet-started approvals are dropped.
  Note the do-nothing reference `whatif(state, [], ...)` is "no new decision": legs already approved
  still run that night; `pause_reslot` additionally halts them.
- `labor_allocate(shift, pool, headcount)`: `headcount` is the ABSOLUTE total for that pool on that
  shift (not a delta), the same number `view().labor_available[shift][pool]` reports; rejected above
  2x the roster.
- `overtime(shift, pool, hours)`: the pool's shift end extends by `hours` tonight (person-hours = hours x
  headcount are charged at 1.5x).
- `hire_temps(count, start_day, days)`: temps pick on the day shift (70% productivity, 2x mis-picks for 5
  days) and join the night crew when approved legs exceed the regular movers; at most 8 at a time.
- `protect(target, ids, days)`: SKUs / PM zones / a customer's recent SKUs are not moved.
- `resize_slots(skus, days_cover)`: active slot capacity = days_cover x average daily units.
- `wave_priority(mode)`: FIFO key for picking (rush_first default, by_cutoff, by_zone).
- `close_aisle(aisle, days)` / `reopen_aisle`: lines reroute to the pallet pool at 1.5x; a closed aisle
  carries no safety risk. A `rack_damage` event only marks the aisle damaged (open, extra risk, no
  moves) until the org closes it.
- `staging_clearance(n_movers)`: tonight the crew finishes in-flight swaps (legs 2 and 3, freeing
  staging) before starting new legs; no effect when staging is empty.
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
    budget = lv.n_moves if s.legs_per_night is None else min(lv.n_moves, s.legs_per_night)
    chosen = phaser.select_tonight(ranked, budget, lv.include_zones, exclude, protect, frozen, lv.order, inputs)
    remaining = s.queue_legs()
    if not chosen and remaining > 0:
        s.violations_pending.append(
            f"reslot_tonight ({lv.decision_id}): {remaining} legs remain but none are selectable tonight "
            f"(protected skus/zones/customers: {len(protect)} skus, {len(pz)} zones; frozen or closed aisles: {sorted(set(frozen))})")
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
