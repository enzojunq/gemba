"""Hourly simulation of one day, 06:00 -> 06:00 next day (spec 9.2). Pool-level, vectorized per hour.

`simulate_day` mutates the state it is given (callers pass a copy) and returns the `DayResult`.
With `expected=True` (what-ifs) mis-picks and near-misses are expected values, never draws.
"""

from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

import contracts as c
from floor import constants as K
from floor.inputs import Inputs
from floor.model import FloorState, Move

SHIFT_RANGE = {"day": (6, 14), "swing": (14, 22), "night": (22, 30)}
POOLS: tuple[str, ...] = c.Pool.__args__  # type: ignore[attr-defined]
ROUTE_FORKLIFT = K.N_ZONES
ROUTE_PALLET = K.N_ZONES + 1

_C1, _C2, _C3, _C4 = (np.uint64(0x9E3779B97F4A7C15), np.uint64(0xBF58476D1CE4E5B9),
                      np.uint64(0x94D049BB133111EB), np.uint64(0xD6E8FEB86659FD93))


def hash_u01(*parts: np.ndarray | int) -> np.ndarray:
    """SplitMix64 of the xor-mix of integer parts -> uniform in [0, 1). Stable across runs/forks."""
    x = np.zeros(1, dtype=np.uint64)
    with np.errstate(over="ignore"):
        for i, p in enumerate(parts):
            arr = np.atleast_1d(np.asarray(p, dtype=np.int64)).astype(np.uint64)
            x = x ^ ((arr + np.uint64(i + 1)) * _C1)
            x = (x ^ (x >> np.uint64(31))) * _C4
        z = x + _C1
        z = (z ^ (z >> np.uint64(30))) * _C2
        z = (z ^ (z >> np.uint64(27))) * _C3
        z = z ^ (z >> np.uint64(31))
    return (z >> np.uint64(11)).astype(np.float64) * (2.0 ** -53)


def order_codes(order_ids: pd.Series) -> np.ndarray:
    return np.array([zlib.crc32(str(o).encode()) for o in order_ids], dtype=np.int64)


def _serve(pending: np.ndarray, work: np.ndarray, cap: float) -> tuple[np.ndarray, float]:
    """FIFO-serve `pending` (indices in priority order) with `cap` seconds. Returns (served idx, seconds used).
    The first unfinished line keeps its partial progress in `work`."""
    if len(pending) == 0 or cap <= 0:
        return pending[:0], 0.0
    w = work[pending]
    cs = np.cumsum(w)
    n_full = int(np.searchsorted(cs, cap, side="right"))
    served = pending[:n_full]
    used = float(cs[n_full - 1]) if n_full > 0 else 0.0
    if n_full < len(pending):
        leftover = cap - used
        if leftover > 0:
            work[pending[n_full]] -= leftover
            used += leftover
    return served, used


def apply_surge(inputs: Inputs, demand: c.DayDemand, effects: list, seed: int = 0) -> c.DayDemand:
    """demand_surge: resample the lines of the surged family/skus/customer by `factor` (seeded)."""
    if not effects:
        return demand
    from floor.perturb import scale_lines
    lines, orders = demand.lines, demand.orders
    if len(lines) == 0:
        return demand
    factor = np.ones(len(lines))
    idx = lines["sku_id"].map(inputs.sku_index).fillna(-1).to_numpy(dtype=np.int64)
    for e in effects:
        f = float(e.args.get("factor", 1.5))
        if e.args.get("family"):
            fmask = inputs.family_mask(str(e.args["family"]))
            mask = (idx >= 0) & fmask[np.where(idx >= 0, idx, 0)]
        elif e.args.get("skus"):
            mask = lines["sku_id"].isin(list(e.args["skus"])).to_numpy()
        elif e.args.get("customer"):
            cust = orders.set_index("order_id")["customer_id"]
            mask = lines["order_id"].map(cust).eq(inputs.customer_alias(str(e.args["customer"]))).to_numpy()
        else:
            continue
        factor = factor * np.where(mask, f, 1.0)
    if np.allclose(factor, 1.0):
        return demand
    codes = order_codes(lines["order_id"])
    new_lines = scale_lines(lines, factor, seed, demand.day, 11, codes)
    return c.DayDemand(day=demand.day, orders=orders, lines=new_lines)


def _headcount(state: FloorState, day: int) -> dict[str, dict[str, float]]:
    hc = {s: {p: float(state.labor.get(s, {}).get(p, 0)) for p in POOLS} for s in SHIFT_RANGE}
    for e in state.effect_active("labor_shortage", day) + state.effect_active("absenteeism", day):
        pool, pct = e.args.get("pool", "forklift"), float(e.args.get("pct", 15))
        for s in hc:
            hc[s][pool] = hc[s][pool] * (1 - pct / 100.0)
    for e in state.effect_active("forklift_breakdown", day):
        n = float(e.args.get("count", 1))
        for s in hc:
            hc[s]["forklift"] = max(0.0, hc[s]["forklift"] - n)
    return hc


