"""`propose_target`: the "slotting software" (spec 9.6). Pure, deterministic, uses history <= day 60 only."""

from __future__ import annotations

import numpy as np

from floor import constants as K
from floor.inputs import Inputs

DEFAULT_PARAMS = {
    "pm_share_target": 0.75,     # share of lines we want to flow through the PM
    "family_weight": 0.3,        # bonus for keeping families in the same zone (0..1)
    "zone_cap_pct": 100.0,       # max zone load as % of balanced load
    "days_cover": K.DEFAULT_DAYS_COVER,
    "recency_half_life": 30.0,   # days
    "excluded_aisles": [],
    "protected_zones": [],
}


def propose_target(inputs: Inputs, params: dict | None = None, history_days: int = 60) -> dict[str, dict]:
    """Return `{sku_id: {"active_loc": loc_id, "active_capacity_units": units}}` for every SKU.

    Greedy: rank SKUs by recency-weighted velocity (days 1..`history_days` only), fill the PM
    zones round-robin so zone loads stay balanced (respecting `zone_cap_pct`), then fill aisle
    active slots nearest the consolidator, level 1 first. Slot capacity = `days_cover` x daily
    units. Params: see `DEFAULT_PARAMS`.
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    n = len(inputs.sku_index)
    hl = float(p["recency_half_life"])
    vel = np.zeros(n)
    units = np.zeros(n)
    wsum = 0.0
    for d in range(1, history_days + 1):
        if d not in inputs.day_slices:
            continue
        w = 0.5 ** ((history_days - d) / hl)
        sl = inputs.day_slices[d][1]
        idx = inputs.lines["sku_idx"].values[sl]
        q = inputs.lines["qty"].values[sl].astype(float)
        np.add.at(vel, idx, w)
        np.add.at(units, idx, q)
        wsum += w
    vel /= max(wsum, 1e-9)
    units /= max(sum(1 for d in range(1, history_days + 1) if d in inputs.day_slices), 1)

    order = np.argsort(-vel, kind="stable")
    pm_slots = [i for i in range(len(inputs.loc_index)) if inputs.loc_kind[i] == 0
                and int(inputs.loc_zone[i]) not in set(int(z) for z in p["protected_zones"])]
    pm_by_zone: dict[int, list[int]] = {}
    for i in pm_slots:
        pm_by_zone.setdefault(int(inputs.loc_zone[i]), []).append(i)
    excluded = set(int(a) for a in p["excluded_aisles"])
    aisle_slots = [i for i in range(len(inputs.loc_index)) if inputs.loc_kind[i] == 1 and inputs.loc_level[i] <= 2
                   and int(inputs.loc_aisle[i]) not in excluded]
    aisle_slots.sort(key=lambda i: (inputs.base_service[i], int(inputs.loc_aisle[i]), i))

    total_lines = float(vel.sum())
    pm_lines_target = p["pm_share_target"] * total_lines
    zone_load = {z: 0.0 for z in pm_by_zone}
    zone_cap = (pm_lines_target / max(len(pm_by_zone), 1)) * p["zone_cap_pct"] / 100.0
    fam_zone: dict[int, int] = {}
    out: dict[str, dict] = {}
    sku_ids = inputs.skus["sku_id"].tolist()
    pm_assigned = 0.0
    ai = 0
    for s in order:
        s = int(s)
        cap = max(10.0, round(units[s] * p["days_cover"]))
        loc = -1
        if pm_assigned < pm_lines_target and any(pm_by_zone[z] for z in pm_by_zone):
            fam = int(inputs.family_of_sku[s])
            pref = fam_zone.get(fam)
            cands = [z for z in pm_by_zone if pm_by_zone[z] and zone_load[z] + vel[s] <= zone_cap]
            if cands:
                if pref in cands and p["family_weight"] > 0:
                    z = pref
                else:
                    z = min(cands, key=lambda z: (zone_load[z], z))
                loc = pm_by_zone[z].pop(0)
                zone_load[z] += vel[s]
                fam_zone.setdefault(fam, z)
                pm_assigned += vel[s]
        if loc < 0:
            while ai < len(aisle_slots) and aisle_slots[ai] in ():
                ai += 1
            if ai < len(aisle_slots):
                loc = aisle_slots[ai]
                ai += 1
            else:
                loc = int(inputs.current_loc[s])
        out[sku_ids[s]] = {"active_loc": inputs.locs["loc_id"].iloc[loc] if loc >= 0 else None,
                           "active_capacity_units": float(cap)}
    return out
