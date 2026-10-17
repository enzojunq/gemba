"""Mini `inputs.db` with the spec 8.1 schema so floor tests never wait on lane A1.

28 aisles, 6 PM zones, ~200 SKUs, ~60 orders/day, days 1-70. Built once per test session.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import sqlalchemy as sa

N_SKUS = 200
N_FAMILIES = 30
N_CUSTOMERS = 12
ORDERS_PER_DAY = 60
DAYS = 70
KEY_CUSTOMER = "C001"


def build_mini_inputs(path: Path, seed: int = 11, n_skus: int = N_SKUS, orders_per_day: int = ORDERS_PER_DAY,
                      days: int = DAYS) -> str:
    rng = np.random.default_rng(seed)
    url = f"sqlite:///{path}"
    eng = sa.create_engine(url)

    sku_ids = [f"S{i + 1:04d}" for i in range(n_skus)]
    fam = [f"F{(i % N_FAMILIES) + 1:03d}" for i in range(n_skus)]
    skus = pd.DataFrame({"sku_id": sku_ids, "family_id": fam, "weight_kg": rng.uniform(0.2, 20, n_skus).round(2),
                         "cube_l": rng.uniform(0.5, 60, n_skus).round(1), "units_per_pallet": 48,
                         "hazmat": 0, "fragile": 0, "customer_tier_hint": "standard"})
    # Zipf velocity: rank-based
    ranks = np.arange(1, n_skus + 1)
    weights = 1.0 / ranks ** 0.9
    weights /= weights.sum()

    # locations: aisles 28 x 6 bays x 4 levels (small), pm 6 zones x 30 slots, staging 150
    rows = []
    for a in range(1, 29):
        for b in range(1, 7):
            for lv in range(1, 5):
                rows.append((f"A{a:02d}-{b:02d}-{lv}", "aisle", a, None, b, lv, "active" if lv <= 2 else "reserve", 120.0))
    for z in range(1, 7):
        for s in range(1, 31):
            rows.append((f"PM{z}-{s:03d}", "pm", None, z, s, 1, "active", 60.0))
    for s in range(1, 151):
        rows.append((f"ST-{s:03d}", "staging", None, None, s, 1, "active", 100.0))
    locs = pd.DataFrame(rows, columns=["loc_id", "kind", "aisle_id", "zone_id", "bay", "level", "slot_type", "capacity_units"])
    locs["max_weight_kg"] = 500.0
    locs["x"] = 0.0
    locs["y"] = 0.0

    # stale current slotting: top SKUs partly in far aisles; target puts them in PM / near aisles
    active_aisle = locs[(locs.kind == "aisle") & (locs.level <= 2)]["loc_id"].tolist()
    pm_slots = locs[locs.kind == "pm"]["loc_id"].tolist()
    far = [l for l in active_aisle if int(l[1:3]) <= 5 or int(l[1:3]) >= 24]
    near = [l for l in active_aisle if 10 <= int(l[1:3]) <= 19]
    rng.shuffle(far)
    rng.shuffle(near)
    cur, tgt = {}, {}
    pm_cur = pm_slots[:60]      # 60 slow-ish SKUs sit in PM today
    pm_tgt = pm_slots[:60]
    order = list(range(n_skus))
    # current: SKUs 0..39 (fast) in far aisles, 40..99 in PM, rest in near aisles / remaining
    pool_cur = far[:40] + pm_cur + near[:100]
    for i in order:
        cur[sku_ids[i]] = pool_cur[i]
    # target: fast SKUs 0..59 into PM, 60..119 near aisles, rest where they are (or far)
    pool_tgt = pm_tgt + near[:60] + [cur[sku_ids[i]] for i in range(120, n_skus)]
    for i in order:
        tgt[sku_ids[i]] = pool_tgt[i]
    daily_units = weights * orders_per_day * 10 * 3
    cap_cur = np.maximum(10, np.where(np.arange(n_skus) < 25, daily_units * 0.4, daily_units * 2.0)).round()
    cap_tgt = np.maximum(10, daily_units * 2.0).round()
    slotting_current = pd.DataFrame({"sku_id": sku_ids, "active_loc": [cur[s] for s in sku_ids],
                                     "reserve_loc": [f"A{(i % 28) + 1:02d}-01-3" for i in range(n_skus)],
                                     "active_capacity_units": cap_cur})
    slotting_target = pd.DataFrame({"sku_id": sku_ids, "active_loc": [tgt[s] for s in sku_ids],
                                    "reserve_loc": slotting_current["reserve_loc"], "active_capacity_units": cap_tgt})

    customers = pd.DataFrame({"customer_id": [f"C{i + 1:03d}" for i in range(N_CUSTOMERS)],
                              "name": [f"Customer {i + 1}" for i in range(N_CUSTOMERS)],
                              "tier": ["key"] + ["standard"] * (N_CUSTOMERS - 1)})

    cutoffs = [13, 17, 21.5]
    orders, lines = [], []
    for d in range(1, days + 1):
        n = orders_per_day + int(rng.integers(-5, 6))
        for i in range(n):
            oid = f"O{d:03d}-{i + 1:04d}"
            rel = float(rng.integers(5, 20))
            rush = rng.random() < 0.05
            cust = KEY_CUSTOMER if rng.random() < 0.25 else f"C{int(rng.integers(2, N_CUSTOMERS + 1)):03d}"
            due = rel + (2 if rush else 3)
            co = next((c for c in cutoffs if c >= due), cutoffs[0])
            orders.append((oid, d, rel, cust, "rush" if rush else "normal", float(co)))
            nl = int(rng.integers(3, 8))
            w = weights.copy()
            if cust == KEY_CUSTOMER:   # key customer concentrates on the fast SKUs (trap 8)
                w[:40] *= 4
                w /= w.sum()
            picks = rng.choice(n_skus, size=nl, replace=False, p=w)
            for j, k in enumerate(picks):
                qty = int(rng.integers(1, 6))
                if rng.random() < 0.03:
                    qty = 48
                lines.append((oid, j + 1, sku_ids[k], qty))
    orders_df = pd.DataFrame(orders, columns=["order_id", "day", "release_hour", "customer_id", "priority", "cutoff_hour"])
    lines_df = pd.DataFrame(lines, columns=["order_id", "line_no", "sku_id", "qty"])

    roster = []
    pid = 0
    for shift, pools in {"day": {"pm": 12, "forklift": 14, "replen": 4, "pallet": 3, "consolidation": 10, "loading": 6},
                         "swing": {"pm": 12, "forklift": 14, "replen": 4, "pallet": 3, "consolidation": 10, "loading": 6},
                         "night": {"night_move": 4}}.items():
        for role, n in pools.items():
            for _ in range(n):
                pid += 1
                roster.append((f"P{pid:03d}", f"Person {pid}", role, shift, 1.0, 0))
    roster_df = pd.DataFrame(roster, columns=["person_id", "name", "role", "shift", "productivity", "is_temp"])
    cal = roster_df.groupby(["shift", "role"]).size().reset_index(name="scheduled")
    cal_rows = [(d, r.shift, r.role, int(r.scheduled)) for d in range(1, days + 1) for r in cal.itertuples()]
    calendar_df = pd.DataFrame(cal_rows, columns=["day", "shift", "role", "scheduled"])

    site = pd.DataFrame({"site_id": ["S1"], "name": ["Mini"], "opening_hour": [6], "closing_hour": [22],
                         "cutoffs": [json.dumps(cutoffs)]})
    aisles = pd.DataFrame({"aisle_id": range(1, 29), "x": range(1, 29),
                           "distance_m": [10 + 4 * min(abs(a - 14), abs(a - 15)) for a in range(1, 29)],
                           "bays": 6, "levels": 4})
    pm_zones = pd.DataFrame({"zone_id": range(1, 7), "slots": 30, "pickers_default": 2, "seconds_per_line": 25})

    with eng.begin() as conn:
        for name, df in [("site", site), ("aisles", aisles), ("pm_zones", pm_zones), ("locations", locs), ("skus", skus),
                         ("customers", customers), ("slotting_current", slotting_current),
                         ("slotting_target", slotting_target), ("orders", orders_df), ("order_lines", lines_df),
                         ("labor_roster", roster_df), ("labor_calendar", calendar_df)]:
            df.to_sql(name, conn, index=False, if_exists="replace")
    return url