def simulate_day(inputs: Inputs, state: FloorState, day: int, demand: c.DayDemand, expected: bool) -> c.DayResult:
    s = state
    n_sku = len(s.sku_loc)
    s.inventory = s.inventory.astype(float)
    violations: list[str] = list(s.violations_pending)
    s.violations_pending = []
    demand = apply_surge(inputs, demand, s.effect_active("demand_surge", day), s.seed)
    orders, lines = demand.orders, demand.lines
    n_orders, n_lines = len(orders), len(lines)

    # ------------------------------------------------------------------ per-day parameters
    hc = _headcount(s, day)
    temps = s.temps_active(day)
    temps_ramp = s.temps_ramping(day)
    closed = set(s.closed_now(day))
    frozen = set(s.frozen_now(day)) | closed
    wms = bool(s.effect_active("wms_sync_delay", day))
    staging_pct0 = s.staging_pct()
    congested = staging_pct0 > K.CONGESTION_THRESHOLD
    zone_down = {int(e.args.get("zone", 0)): (e.start_hour if e.start_hour >= 6 else e.start_hour + 24, float(e.args.get("hours", 4)))
                 for e in s.effect_active("pm_zone_down", day)}
    key_week = {inputs.customer_alias(str(e.args.get("customer"))) for e in s.effect_active("key_customer_week", day)}
    extra_fork = 0.0
    for e in s.effect_active("returns_spike", day):
        extra_fork += float(e.args.get("pct", 10)) / 100.0 * n_lines * 60.0
    for e in s.effect_active("new_sku_launch", day):
        extra_fork += float(e.args.get("n", 10)) * 5 * (K.FORKLIFT_BASE + K.FORKLIFT_PER_M * 66)
    dip = s.dip_mask(day)
    units_per_pallet = inputs.skus["units_per_pallet"].to_numpy(dtype=float) if "units_per_pallet" in inputs.skus else np.full(n_sku, 48.0)

    # ------------------------------------------------------------------ line arrays
    if n_lines:
        oidx = pd.Index(orders["order_id"]).get_indexer(lines["order_id"]).astype(np.int64)
    else:
        oidx = np.zeros(0, dtype=np.int64)
    sku = lines["sku_id"].map(inputs.sku_index).fillna(-1).to_numpy(dtype=np.int64) if n_lines else np.zeros(0, dtype=np.int64)
    qty = lines["qty"].to_numpy(dtype=float) if n_lines else np.zeros(0)
    line_no = lines["line_no"].to_numpy(dtype=np.int64) if n_lines else np.zeros(0, dtype=np.int64)
    known = sku >= 0
    sku_safe = np.where(known, sku, 0)
    loc = np.where(known, s.sku_loc[sku_safe], -1)
    has_loc = loc >= 0
    loc_safe = np.where(has_loc, loc, 0)
    kind = np.where(has_loc, inputs.loc_kind[loc_safe], 1)
    aisle = np.where(has_loc, inputs.loc_aisle[loc_safe], K.N_AISLES).astype(np.int64)
    zone = np.where(has_loc, inputs.loc_zone[loc_safe], 0).astype(np.int64)
    base = np.where(has_loc, inputs.base_service[loc_safe], K.FORKLIFT_BASE + K.FORKLIFT_PER_M * 66).astype(float)
    is_pallet = known & (qty >= units_per_pallet[sku_safe])
    in_closed = np.isin(aisle, list(closed)) & (kind != 0)
    route = np.where(kind == 0, zone - 1, ROUTE_FORKLIFT)
    route = np.where(is_pallet | in_closed, ROUTE_PALLET, route).astype(np.int64)
    svc = np.where(is_pallet, K.PALLET_SECONDS, base)
    svc = np.where(in_closed & ~is_pallet, base * K.FROZEN_TO_PALLET, svc)
    dip_line = known & dip[sku_safe]
    svc = svc * np.where(dip_line, K.DIP_TIME, 1.0)
    if congested:
        svc = svc * np.where(np.isin(aisle, K.STAGING_ADJACENT_AISLES) & (kind != 0), K.CONGESTION_TIME, 1.0)
    work = svc.copy()
    if closed:
        dec_closed = [a for a in closed if (s.closed_aisles.get(a, (0, None))[1] or "").startswith("dec:")]
        for a in dec_closed:
            n_a = int((in_closed & (aisle == a)).sum())
            if n_a:
                violations.append(f"{n_a} lines rerouted to pallet pool from closed aisle {a} ({s.closed_aisles[a][1]})")

    rel = orders["release_hour"].to_numpy(dtype=float) if n_orders else np.zeros(0)
    cut = orders["cutoff_hour"].to_numpy(dtype=float) if n_orders else np.zeros(0)
    cut = np.where(cut == 21.0, 21.5, cut)  # the 21 cutoff is 21:30 (data lane)
    eff_cut = np.where(cut >= rel, cut, cut + 24.0)
    rush = (orders["priority"].to_numpy() == "rush") if n_orders else np.zeros(0, dtype=bool)
    cust = orders["customer_id"].to_numpy() if n_orders else np.zeros(0, dtype=object)
    if key_week:
        rush = rush | np.isin(cust, list(key_week))
    l_rel, l_cut, l_rush = rel[oidx], eff_cut[oidx], rush[oidx]
    if s.wave_mode == "by_cutoff":
        keys = (line_no, oidx, l_rel, l_cut)
    elif s.wave_mode == "by_zone":
        keys = (line_no, oidx, l_rel, route, ~l_rush)
    else:  # rush_first: FIFO by release within priority; orders complete together
        keys = (line_no, oidx, l_rel, ~l_rush)
    order_perm = np.lexsort(keys) if n_lines else np.zeros(0, dtype=np.int64)
    rank = np.empty(n_lines, dtype=np.int64)
    rank[order_perm] = np.arange(n_lines)

    # mis-pick probability per line (expected) and uniform draws keyed by (seed, day, order_id, line_no)
    p_base = np.where(kind == 0, K.MISPICK_PM, K.MISPICK_FORKLIFT)
    p_base = np.where(is_pallet, K.MISPICK_PALLET, p_base)
    p_line = p_base * np.where(dip_line, K.DIP_MISPICK, 1.0) * np.where(dip_line & wms, K.WMS_MISPICK, 1.0)
    fork_hc = hc["day"]["forklift"]
    temp_share = (K.TEMP_PRODUCTIVITY * temps_ramp) / max(fork_hc + K.TEMP_PRODUCTIVITY * temps, 1e-9) if temps else 0.0
    p_line = p_line * np.where(route == ROUTE_FORKLIFT, 1.0 + temp_share * (K.TEMP_MISPICK - 1.0), 1.0)
    if not expected and n_lines:
        u = hash_u01(s.seed, day, order_codes(orders["order_id"])[oidx], line_no)
        mis_line = u < p_line
    else:
        mis_line = np.zeros(n_lines, dtype=bool)

    # ------------------------------------------------------------------ per-hour bookkeeping
    done = np.zeros(n_lines, dtype=bool)
    stockout_flag = np.zeros(n_lines, dtype=bool)
    lines_left = np.bincount(oidx, minlength=n_orders).astype(np.int64) if n_orders else np.zeros(0, dtype=np.int64)
    first_done = np.full(n_orders, np.inf)
    arrive = np.full(n_orders, np.inf)       # hour the last line finished (consolidation arrival)
    closed_t = np.full(n_orders, np.inf)
    loaded_t = np.full(n_orders, np.inf)
    inv = s.inventory
    cap_units = s.sku_cap
    replen_req = np.full(n_sku, -1, dtype=np.int64)   # hour requested, -1 none
    lump = dict(s.carry_work)                           # pool -> seconds of anonymous backlog
    carry_lines_pool = {}

    hourly = []
    served_seconds = {p: 0.0 for p in POOLS}
    avail_hours = {p: 0.0 for p in POOLS}
    busy_hours_sum = 0.0
    mispicks_cust: dict[str, int | float] = {}
    lines_cust: dict[str, int] = {}
    for cu in np.unique(cust) if n_orders else []:
        lines_cust[str(cu)] = int((cust[oidx] == cu).sum())
        mispicks_cust[str(cu)] = 0
    mispick_by_dec: dict[str, float] = {}
    total_mispicks = 0.0
    replen_trips = 0
    jam_hours = 0
    cons_max = 0.0
    pm_peak = 0.0
    stage_max = staging_pct0
    risk_max = 0.0
    near_exp = 0.0
    near_draws: list[tuple[int, int, str | None]] = []
    moves_done_tonight = 0
    served_lines_total = 0
    zone_work_day = np.zeros(K.N_ZONES)
    zone_cap_day = np.zeros(K.N_ZONES)
    ot_person_hours = 0.0
    stall_reported = False

    pool_of_route = {**{z: "pm" for z in range(K.N_ZONES)}, ROUTE_FORKLIFT: "forklift", ROUTE_PALLET: "pallet"}
    approved = [m for m in s.moves if m.approved and m.legs_done < m.legs]
    approved.sort(key=lambda m: (0 if m.legs_done > 0 else 1, m.seq))
    legs_budget_carry = 0.0

    for h in K.DAY_HOURS:
        ha = h if h >= 6 else h + 24
        phase = K.SHIFT_OF_HOUR[h]
        av: dict[str, float] = {p: 0.0 for p in POOLS}
        for shift, (s0, s1) in SHIFT_RANGE.items():
            ot_hours = s.overtime_today.get((shift, "__all__"), (0.0, ""))[0]
            for p in POOLS:
                oth = s.overtime_today.get((shift, p), (0.0, ""))[0]
                if s0 <= ha < s1 + max(oth, ot_hours):
                    av[p] += hc[shift][p]
                    if ha >= s1:
                        ot_person_hours += hc[shift][p]
        if temps and 6 <= ha < 14:
            av["temp"] += temps
        if temps and 22 <= ha < 29 and approved:
            av["night_move"] += temps * K.TEMP_PRODUCTIVITY
        for p in POOLS:
            avail_hours[p] += av[p]

        hour_served = {p: 0 for p in POOLS}
        hour_queue = {p: 0 for p in POOLS}
        hour_busy = {p: 0.0 for p in POOLS}
        zone_load = [0.0] * K.N_ZONES
        aisle_work = np.zeros(K.N_AISLES + 1)
        aisle_people = np.zeros(K.N_AISLES + 1)
        hour_stockouts = 0
        hour_mispicks = 0.0
        served_all = []

        pending_all = np.flatnonzero((~done) & (l_rel <= ha))
        if len(pending_all):
            pending_all = pending_all[np.argsort(rank[pending_all], kind="stable")]
            blocked = (inv[sku_safe[pending_all]] < qty[pending_all]) & known[pending_all] & (route[pending_all] != ROUTE_PALLET)
            hour_stockouts = int(blocked.sum())
            stockout_flag[pending_all[blocked]] = True
            pending_ok = pending_all[~blocked]
            route_p = route[pending_ok]
            pend_work_by_route = np.bincount(route[pending_all], weights=work[pending_all], minlength=ROUTE_PALLET + 1)
            # forklift extra work (returns / new sku) arrives spread over 06-22
            if extra_fork and 6 <= ha < 22:
                lump["forklift"] = lump.get("forklift", 0.0) + extra_fork / 16.0
            spare_pm = 0.0
            spare_fork = 0.0
            zone_pend: dict[int, np.ndarray] = {}
            for r in range(ROUTE_PALLET + 1):
                pool = pool_of_route[r]
                zone_is_down = False
                if pool == "pm":
                    cap_people = av["pm"] / K.N_ZONES
                    zd = zone_down.get(r + 1)
                    if zd and zd[0] <= ha < zd[0] + zd[1]:
                        cap_people = 0.0
                        zone_is_down = True
                elif pool == "forklift":
                    cap_people = av["forklift"] + K.TEMP_PRODUCTIVITY * av["temp"]
                else:
                    cap_people = av["pallet"]
                cap = cap_people * 3600.0 * K.PRODUCTIVE_FACTOR
                if pool == "pm":
                    zone_load[r] = (pend_work_by_route[r] / cap) if cap > 0 else (2.0 if (zone_is_down and pend_work_by_route[r] > 0) else 0.0)
                    zone_cap_day[r] += cap
                used = 0.0
                if lump.get(pool, 0.0) > 0 and cap > 0:
                    take = min(cap, lump[pool])
                    lump[pool] -= take
                    cap -= take
                    used += take
                pend = pending_ok[route_p == r]
                served, u2 = _serve(pend, work, cap)
                used += u2
                served_all.append(served)
                hour_served[pool] += len(served)
                served_seconds[pool] += used
                hour_busy[pool] += used / (3600.0 * K.PRODUCTIVE_FACTOR)
                if pool == "pm":
                    zone_work_day[r] += used
                    spare_pm += max(cap - u2, 0.0)
                    zone_pend[r] = pend[len(served):]
                elif pool == "forklift":
                    spare_fork = max(cap - u2, 0.0)
                if pool == "forklift" and len(pend):
                    np.add.at(aisle_work, aisle[pend], work[pend])
                if len(served) and pool in ("forklift", "pallet"):
                    np.add.at(aisle_people, aisle[served], svc[served] / (3600.0 * K.PRODUCTIVE_FACTOR))
            # Flex: idle PM pickers help overloaded zones, then idle forklift pickers (cross-trained)
            # pick in the PM, both at a walking penalty. Each helper's time is booked to its own pool.
            for helper, spare in (("pm", spare_pm), ("forklift", spare_fork * K.FORKLIFT_HELP_FRACTION if K.FORKLIFT_HELPS_PM else 0.0)):
                if spare <= 0:
                    continue
                for r in sorted(zone_pend, key=lambda z: -len(zone_pend[z])):
                    rest = zone_pend[r]
                    rest = rest[~done[rest]]
                    if len(rest) == 0 or spare <= 0:
                        continue
                    served, u2 = _serve(rest, work, spare / K.PM_FLEX_PENALTY)
                    spare -= u2 * K.PM_FLEX_PENALTY
                    served_all.append(served)
                    done[served] = True
                    hour_served["pm"] += len(served)
                    served_seconds[helper] += u2 * K.PM_FLEX_PENALTY
                    hour_busy[helper] += u2 * K.PM_FLEX_PENALTY / (3600.0 * K.PRODUCTIVE_FACTOR)
                    zone_work_day[r] += u2
            served_idx = np.concatenate(served_all) if served_all else np.zeros(0, dtype=np.int64)
            if len(served_idx):
                done[served_idx] = True
                served_lines_total += len(served_idx)
                dep = served_idx[known[served_idx] & (route[served_idx] != ROUTE_PALLET)]
                np.subtract.at(inv, sku_safe[dep], qty[dep])
                # mis-picks
                if expected:
                    mp = p_line[served_idx]
                    hour_mispicks = float(mp.sum())
                    for cu in np.unique(cust[oidx[served_idx]]):
                        mispicks_cust[str(cu)] = mispicks_cust.get(str(cu), 0) + float(mp[cust[oidx[served_idx]] == cu].sum())
                    dl = dip_line[served_idx]
                    for i in np.flatnonzero(dl):
                        dec = s.dip_decision.get(int(sku[served_idx[i]]), "")
                        if dec:
                            mispick_by_dec[dec] = mispick_by_dec.get(dec, 0.0) + float(mp[i])
                else:
                    mis = served_idx[mis_line[served_idx]]
                    hour_mispicks = float(len(mis))
                    for i in mis:
                        cu = str(cust[oidx[i]])
                        mispicks_cust[cu] = mispicks_cust.get(cu, 0) + 1
                        if dip_line[i]:
                            dec = s.dip_decision.get(int(sku[i]), "")
                            if dec:
                                mispick_by_dec[dec] = mispick_by_dec.get(dec, 0.0) + 1.0
                total_mispicks += hour_mispicks
                np.subtract.at(lines_left, oidx[served_idx], 1)
                fd = np.unique(oidx[served_idx])
                first_done[fd] = np.minimum(first_done[fd], ha)
                newly = fd[lines_left[fd] <= 0]
                arrive[newly] = np.minimum(arrive[newly], ha)
            # queues at end of hour (lines)
            still = pending_all[~done[pending_all]]
            for r in range(ROUTE_PALLET + 1):
                hour_queue[pool_of_route[r]] += int((route[still] == r).sum())

        # replenishment: request trips for low slots, serve FIFO at 6 min/trip
        low = (inv < K.REPLEN_TRIGGER * cap_units) & (replen_req < 0) & (s.sku_loc >= 0)
        replen_req[low] = ha
        req = np.flatnonzero(replen_req >= 0)
        rcap = int(av["replen"] * 3600.0 * K.PRODUCTIVE_FACTOR / K.REPLEN_TRIP_SECONDS)
        if len(req) and rcap > 0:
            order_r = np.lexsort((inv[req] / np.maximum(cap_units[req], 1e-9), replen_req[req]))
            take = req[order_r[:rcap]]
            inv[take] = cap_units[take]
            replen_req[take] = -1
            replen_trips += len(take)
            hour_served["replen"] = len(take)
            hour_busy["replen"] = len(take) * K.REPLEN_TRIP_SECONDS / (3600.0 * K.PRODUCTIVE_FACTOR)
            served_seconds["replen"] += len(take) * K.REPLEN_TRIP_SECONDS
            ra = inputs.loc_aisle[s.sku_loc[take]]
            np.add.at(aisle_people, ra, K.REPLEN_TRIP_SECONDS / 3600.0)
        hour_queue["replen"] = int((replen_req >= 0).sum())

        # consolidation: orders whose last line finished are closed FIFO at 40 s each; jam above 400 open
        open_mask = (first_done <= ha) & ~(closed_t < np.inf)
        n_open = int(open_mask.sum())
        cons_max = max(cons_max, n_open)
        jam = n_open > K.CONSOLIDATOR_CAP
        jam_hours += int(jam)
        ccap = av["consolidation"] * 3600.0 * K.PRODUCTIVE_FACTOR / K.CONSOLIDATE_SECONDS / (K.JAM_SLOWDOWN if jam else 1.0)
        ready = np.flatnonzero((arrive <= ha) & ~(closed_t < np.inf))
        n_close = 0
        if len(ready) and ccap > 0:
            ready = ready[np.lexsort((eff_cut[ready], arrive[ready]))]
            n_close = min(len(ready), int(ccap))
            closed_t[ready[:n_close]] = ha + (np.arange(1, n_close + 1) / max(ccap, 1.0))
        hour_served["consolidation"] = n_close
        hour_queue["consolidation"] = max(n_open - n_close, 0)
        hour_busy["consolidation"] = n_close * K.CONSOLIDATE_SECONDS / (3600.0 * K.PRODUCTIVE_FACTOR)

        # loading: ready = closed + 40 min; doors staffed = loaders // 2; 120 orders/h/door
        rt = closed_t + K.PACK_LOAD_MIN / 60.0
        can_load = np.flatnonzero((rt <= ha + 1) & ~(loaded_t < np.inf))
        doors = min(K.DOORS, int(av["loading"] // K.LOADERS_PER_DOOR))
        lcap = doors * K.ORDERS_PER_DOOR_HOUR
        n_load = 0
        late_h = 0
        if len(can_load) and lcap > 0:
            can_load = can_load[np.lexsort((rt[can_load], eff_cut[can_load]))]
            n_load = min(len(can_load), lcap)
            sel = can_load[:n_load]
            lt = np.maximum(rt[sel], ha + np.arange(1, n_load + 1) / lcap)
            loaded_t[sel] = lt
            late_h = int((lt > eff_cut[sel]).sum())
        hour_served["loading"] = n_load
        hour_queue["loading"] = int(len(can_load) - n_load)
        hour_busy["loading"] = min(av["loading"], n_load / K.ORDERS_PER_DOOR_HOUR * K.LOADERS_PER_DOOR)
        next_cut = next((cc for cc in inputs.cutoffs if cc > ha), None) if ha < 22 else None

        # night moves 22:00-05:00 (7 productive hours), 6 min per leg per mover
        moves_h = {"done": 0, "sample": []}
        if 22 <= ha < 22 + K.NIGHT_PRODUCTIVE_HOURS and approved:
            legs_cap = av["night_move"] * 3600.0 / K.MOVE_LEG_SECONDS + legs_budget_carry
            n_legs = int(legs_cap)
            legs_budget_carry = legs_cap - n_legs
            done_legs, sample, stalled, legs_by_aisle = _execute_legs(inputs, s, approved, n_legs, frozen, day)
            moves_h["done"] = done_legs
            moves_h["sample"] = sample
            moves_done_tonight += done_legs
            hour_served["night_move"] = done_legs
            hour_busy["night_move"] = done_legs * K.MOVE_LEG_SECONDS / 3600.0
            served_seconds["night_move"] += done_legs * K.MOVE_LEG_SECONDS
            if stalled and not stall_reported:
                violations.append(f"staging full: {stalled} legs stalled on night of day {day}")
                stall_reported = True
            for a, n_a in legs_by_aisle.items():
                aisle_people[a] += n_a * K.MOVE_LEG_SECONDS / 3600.0
            approved = [m for m in approved if m.legs_done < m.legs]
        hour_queue["night_move"] = sum(m.legs - m.legs_done for m in approved)
        stage_pct = s.staging_pct()
        stage_max = max(stage_max, stage_pct)
        if stage_pct > K.CONGESTION_THRESHOLD:
            for a in K.STAGING_ADJACENT_AISLES:
                aisle_people[a] += 1.0

        # safety
        risk = aisle_people / K.SAFE_LIMIT
        for a in closed:
            risk[a] = 0.0
        risk_max = max(risk_max, float(risk[1:].max()) if K.N_AISLES else 0.0)
        excess = np.maximum(risk[1:] - 1.0, 0.0)
        p_nm = K.NEAR_MISS_P * excess
        near_exp += float(p_nm.sum())
        near_aisle = None
        if not expected and p_nm.any():
            u = hash_u01(s.seed, day, ha, np.arange(1, K.N_AISLES + 1))
            hit = np.flatnonzero(u < p_nm)
            if len(hit):
                near_aisle = int(hit[0]) + 1
                cause = None
                if stage_pct > K.CONGESTION_THRESHOLD and near_aisle in K.STAGING_ADJACENT_AISLES and s.staging:
                    decs = [d for _, d in s.staging.values() if d]
                    cause = c.node_id(c.DEC, max(set(decs), key=decs.count)) if decs else None
                near_draws.append((day, near_aisle, cause))

        for p in POOLS:
            busy_hours_sum += hour_busy[p] if p in ("pm", "forklift", "pallet", "replen", "temp") else 0.0
        fork_cap_share = max((av["forklift"] + K.TEMP_PRODUCTIVITY * av["temp"]) * 3600.0 * K.PRODUCTIVE_FACTOR / K.N_AISLES, 1e-9)
        hourly.append({
            "hour": int(h), "phase": phase,
            "pools": {p: {"available": int(round(av[p])), "busy": int(round(min(av[p], hour_busy[p]))),
                          "queue": int(hour_queue[p]), "served": int(hour_served[p])} for p in POOLS},
            "pm_zones": [round(float(z), 3) for z in zone_load],
            "aisles": [round(float(aisle_work[a] / fork_cap_share), 3) for a in range(1, K.N_AISLES + 1)],
            "aisle_people": [int(round(float(aisle_people[a]))) for a in range(1, K.N_AISLES + 1)],
            "safety_risk": [round(float(risk[a]), 3) for a in range(1, K.N_AISLES + 1)],
            "frozen_aisles": sorted(int(a) for a in frozen),
            "consolidator_open": int(n_open),
            "jam": bool(jam),
            "trucks": {"loaded": int(n_load), "late": int(late_h), "next_cutoff": (float(next_cut) if next_cut is not None else None)},
            "staging_occupancy": round(float(stage_pct), 2),
            "stockouts": int(hour_stockouts),
            "mispicks": int(round(hour_mispicks)),
            "near_miss_aisle": near_aisle,
            "moves": moves_h,
        })

    # ------------------------------------------------------------------ day close
    on_time = (loaded_t <= eff_cut)
    unclosed_next_day = (closed_t == np.inf) & (eff_cut >= K.NEXT_DAY_FIRST_CUTOFF)
    on_time = on_time | unclosed_next_day
    on_time_pct = 100.0 * float(on_time.sum()) / n_orders if n_orders else 100.0
    for r in range(ROUTE_PALLET + 1):
        pool = pool_of_route[r]
        left = (~done) & (route == r)
        if left.any():
            lump[pool] = lump.get(pool, 0.0) + float(work[left].sum())
    s.carry_work = {p: min(v, K.CARRY_HOURS * 3600.0 * max(hc["day"][p], 1.0)) for p, v in lump.items() if v > 0}
    s.inventory = inv
    s.dip_day = s.dip_day  # updated in _execute_legs

    for r in range(K.N_ZONES):
        left_r = (~done) & (route == r)
        zone_work_day[r] += float(work[left_r].sum())
    pm_peak = float(np.max(zone_work_day / np.maximum(zone_cap_day, 1e-9))) if zone_cap_day.max() > 0 else 0.0
    served_lines = int(done.sum())
    busy_4 = (served_seconds["pm"] + served_seconds["forklift"] + served_seconds["pallet"] + served_seconds["replen"]) / 3600.0
    lph = served_lines / busy_4 if busy_4 > 0 else 0.0
    headcount_cost = sum(hc[sh][p] * K.SHIFT_HOURS * K.HOURLY_WAGE for sh in hc for p in POOLS if p != "temp")
    ot_hours_kpi = ot_person_hours
    labor_cost = headcount_cost + temps * K.SHIFT_HOURS * K.TEMP_WAGE + ot_hours_kpi * K.HOURLY_WAGE * K.OVERTIME_MULT
    mispick_rate = 100.0 * total_mispicks / served_lines if served_lines else 0.0
    by_cust = {cu: round(100.0 * mispicks_cust.get(cu, 0) / lines_cust[cu], 4) if lines_cust[cu] else 0.0 for cu in lines_cust}
    near = near_exp if expected else float(len(near_draws))
    s.near_misses.extend(near_draws)
    queue_legs = s.queue_legs()
    progress = 100.0 * s.moves_done_total / s.moves_target_total if s.moves_target_total else 100.0
    kpis = c.Kpis(
        lines=int(n_lines), lph=round(lph, 3), throughput_lph_site=round(served_lines / 16.0, 3),
        on_time_pct=round(on_time_pct, 3), mispick_rate=round(mispick_rate, 4), pm_peak_zone_load=round(pm_peak, 3),
        consolidator_queue_max=float(cons_max), jam_hours=int(jam_hours), replen_trips=int(replen_trips),
        stockout_lines=int(stockout_flag.sum()), overtime_hours=round(ot_hours_kpi, 2), labor_cost_usd=round(labor_cost, 2),
        moves_done=int(moves_done_tonight), moves_queued=int(queue_legs), reslot_progress_pct=round(progress, 3),
        staging_occupancy_max=round(stage_max, 2), safety_risk_max=round(risk_max, 3), near_misses=round(near, 4),
        mispick_rate_by_customer=by_cust,
    )
    key = s.key_customer
    top_dec = max(mispick_by_dec, key=mispick_by_dec.get) if mispick_by_dec else None
    s.kpi_history.append({"day": day, "on_time": kpis.on_time_pct, "key_mispick": by_cust.get(key, 0.0),
                          "mispick": kpis.mispick_rate, "dip_decision": top_dec, "stockout_pct": 100.0 * kpis.stockout_lines / max(n_lines, 1),
                          "staging_0600": s.staging_pct(), "near_misses": [a for d, a, _ in near_draws],
                          "lines": n_lines, "lph": kpis.lph})
    s.kpi_history = s.kpi_history[-7:]
    for (shift, pool), (hrs, dec) in s.overtime_today.items():
        if hrs > 0 and pool != "__all__":
            s.overtime_history.append((day, pool, hrs, dec))
    s.overtime_today = {}
    s.staging_movers = 0
    s.day = day + 1
    return c.DayResult(day=day, kpis=kpis, hourly={"version": 1, "day": int(day), "hours": hourly}, violations=violations)


def _execute_legs(inputs: Inputs, s: FloorState, approved: list[Move], n_legs: int, frozen: set[int], day: int
                  ) -> tuple[int, list[dict], int, dict[int, int]]:
    """Execute up to `n_legs` legs of the approved moves in order.
    Returns (legs done, sample for the animation, stalled legs, legs touching each aisle)."""
    done = 0
    sample: list[dict] = []
    stalled = 0
    legs_by_aisle: dict[int, int] = {}
    loc_ids = inputs.locs["loc_id"]
    sku_ids = inputs.skus["sku_id"]
    free_staging = [i for i in range(1, K.STAGING_SLOTS + 1) if i not in s.staging]
    free_staging.reverse()

    def aisle_of_loc(l: int) -> int:
        return int(inputs.loc_aisle[l]) if l >= 0 else 0

    def record(sku: int, frm: int, to: int, dec: str) -> None:
        nonlocal done
        done += 1
        s.moves_done_total += 1
        if len(sample) < 10:
            sample.append({"sku": str(sku_ids.iloc[sku]), "from": str(loc_ids.iloc[frm]) if frm >= 0 else "",
                           "to": str(loc_ids.iloc[to]) if to >= 0 else ""})
        s.dip_day[sku] = day
        if dec:
            s.dip_decision[sku] = dec
        for l in (frm, to):
            a = aisle_of_loc(l)
            if a:
                legs_by_aisle[a] = legs_by_aisle.get(a, 0) + 1

    def one_leg(m: Move) -> bool:
        """Execute the next leg of `m`. Returns False when it cannot proceed (frozen aisle / no staging)."""
        cur = int(s.sku_loc[m.sku])
        tgt = m.to_loc
        if aisle_of_loc(cur) in frozen or aisle_of_loc(tgt) in frozen:
            return False
        occupant = s.loc_occupant.get(tgt, -1)
        if m.legs_done == 0:
            m.legs = 3 if (occupant >= 0 and occupant != m.sku) else 1
            if m.legs == 1:
                s.loc_occupant.pop(cur, None)
                s.sku_loc[m.sku] = tgt
                s.loc_occupant[tgt] = m.sku
                s.sku_cap[m.sku] = inputs.target_cap[m.sku] if inputs.target_cap[m.sku] > 0 else s.sku_cap[m.sku]
                s.inventory[m.sku] = s.sku_cap[m.sku]
                m.legs_done = 1
                m.from_loc = cur
                record(m.sku, cur, tgt, m.decision_id)
                return True
            if not free_staging:
                return False
            slot = free_staging.pop()
            s.staging[slot] = (m.sku, m.decision_id)
            m.staging_slot = slot
            m.from_loc = cur
            s.loc_occupant.pop(cur, None)
            s.sku_loc[m.sku] = inputs.loc_index.get(f"ST-{slot:03d}", cur)
            m.legs_done = 1
            record(m.sku, cur, int(s.sku_loc[m.sku]), m.decision_id)
            return True
        if m.legs_done == 1:
            occ = s.loc_occupant.get(tgt, -1)
            if occ >= 0:
                s.sku_loc[occ] = m.from_loc
                s.loc_occupant[m.from_loc] = occ
                s.loc_occupant.pop(tgt, None)
                record(occ, tgt, m.from_loc, m.decision_id)
                for om in s.moves:
                    if om.sku == occ and om.to_loc == m.from_loc and om.legs_done < om.legs:
                        om.legs_done = om.legs
            m.legs_done = 2
            return True
        s.sku_loc[m.sku] = tgt
        s.loc_occupant[tgt] = m.sku
        s.sku_cap[m.sku] = inputs.target_cap[m.sku] if inputs.target_cap[m.sku] > 0 else s.sku_cap[m.sku]
        s.inventory[m.sku] = s.sku_cap[m.sku]
        if m.staging_slot in s.staging:
            del s.staging[m.staging_slot]
            free_staging.append(m.staging_slot)
        m.staging_slot = -1
        m.legs_done = 3
        record(m.sku, inputs.loc_index.get("ST-001", -1), tgt, m.decision_id)
        return True

    # waves: finish swaps already in flight, then first legs of everything approved (staging permitting),
    # then second legs, then third legs. Staging fills when a night cannot finish what it started.
    in_flight = [m for m in approved if 0 < m.legs_done < m.legs]
    fresh = [m for m in approved if m.legs_done == 0]
    for m in in_flight:
        while done < n_legs and m.legs_done < m.legs:
            if not one_leg(m):
                break
    for wave in (1, 2, 3):
        for m in fresh:
            if done >= n_legs:
                break
            if m.legs_done == wave - 1 and m.legs_done < m.legs:
                if not one_leg(m):
                    if wave == 1 and not free_staging:
                        stalled += 1
    stalled += sum(1 for m in fresh if m.legs_done == 0 and done < n_legs and not free_staging)
    return done, sample, stalled, legs_by_aisle
