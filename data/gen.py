"""Gemba input-data generator (PLATFORM_SPEC 7.x, 8.1).

    python -m data.gen --seed 1 [--verify] [--out inputs.db] [--days 90] [--orders-per-day 2000]

Pure, seeded numpy/pandas. Writes the SQLite file at ``INPUTS_DB_URL`` (default ``sqlite:///inputs.db``).
Every one of the eight traps of spec 7.2 is a named function below; ``data.verify`` measures them.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- constants (spec 7.1-7.3)
N_SKU = 2000
N_FAM = 300
N_CUST = 200
N_AISLE = 28
BAYS = 60                 # 30 bays x 2 sides; bay 1-30 = left side, 31-60 = right side
LEVELS = 4                # 1-2 active, 3-4 reserve
PM_ZONES = 6
PM_SLOTS = 120
N_STAGING = 150
CUTOFFS = [13, 17, 21.5]
CUTOFF_INT = [13, 17, 21]
LINES_PER_ORDER = 12.0
ZONE_CAP_LINES_PER_DAY = 2 * 8 * 3600 / 25 * 2       # 4608
FAR_AISLES = [1, 2, 3, 4, 5, 24, 25, 26, 27, 28]
AISLE_SEQ = [14, 15, 13, 16, 12, 17, 11, 18, 10, 19, 9, 20, 8, 21, 7, 22, 6, 23, 5, 24, 4, 25, 3, 26, 2, 27, 1, 28]
TIER = 60                 # SKUs per aisle tier in the slotter (level 1 of one aisle)
FAMILY_P = 0.55
RUSH_P = 0.05
PALLET_P = 0.03
C001_LINE_SHARE = 0.08
C001_KEY_P = 0.85
ZIPF_OFFSET = 20.0
ZIPF_S = 1.3
PM_PEAK_TARGET = 1.2
N_EXTRA_SWAPS = 150       # random current-vs-ideal swaps so the target differs in ~1,100 slots
KEEP_TOLERANCE_M = 8

ROLES_PER_PICK_SHIFT = {"pm": 12, "forklift": 14, "replen": 4, "pallet": 3, "consolidation": 10, "loading": 6}
NIGHT_MOVERS = 4
N_TEMPS = 8

FIRST = ["Ana", "Bruno", "Carla", "Diego", "Elena", "Fabio", "Gina", "Hugo", "Iris", "Joao", "Kira", "Luis",
         "Marta", "Nico", "Olga", "Pedro", "Rita", "Saul", "Tania", "Ugo", "Vera", "Wes", "Xavi", "Yara", "Zeca",
         "Amir", "Beth", "Caio", "Dana", "Eli"]
LAST = ["Silva", "Santos", "Oliveira", "Souza", "Lima", "Pereira", "Costa", "Rocha", "Alves", "Gomes",
        "Martins", "Araujo", "Melo", "Barbosa", "Ribeiro", "Nunes", "Moreira", "Cardoso", "Teixeira", "Correia"]


def aisle_distance(a: int) -> int:
    """Aisles 14 and 15 are 10 m from the consolidator, +4 m per aisle away."""
    return int(round(8 + 4 * abs(a - 14.5)))


def history_days_for(days: int) -> int:
    return max(1, int(round(days * 2 / 3)))


def aisle_loc(a: int, bay: int, level: int) -> str:
    return f"A{a:02d}-{bay:02d}-{level}"


def pm_loc(z: int, slot: int) -> str:
    return f"PM{z}-{slot:03d}"


def loc_distance(loc: str) -> int:
    if loc.startswith("PM"):
        return 0
    if loc.startswith("A"):
        return aisle_distance(int(loc[1:3]))
    return 0


# --------------------------------------------------------------------------- static tables
def build_locations() -> pd.DataFrame:
    rows = []
    for a in range(1, N_AISLE + 1):
        for bay in range(1, BAYS + 1):
            for lvl in range(1, LEVELS + 1):
                active = lvl <= 2
                rows.append((aisle_loc(a, bay, lvl), "aisle", a, None, bay, lvl, "active" if active else "reserve",
                             20000 if active else 40000, 800.0 if active else 1500.0, float(a), float(bay)))
    for z in range(1, PM_ZONES + 1):
        for s in range(1, PM_SLOTS + 1):
            rows.append((pm_loc(z, s), "pm", None, z, s, 1, "active", 20000, 60.0, 14.5, float((z - 1) * PM_SLOTS + s) / 12))
    for s in range(1, N_STAGING + 1):
        rows.append((f"ST-{s:03d}", "staging", None, None, s, 1, "reserve", 40000, 1500.0, 14.5, -5.0))
    return pd.DataFrame(rows, columns=["loc_id", "kind", "aisle_id", "zone_id", "bay", "level", "slot_type",
                                       "capacity_units", "max_weight_kg", "x", "y"])


def build_roster(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    pid = 0

    def add(role, shift, n, temp):
        nonlocal pid
        for _ in range(n):
            pid += 1
            name = f"{FIRST[rng.integers(len(FIRST))]} {LAST[rng.integers(len(LAST))]}"
            prod = 0.7 if temp else float(np.clip(rng.normal(1.0, 0.08), 0.8, 1.2))
            rows.append((f"P{pid:03d}", name, role, shift, round(prod, 3), int(temp)))

    for shift in ("day", "swing"):
        for role, n in ROLES_PER_PICK_SHIFT.items():
            add(role, shift, n, False)
    add("night_move", "night", NIGHT_MOVERS, False)
    add("temp", "day", N_TEMPS, True)
    return pd.DataFrame(rows, columns=["person_id", "name", "role", "shift", "productivity", "is_temp"])


def build_calendar(days: int) -> pd.DataFrame:
    rows = []
    for d in range(1, days + 1):
        for shift in ("day", "swing"):
            for role, n in ROLES_PER_PICK_SHIFT.items():
                rows.append((d, shift, role, n))
        rows.append((d, "night", "night_move", NIGHT_MOVERS))
        rows.append((d, "day", "temp", 0))          # temps are on request only
    return pd.DataFrame(rows, columns=["day", "shift", "role", "scheduled"])


# --------------------------------------------------------------------------- trap 1 + families
def zipf_velocity(rng: np.random.Generator):
    """Trap 1. Seed-pick weights follow a shifted power law over a velocity rank; families are contiguous
    in rank so co-ordering (trap 2) does not flatten the curve. Returns (w, rank, fam_of, fam_members, fam_size)."""
    sizes = rng.integers(3, 9, size=N_FAM)
    while sizes.sum() < N_SKU:
        i = rng.integers(N_FAM)
        if sizes[i] < 8:
            sizes[i] += 1
    while sizes.sum() > N_SKU:
        i = rng.integers(N_FAM)
        if sizes[i] > 3:
            sizes[i] -= 1
    fam_order = rng.permutation(N_FAM)
    fam_by_rank = np.repeat(fam_order, sizes[fam_order])          # rank -> family
    rank_of_sku = rng.permutation(N_SKU)                           # sku -> rank (ids hide velocity)
    fam_of = np.empty(N_SKU, dtype=np.int64)
    fam_of[np.argsort(rank_of_sku)] = fam_by_rank
    w = (rank_of_sku + ZIPF_OFFSET) ** (-ZIPF_S)
    w /= w.sum()
    fam_members = np.full((N_FAM, 8), -1, dtype=np.int64)
    fam_size = np.zeros(N_FAM, dtype=np.int64)
    for s in np.argsort(fam_of, kind="stable"):
        f = fam_of[s]
        fam_members[f, fam_size[f]] = s
        fam_size[f] += 1
    return w, rank_of_sku, fam_of, fam_members, fam_size


def expected_lines_share(p: np.ndarray, fam_of: np.ndarray):
    """Expected lines per seed pick for each SKU under family co-ordering; returns (share, lines_per_seed)."""
    fam_sum = np.bincount(fam_of, weights=p, minlength=N_FAM)
    e = p + FAMILY_P * (fam_sum[fam_of] - p)
    lam = e.sum()
    return e / lam, lam


# --------------------------------------------------------------------------- trap 2
def family_cooccurrence(rng: np.random.Generator, seed_sku: np.ndarray, fam_of, fam_members, p=FAMILY_P):
    """Trap 2. For each seed pick, each other member of its family joins with probability p.
    Returns (row_index_of_seed, sku) pairs including the seed itself."""
    cand = fam_members[fam_of[seed_sku]]                            # (S, 8)
    mask = (cand >= 0) & (cand != seed_sku[:, None]) & (rng.random(cand.shape) < p)
    idx = np.repeat(np.arange(len(seed_sku)), 8).reshape(cand.shape)[mask]
    return np.concatenate([np.arange(len(seed_sku)), idx]), np.concatenate([seed_sku, cand[mask]])


# --------------------------------------------------------------------------- trap 3
def month3_shift(rng: np.random.Generator, rank_of_sku, fam_of, fam_members, fam_size, exclude: set[int]):
    """Trap 3. Whole families form a seasonal group (x3 from the held-out period) and a fading group (x0.3)."""
    best_rank = np.full(N_FAM, N_SKU)
    np.minimum.at(best_rank, fam_of, rank_of_sku)

    def pick(lo, hi, n_target, taken=frozenset()):
        fams = [f for f in rng.permutation(N_FAM) if lo <= best_rank[f] < hi and f not in exclude and f not in taken]
        chosen, n = [], 0
        for f in fams:
            if n >= n_target:
                break
            chosen.append(f)
            n += fam_size[f]
        return chosen

    seasonal_f = pick(500, 1500, 120)
    fading_f = pick(150, 700, 120, taken=frozenset(seasonal_f))
    # whole families, then trimmed to exactly 120 SKUs (the slowest members of the last family drop out)
    seasonal = np.concatenate([fam_members[f, :fam_size[f]] for f in seasonal_f])
    seasonal = seasonal[np.argsort(rank_of_sku[seasonal], kind="stable")][:120]
    fading = np.concatenate([fam_members[f, :fam_size[f]] for f in fading_f])
    fading = fading[np.argsort(rank_of_sku[fading], kind="stable")][:120]
    mult = np.ones(N_SKU)
    mult[seasonal] = 3.0
    mult[fading] = 0.3
    return seasonal, fading, mult, set(seasonal_f) | set(fading_f)


# --------------------------------------------------------------------------- trap 4 (far families + undersized)
def stale_layout(rng: np.random.Generator, rank_of_sku, fam_of, fam_members, fam_size):
    """Trap 4a. Whole families whose members sit in the top 200 (until 80 of the top 200 are covered) plus
    families from ranks 200-1000 (until ~220 SKUs) live in far aisles 1-5 / 24-28. Returns sku index array."""
    best_rank = np.full(N_FAM, N_SKU)
    np.minimum.at(best_rank, fam_of, rank_of_sku)
    far_f, covered = [], 0
    for f in rng.permutation(N_FAM):
        if covered >= 80:
            break
        if best_rank[f] < 200:
            far_f.append(f)
            covered += int((rank_of_sku[fam_members[f, :fam_size[f]]] < 200).sum())
    n = 0
    for f in rng.permutation(N_FAM):
        if n >= 220:
            break
        if 200 <= best_rank[f] < 1000 and f not in far_f:
            far_f.append(f)
            n += fam_size[f]
    far = np.concatenate([fam_members[f, :fam_size[f]] for f in far_f])
    return far, set(far_f)


def undersized_slots(rng: np.random.Generator, order_by_v: np.ndarray, n=25):
    """Trap 4b. 25 of the top-300 SKUs get an active slot holding < 1 day of cover."""
    return rng.choice(order_by_v[:300], size=n, replace=False)


# --------------------------------------------------------------------------- trap 5
def tight_pm(v_lines_per_day: np.ndarray, pm_skus: np.ndarray, peak=PM_PEAK_TARGET, cap=ZONE_CAP_LINES_PER_DAY):
    """Trap 5. Greedy zone fill: zone 1 takes the fastest SKUs until it carries ~peak x capacity, zone 2 the next
    until its (geometrically smaller) target, and so on; empty slots are back-filled with the slowest SKUs.
    Returns zone (1-based) per SKU in pm_skus (sorted fastest first)."""
    v = v_lines_per_day[pm_skus]
    order = np.argsort(-v, kind="stable")
    total = v.sum() / cap
    peak = min(peak, total * 0.9)
    lo, hi = 0.0, 1.0
    for _ in range(60):                                        # solve peak * sum(g^j) = total
        g = (lo + hi) / 2
        s = peak * sum(g ** j for j in range(PM_ZONES))
        lo, hi = (g, hi) if s < total else (lo, g)
    targets = np.array([peak * g ** j for j in range(PM_ZONES)]) * cap
    zone = np.zeros(len(pm_skus), dtype=int)
    head, tail = 0, len(order) - 1
    for z in range(1, PM_ZONES + 1):
        load, used = 0.0, 0
        while head <= tail and used < PM_SLOTS and (load < targets[z - 1] or z == PM_ZONES):
            zone[order[head]] = z
            load += v[order[head]]
            head += 1
            used += 1
        while head <= tail and used < PM_SLOTS:                # back-fill with the slowest
            zone[order[tail]] = z
            tail -= 1
            used += 1
    return zone


# --------------------------------------------------------------------------- traps 6, 7
def rush_orders(rng: np.random.Generator, n: int) -> np.ndarray:
    """Trap 6. 5% of orders are rush (2 h due window)."""
    return rng.random(n) < RUSH_P


def pallet_lines(rng: np.random.Generator, sku: np.ndarray, upp: np.ndarray) -> np.ndarray:
    """Trap 7. 3% of lines are full pallets (qty == units_per_pallet); the rest are small eaches."""
    qty = np.minimum(rng.geometric(0.4, size=len(sku)), 12).astype(np.int64)
    full = rng.random(len(sku)) < PALLET_P
    qty[full] = upp[sku[full]]
    return qty


# --------------------------------------------------------------------------- trap 8
def key_customer_concentration(v_lines_per_day: np.ndarray, active_loc: list[str], seasonal: np.ndarray):
    """Trap 8. The key-customer SKU set: top-300 by value-of-move (lines/day x current distance, non-PM)
    plus the seasonal group. C001's seed picks come from this set with probability C001_KEY_P."""
    dist = np.array([loc_distance(l) for l in active_loc], dtype=float)
    score = v_lines_per_day * dist
    top = np.argsort(-score, kind="stable")[:300]
    return np.unique(np.concatenate([top, seasonal]))


