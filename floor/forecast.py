"""Forecast demand (spec 9.4a): same-weekday resampling from the 14 days before `day`, seeded by (seed, d).

Never reads a day >= `day` from actuals. Scaled by announced events and per-family recalibration.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import contracts as c
from floor.inputs import Inputs
from floor.model import FloorState


def forecast_day(inputs: Inputs, state: FloorState, d: int, today: int, known: list[c.Event]) -> c.DayDemand:
    """Forecast for day `d` built from actual days < `today` only."""
    src_days = [x for x in (d - 7, d - 14, d - 21) if 1 <= x < today and x in inputs.day_slices]
    if not src_days:
        src_days = [x for x in range(today - 14, today) if x >= 1 and x in inputs.day_slices][-2:]
    rng = np.random.default_rng([int(state.seed), int(d), 977])
    if not src_days:
        return inputs.demand(-1)
    o_parts, l_parts = [], []
    n_target = int(round(np.mean([inputs.day_slices[x][0].stop - inputs.day_slices[x][0].start for x in src_days])))
    pool_o = pd.concat([inputs.orders.iloc[inputs.day_slices[x][0]] for x in src_days], ignore_index=True)
    pick = rng.choice(len(pool_o), size=min(n_target, len(pool_o)), replace=False)
    pick.sort()
    orders = pool_o.iloc[pick].copy()
    src_ids = orders["order_id"].to_numpy()
    lines = inputs.lines[inputs.lines["order_id"].isin(src_ids) & inputs.lines["day"].isin(src_days)].copy()
    new_id = {o: f"f{d}-{i:05d}" for i, o in enumerate(src_ids)}
    orders["order_id"] = orders["order_id"].map(new_id)
    lines["order_id"] = lines["order_id"].map(new_id)
    orders["day"] = d
    lines["day"] = d
    o_parts.append(orders)
    l_parts.append(lines)
    orders = pd.concat(o_parts, ignore_index=True)
    lines = pd.concat(l_parts, ignore_index=True)

    # event scaling (announced by `today`), and family recalibration: resample lines with the factor
    fam = inputs.skus.set_index("sku_id")["family_id"]
    line_fam = lines["sku_id"].map(fam)
    factor = np.ones(len(lines))
    for e in known:
        n = int(e.args.get("days", 1))
        if not (e.day <= d < e.day + n):
            continue
        if e.kind == "demand_surge":
            f = float(e.args.get("factor", 1.5))
            if e.args.get("family"):
                fmask = inputs.family_mask(str(e.args["family"]))
                idx = lines["sku_id"].map(inputs.sku_index).fillna(-1).to_numpy(dtype=np.int64)
                factor *= np.where((idx >= 0) & fmask[np.where(idx >= 0, idx, 0)], f, 1.0)
            elif e.args.get("skus"):
                factor *= np.where(lines["sku_id"].isin(list(e.args["skus"])), f, 1.0)
            elif e.args.get("customer"):
                cust = orders.set_index("order_id")["customer_id"]
                factor *= np.where(lines["order_id"].map(cust).eq(inputs.customer_alias(str(e.args["customer"]))), f, 1.0)
        elif e.kind == "key_customer_week":
            cust = orders.set_index("order_id")["customer_id"]
            factor *= np.where(lines["order_id"].map(cust).eq(inputs.customer_alias(str(e.args.get("customer")))), 1.3, 1.0)
        elif e.kind == "returns_spike":
            pass
    if state.recal:
        factor *= line_fam.map(state.recal).fillna(1.0).to_numpy(dtype=float)
    if not np.allclose(factor, 1.0) and len(lines):
        u = rng.random(len(lines))
        keep = u < np.minimum(factor, 1.0)
        reps = np.floor(factor).astype(int)
        frac = factor - reps
        extra_n = reps - 1 + (u < frac).astype(int)
        extra_n = np.where(factor < 1.0, 0, extra_n)
        base = lines[keep | (factor >= 1.0)]
        parts = [base]
        for k in range(1, int(extra_n.max()) + 1 if len(extra_n) else 1):
            sel = lines[extra_n >= k].copy()
            if len(sel):
                sel["line_no"] = sel["line_no"] + 1000 * k
                parts.append(sel)
        lines = pd.concat(parts, ignore_index=True).sort_values(["order_id", "line_no"], kind="stable")
    orders = orders.drop(columns=["day"]).reset_index(drop=True)
    lines = lines.drop(columns=["day", "sku_idx"], errors="ignore").reset_index(drop=True)
    return c.DayDemand(day=d, orders=orders[["order_id", "release_hour", "customer_id", "priority", "cutoff_hour"]],
                       lines=lines[["order_id", "line_no", "sku_id", "qty"]])


def forecast_demand(inputs: Inputs, state: FloorState, day: int, horizon: int, known_events: list[c.Event]) -> list[c.DayDemand]:
    known = c.visible(known_events, day)
    return [forecast_day(inputs, state, d, day, known) for d in range(day, day + horizon)]


def recalibrate(inputs: Inputs, state: FloorState, actual: list[c.DayResult], predicted: list[c.Kpis]) -> FloorState:
    """Multiply each family's forecast by actual / forecast lines over the last 3 days (spec 9.4a).
    Families are not visible in `Kpis`, so the site-wide ratio is applied to every family."""
    s = state.copy()
    pairs = [(a.kpis.lines, p.lines) for a, p in zip(actual[-3:], predicted[-3:]) if p.lines > 0]
    if not pairs:
        return s
    ratio = sum(a for a, _ in pairs) / max(sum(p for _, p in pairs), 1)
    ratio = float(min(max(ratio, 0.5), 2.0))
    for f in inputs.family_ids:
        s.recal[f] = round(s.recal.get(f, 1.0) * ratio, 4)
    return s