# --------------------------------------------------------------------------- current layout
def build_current(rng, v0, rank_of_sku, far, upp, n_extra=None):
    """Current (stale) layout: PM = top 720 non-far SKUs (zones assigned later by tight_pm),
    far families in far aisles, the rest in tiers of 60 from aisles 14/15 outward, plus random swaps."""
    order = np.argsort(-v0, kind="stable")
    far_set = set(far.tolist())
    non_far = [s for s in order if s not in far_set]
    pm_skus = np.array(non_far[:PM_ZONES * PM_SLOTS])
    pool = non_far[PM_ZONES * PM_SLOTS:]
    loc = [""] * N_SKU
    for i, s in enumerate(pool):
        loc[s] = aisle_loc(AISLE_SEQ[i // TIER], i % TIER + 1, 1)
    used = set(loc)
    far_slots = [aisle_loc(a, b, l) for a in FAR_AISLES for b in range(1, BAYS + 1) for l in (1, 2)]
    far_slots = [x for x in far_slots if x not in used]
    chosen = rng.choice(len(far_slots), size=len(far), replace=False)
    for s, k in zip(far, chosen):
        loc[s] = far_slots[k]
    pool_arr = np.array(pool)
    for _ in range(N_EXTRA_SWAPS if n_extra is None else n_extra):
        a, b = rng.choice(pool_arr, size=2, replace=False)
        loc[a], loc[b] = loc[b], loc[a]
    return loc, pm_skus


def finish_current(rng, loc, pm_skus, v_total, upd, undersized):
    zone = tight_pm(v_total, pm_skus)
    slot_used = {z: 0 for z in range(1, PM_ZONES + 1)}
    for s, z in zip(pm_skus, zone):
        slot_used[z] += 1
        loc[s] = pm_loc(z, slot_used[z])
    cover = rng.uniform(2.0, 4.0, size=N_SKU)
    cap = np.maximum(10, np.ceil(cover * upd / 10) * 10).astype(np.int64)
    cap[undersized] = np.maximum(1, np.floor(0.6 * upd[undersized])).astype(np.int64)
    reserve_slots = [aisle_loc(a, b, l) for a in range(1, N_AISLE + 1) for b in range(1, BAYS + 1) for l in (3, 4)]
    res_idx = rng.choice(len(reserve_slots), size=N_SKU, replace=False)
    reserve = [reserve_slots[i] for i in res_idx]
    return loc, reserve, cap


# --------------------------------------------------------------------------- target layout (days 1..history only)
def build_target(lines_hist: pd.DataFrame, history_days: int, skus: pd.DataFrame, current: pd.DataFrame,
                 zone_cap: float = ZONE_CAP_LINES_PER_DAY, tolerance_m: int = KEEP_TOLERANCE_M) -> pd.DataFrame:
    """Deterministic slotter. Uses only history lines. Top 720 fill the PM with no zone above 1.0 expected load
    (keeping a SKU's current PM slot when the zone allows); the next fastest get aisle slots in tiers of 60 from
    aisles 14/15 outward, keeping a current aisle slot within ``tolerance_m`` of the assigned aisle;
    active_capacity_units = 2 days of cover rounded up to 10."""
    sku_ids = skus["sku_id"].tolist()
    upp = skus.set_index("sku_id")["units_per_pallet"]
    counts = lines_hist.groupby("sku_id").size().reindex(sku_ids, fill_value=0)
    v = (counts / history_days).to_numpy(dtype=float)
    nonpal = lines_hist[lines_hist["qty"] != lines_hist["sku_id"].map(upp).to_numpy()]
    units = nonpal.groupby("sku_id")["qty"].sum().reindex(sku_ids, fill_value=0)
    upd = (units / history_days).to_numpy(dtype=float)
    cur = current.set_index("sku_id")
    cur_loc = cur["active_loc"].reindex(sku_ids).tolist()
    order = np.lexsort((np.arange(N_SKU), -v))                    # by v desc, sku index asc

    target = [""] * N_SKU
    # PM
    n_pm = PM_ZONES * PM_SLOTS
    pm = order[:n_pm]
    load = np.zeros(PM_ZONES + 1)
    pm_used: dict[int, set[int]] = {z: set() for z in range(1, PM_ZONES + 1)}
    pending = []
    for s in pm:
        l = cur_loc[s]
        if l.startswith("PM"):
            z, slot = int(l[2]), int(l[4:7])
            if load[z] + v[s] <= zone_cap and slot not in pm_used[z]:
                target[s] = l
                load[z] += v[s]
                pm_used[z].add(slot)
                continue
        pending.append(s)
    for s in pending:
        best = None
        for z in range(1, PM_ZONES + 1):
            if len(pm_used[z]) < PM_SLOTS and (best is None or load[z] < load[best]):
                best = z
        slot = next(k for k in range(1, PM_SLOTS + 1) if k not in pm_used[best])
        target[s] = pm_loc(best, slot)
        load[best] += v[s]
        pm_used[best].add(slot)
    # aisles
    rest = order[n_pm:]
    used = set()
    assigned_aisle = {}
    pending = []
    for i, s in enumerate(rest):
        a = AISLE_SEQ[min(i // TIER, N_AISLE - 1)]
        assigned_aisle[s] = a
        l = cur_loc[s]
        if l.startswith("A") and abs(aisle_distance(int(l[1:3])) - aisle_distance(a)) <= tolerance_m and l not in used:
            target[s] = l
            used.add(l)
        else:
            pending.append(s)
    free = {a: [aisle_loc(a, b, lv) for lv in (1, 2) for b in range(1, BAYS + 1)] for a in range(1, N_AISLE + 1)}
    free = {a: [x for x in slots if x not in used] for a, slots in free.items()}
    for s in pending:
        a = assigned_aisle[s]
        start = AISLE_SEQ.index(a)
        for a2 in AISLE_SEQ[start:] + AISLE_SEQ[:start]:
            if free[a2]:
                target[s] = free[a2].pop(0)
                break
    cap = np.maximum(10, np.ceil(2.0 * upd / 10) * 10).astype(np.int64)
    return pd.DataFrame({"sku_id": sku_ids, "active_loc": target,
                         "reserve_loc": cur["reserve_loc"].reindex(sku_ids).tolist(),
                         "active_capacity_units": cap})


# --------------------------------------------------------------------------- orders
RELEASE_HOURS = np.arange(5, 20)
RELEASE_P = np.array([2, 5, 8, 11, 12, 11, 9, 8, 8, 7, 6, 5, 4, 2, 2], dtype=float)
RELEASE_P /= RELEASE_P.sum()


def cutoff_for(release: np.ndarray, rush: np.ndarray) -> np.ndarray:
    """First carrier cutoff >= release + 3 h (rush: + 2 h); none left -> 13 (ships next day)."""
    due = release + np.where(rush, 2, 3)
    out = np.full(len(release), CUTOFF_INT[0], dtype=np.int64)
    done = np.zeros(len(release), dtype=bool)
    for c in CUTOFF_INT:
        hit = (~done) & (due <= c)
        out[hit] = c
        done |= hit
    return out


def gen_orders(rng, days: np.ndarray, opd: int, p_gen, p_c1, mu_seeds, cust_p, fam_of, fam_members, upp):
    n = len(days) * opd
    day = np.repeat(days, opd)
    num = np.tile(np.arange(1, opd + 1), len(days))
    order_id = np.char.add(np.char.add("O", np.char.zfill(day.astype(str), 2)), "-")
    order_id = np.char.add(order_id, np.char.zfill(num.astype(str), 5))
    customer = rng.choice(N_CUST, size=n, p=cust_p)
    rush = rush_orders(rng, n)
    release = rng.choice(RELEASE_HOURS, size=n, p=RELEASE_P)
    cutoff = cutoff_for(release, rush)
    k = np.maximum(1, rng.poisson(mu_seeds, size=n))
    oidx = np.repeat(np.arange(n), k)
    S = len(oidx)
    seed = rng.choice(N_SKU, size=S, p=p_gen)
    c1 = customer[oidx] == 0
    seed[c1] = rng.choice(N_SKU, size=int(c1.sum()), p=p_c1)
    sidx, sku = family_cooccurrence(rng, seed, fam_of, fam_members)
    o = oidx[sidx]
    key = np.unique(o.astype(np.int64) * N_SKU + sku)
    o, sku = key // N_SKU, key % N_SKU
    line_no = np.arange(len(o)) - np.searchsorted(o, o, side="left") + 1
    qty = pallet_lines(rng, sku, upp)
    orders = pd.DataFrame({"order_id": order_id, "day": day, "release_hour": release,
                           "customer_id": [f"C{c + 1:03d}" for c in customer],
                           "priority": np.where(rush, "rush", "normal"), "cutoff_hour": cutoff})
    lines = pd.DataFrame({"order_id": order_id[o], "line_no": line_no, "sku_id": sku, "qty": qty, "day": day[o]})
    return orders, lines


# --------------------------------------------------------------------------- main generation
def generate(seed: int = 1, days: int = 90, orders_per_day: int = 2000, heldout_seed: int = 0) -> dict:
    t0 = time.time()
    rng = np.random.default_rng(seed)
    hist = history_days_for(days)
    lines_per_day = orders_per_day * LINES_PER_ORDER

    w, rank_of_sku, fam_of, fam_members, fam_size = zipf_velocity(rng)
    upp = rng.integers(20, 121, size=N_SKU)
    weight = np.round(np.exp(rng.normal(0.3, 0.9, size=N_SKU)).clip(0.05, 40), 3)
    cube = np.round(weight * rng.uniform(1.0, 6.0, size=N_SKU), 2)
    hazmat = (rng.random(N_SKU) < 0.02).astype(int)
    fragile = (rng.random(N_SKU) < 0.05).astype(int)

    far, far_fams = stale_layout(rng, rank_of_sku, fam_of, fam_members, fam_size)
    seasonal, fading, mult, _ = month3_shift(rng, rank_of_sku, fam_of, fam_members, fam_size, exclude=far_fams)

    share0, _ = expected_lines_share(w, fam_of)
    v0 = share0 * lines_per_day
    loc0, pm0 = build_current(rng, v0, rank_of_sku, far, upp, n_extra=0)
    key_skus = key_customer_concentration(v0, loc0, seasonal)

    def phase_dists(w_phase):
        p_gen = w_phase / w_phase.sum()
        pk = np.zeros(N_SKU)
        pk[key_skus] = p_gen[key_skus]
        p_c1 = C001_KEY_P * pk / pk.sum() + (1 - C001_KEY_P) * p_gen
        p_mix = (1 - C001_LINE_SHARE) * p_gen + C001_LINE_SHARE * p_c1
        share, lam = expected_lines_share(p_mix, fam_of)
        return p_gen, p_c1, share * lines_per_day, LINES_PER_ORDER / lam

    p_gen1, p_c11, v_total, mu1 = phase_dists(w)
    p_gen2, p_c12, _, mu2 = phase_dists(w * mult)
    loc, pm_skus = build_current(rng, v_total, rank_of_sku, far, upp)   # PM set from the realised velocity
    upd = 0.97 * v_total * 2.5
    undersized = undersized_slots(rng, np.argsort(-v_total, kind="stable"))
    loc, reserve, cap = finish_current(rng, loc, pm_skus, v_total, upd, undersized)

    # customers: C001 8 %, C002/C003 4 % each, rest power-law over C004..C200
    cust_p = np.zeros(N_CUST)
    cust_p[0], cust_p[1], cust_p[2] = C001_LINE_SHARE, 0.04, 0.04
    tail = 1.0 / np.arange(1, N_CUST - 2) ** 0.6
    cust_p[3:] = (1 - cust_p[:3].sum()) * tail / tail.sum()

    roster = build_roster(rng)
    orders1, lines1 = gen_orders(rng, np.arange(1, hist + 1), orders_per_day, p_gen1, p_c11, mu1,
                                 cust_p, fam_of, fam_members, upp)
    rng_held = np.random.default_rng([seed, 7919 + heldout_seed])
    orders2, lines2 = gen_orders(rng_held, np.arange(hist + 1, days + 1), orders_per_day, p_gen2, p_c12, mu2,
                                 cust_p, fam_of, fam_members, upp)
    sku_ids = np.array([f"S{i + 1:04d}" for i in range(N_SKU)])
    lines1["sku_id"] = sku_ids[lines1["sku_id"].to_numpy()]
    lines2["sku_id"] = sku_ids[lines2["sku_id"].to_numpy()]
    orders = pd.concat([orders1, orders2], ignore_index=True)
    lines = pd.concat([lines1, lines2], ignore_index=True)

    key_set = set(key_skus.tolist())
    demand_group = np.full(N_SKU, "normal", dtype=object)
    demand_group[seasonal] = "seasonal"
    demand_group[fading] = "fading"
    skus = pd.DataFrame({"sku_id": sku_ids, "family_id": [f"F{f + 1:03d}" for f in fam_of],
                         "weight_kg": weight, "cube_l": cube, "units_per_pallet": upp,
                         "hazmat": hazmat, "fragile": fragile,
                         "customer_tier_hint": ["key" if i in key_set else "standard" for i in range(N_SKU)],
                         "demand_group": demand_group})
    current = pd.DataFrame({"sku_id": sku_ids, "active_loc": loc, "reserve_loc": reserve, "active_capacity_units": cap})
    target = build_target(lines1, hist, skus, current)
    n_changed = int((target["active_loc"].to_numpy() != current["active_loc"].to_numpy()).sum())

    meta = {"history_days": hist, "orders_per_day": orders_per_day, "seed": seed,
            "seasonal": sku_ids[seasonal].tolist(), "fading": sku_ids[fading].tolist(),
            "far": sku_ids[far].tolist(), "undersized": sku_ids[undersized].tolist(),
            "key_skus": sku_ids[key_skus].tolist(), "n_changed_slots": n_changed,
            "zone_cap_lines_per_day": ZONE_CAP_LINES_PER_DAY}
    site = pd.DataFrame([{"site_id": "SITE1", "name": "Gemba DC (synthetic)", "opening_hour": 6, "closing_hour": 22,
                          "cutoffs": json.dumps(CUTOFFS), "history_days": hist, "gen_meta": json.dumps(meta)}])
    aisles = pd.DataFrame({"aisle_id": range(1, N_AISLE + 1), "x": [float(a) for a in range(1, N_AISLE + 1)],
                           "distance_m": [aisle_distance(a) for a in range(1, N_AISLE + 1)],
                           "bays": BAYS, "levels": LEVELS})
    pm_zones = pd.DataFrame({"zone_id": range(1, PM_ZONES + 1), "slots": PM_SLOTS, "pickers_default": 2,
                             "seconds_per_line": 25.0})
    customers = pd.DataFrame({"customer_id": [f"C{i:03d}" for i in range(1, N_CUST + 1)],
                              "name": [f"Customer {i:03d}" for i in range(1, N_CUST + 1)],
                              "tier": ["key" if i <= 3 else "standard" for i in range(1, N_CUST + 1)]})
    return {"site": site, "aisles": aisles, "pm_zones": pm_zones, "locations": build_locations(), "skus": skus,
            "customers": customers, "slotting_current": current, "slotting_target": target, "orders": orders,
            "order_lines": lines, "labor_roster": roster, "labor_calendar": build_calendar(days),
            "_meta": meta, "_gen_seconds": time.time() - t0}


# --------------------------------------------------------------------------- write
def db_path_from_env(default: str = "sqlite:///inputs.db") -> str:
    url = os.environ.get("INPUTS_DB_URL", default)
    return url[len("sqlite:///"):] if url.startswith("sqlite:///") else url


def write_db(tables: dict, path: str) -> None:
    p = Path(path)
    if p.exists():
        p.unlink()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;")
    con.executescript((Path(__file__).parent / "schema.sql").read_text())
    for name, df in tables.items():
        if name.startswith("_"):
            continue
        cols = list(df.columns)
        sql = f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        data = zip(*[df[c].astype(object).where(df[c].notna(), None).tolist() for c in cols])
        con.executemany(sql, data)
    con.executescript("""
        CREATE INDEX ix_orders_day ON orders(day);
        CREATE INDEX ix_lines_day ON order_lines(day);
        CREATE INDEX ix_lines_order ON order_lines(order_id);
        CREATE INDEX ix_calendar_day ON labor_calendar(day);
    """)
    con.commit()
    con.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--orders-per-day", type=int, default=2000)
    ap.add_argument("--heldout-seed", type=int, default=0, help="perturbs days after history only (tests)")
    ap.add_argument("--out", default=None, help="SQLite path; default from INPUTS_DB_URL or inputs.db")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args(argv)
    out = a.out or db_path_from_env()
    t0 = time.time()
    tables = generate(a.seed, a.days, a.orders_per_day, a.heldout_seed)
    t1 = time.time()
    write_db(tables, out)
    t2 = time.time()
    print(f"wrote {out}: orders={len(tables['orders'])} lines={len(tables['order_lines'])} "
          f"changed_slots={tables['_meta']['n_changed_slots']} gen={t1 - t0:.1f}s write={t2 - t1:.1f}s")
    if a.verify:
        from data.verify import verify, print_report
        checks = verify(out)
        print_report(checks)
        return 0 if all(c.ok for c in checks) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
