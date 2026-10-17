"""Verify the eight generator traps (spec 7.2) on an inputs.db. ``python -m data.verify [--db inputs.db]``.
Exit code 1 if any check fails. Each check measures the realised data, not the generator's intent."""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass

import numpy as np
import pandas as pd

from data.gen import TARGET_ZONE_CAPACITY, ZONE_CAP_LINES_PER_DAY, aisle_distance, db_path_from_env, history_days_for

FAR = {1, 2, 3, 4, 5, 24, 25, 26, 27, 28}


@dataclass
class Check:
    name: str
    measured: float
    target: str
    ok: bool


def _load(db: str) -> dict:
    con = sqlite3.connect(db)
    t = {n: pd.read_sql(f"SELECT * FROM {n}", con) for n in
         ("site", "skus", "customers", "slotting_current", "slotting_target", "orders", "order_lines")}
    con.close()
    return t


def _loc_zone(loc: str):
    return int(loc[2]) if loc.startswith("PM") else None


def _loc_aisle(loc: str):
    return int(loc[1:3]) if loc.startswith("A") else None


def verify(db: str) -> list[Check]:
    t = _load(db)
    site = t["site"].iloc[0]
    meta = json.loads(site["gen_meta"]) if "gen_meta" in site and site["gen_meta"] else {}
    hist = int(site["history_days"]) if "history_days" in site else history_days_for(int(t["orders"]["day"].max()))
    skus, lines, orders = t["skus"], t["order_lines"], t["orders"]
    cur = t["slotting_current"].set_index("sku_id")
    if "day" not in lines.columns:
        lines = lines.merge(orders[["order_id", "day"]], on="order_id")
    lh = lines[lines["day"] <= hist]
    lo = lines[lines["day"] > hist]
    n_hist_days = max(1, lh["day"].nunique())
    counts = lh.groupby("sku_id").size().reindex(skus["sku_id"], fill_value=0)
    v = counts / n_hist_days                                     # lines/day, history
    checks: list[Check] = []

    # 1 zipf
    top20 = counts.sort_values(ascending=False).iloc[: int(0.2 * len(counts))].sum() / counts.sum()
    checks.append(Check("zipf_velocity: top-20% SKU share of hist lines", round(float(top20), 4), ">= 0.75", top20 >= 0.75))

    # 2 family co-occurrence (first 5 history days)
    fam = skus.set_index("sku_id")["family_id"]
    fsize = fam.value_counts()
    smp = lh[lh["day"] <= min(5, hist)].copy()
    smp["fam"] = smp["sku_id"].map(fam)
    m = smp.groupby(["order_id", "fam"]).size()
    n = m.index.get_level_values("fam").map(fsize).to_numpy(dtype=float)
    m = m.to_numpy(dtype=float)
    pco = float((m * (m - 1) / np.maximum(n - 1, 1)).sum() / m.sum())
    checks.append(Check("family_cooccurrence: P(other member | member present)", round(pco, 4), ">= 0.4", pco >= 0.4))

    # 3 month-3 shift
    seasonal, fading = set(meta.get("seasonal", [])), set(meta.get("fading", []))
    def share(df, group):
        return df["sku_id"].isin(group).mean() if len(df) else float("nan")
    r_seas = share(lo, seasonal) / share(lh, seasonal) if seasonal else float("nan")
    r_fade = share(lo, fading) / share(lh, fading) if fading else float("nan")
    checks.append(Check("month3_shift: seasonal line-share ratio (held-out / hist)", round(float(r_seas), 3), ">= 2.0", r_seas >= 2.0))
    checks.append(Check("month3_shift: fading line-share ratio (held-out / hist)", round(float(r_fade), 3), "<= 0.5", r_fade <= 0.5))
    dg = skus["demand_group"].value_counts() if "demand_group" in skus.columns else pd.Series(dtype=int)
    n_seas, n_fade = int(dg.get("seasonal", 0)), int(dg.get("fading", 0))
    checks.append(Check("month3_shift: skus.demand_group seasonal / fading counts", f"{n_seas}/{n_fade}", "120/120",
                        n_seas == 120 and n_fade == 120))
    dg_sets = skus.groupby("demand_group")["sku_id"].apply(set) if len(dg) else {}
    consistent = dg_sets.get("seasonal", set()) == seasonal and dg_sets.get("fading", set()) == fading
    checks.append(Check("month3_shift: demand_group matches generated groups", int(consistent), "1", consistent))

    # 4 stale layout
    top200 = v.sort_values(ascending=False).index[:200]
    aisles = cur.loc[top200, "active_loc"].map(_loc_aisle)
    far_frac = float(aisles.isin(FAR).mean())
    checks.append(Check("stale_layout: top-200 SKUs in aisles 1-5/24-28", round(far_frac, 4), "0.35..0.55", 0.35 <= far_frac <= 0.55))
    upp = skus.set_index("sku_id")["units_per_pallet"]
    nonpal = lh[lh["qty"] != lh["sku_id"].map(upp).to_numpy()]
    upd = (nonpal.groupby("sku_id")["qty"].sum() / n_hist_days).reindex(skus["sku_id"], fill_value=0)
    top300 = v.sort_values(ascending=False).index[:300]
    cover = cur.loc[top300, "active_capacity_units"] / upd.loc[top300].replace(0, np.nan)
    n_under = int((cover < 1.0).sum())
    checks.append(Check("stale_layout: top-300 SKUs with < 1 day active cover", n_under, "20..60", 20 <= n_under <= 60))

    trips = float((upd / cur.loc[upd.index, "active_capacity_units"]).sum())
    checks.append(Check("stale_layout: implied replen trips/day (current, hist)", round(trips, 1), "<= 560 (pool ~576-640)", trips <= 560))
    # 5 tight PM
    zone = cur["active_loc"].map(_loc_zone)
    zl = v.groupby(zone.reindex(v.index)).sum() / ZONE_CAP_LINES_PER_DAY
    peak = float(zl.max()) if len(zl) else 0.0
    checks.append(Check("tight_pm: peak PM zone expected load (hist)", round(peak, 3), "1.10..1.30", 1.10 <= peak <= 1.30))

    # 6 rush
    rush = float((orders["priority"] == "rush").mean())
    checks.append(Check("rush_orders: rush order share", round(rush, 4), "0.04..0.06", 0.04 <= rush <= 0.06))

    # 7 pallet
    pal = float((lines["qty"] == lines["sku_id"].map(upp).to_numpy()).mean())
    checks.append(Check("pallet_lines: full-pallet line share", round(pal, 4), "0.02..0.04", 0.02 <= pal <= 0.04))

    # 8 key customer
    c1 = lines.merge(orders[["order_id", "customer_id"]], on="order_id")
    c1_share = float((c1["customer_id"] == "C001").mean())
    checks.append(Check("key_customer: C001 share of all lines", round(c1_share, 4), "0.06..0.10", 0.06 <= c1_share <= 0.10))
    dist = cur["active_loc"].map(lambda l: aisle_distance(int(l[1:3])) if l.startswith("A") else 0)
    score = (v * dist.reindex(v.index)).sort_values(ascending=False)
    key = set(score[score > 0].index[:300]) | seasonal
    c1l = c1[c1["customer_id"] == "C001"]
    kshare = float(c1l["sku_id"].isin(key).mean()) if len(c1l) else 0.0
    checks.append(Check("key_customer: C001 lines in top-300 value-of-move + seasonal", round(kshare, 4), ">= 0.6", kshare >= 0.6))

    # target PM: every zone under the slotter cap (target 2 of spec 9.3a wants peak 0.85-1.0 after the reslot)
    tz = t["slotting_target"].set_index("sku_id")["active_loc"].map(_loc_zone)
    tzl = v.groupby(tz.reindex(v.index)).sum()
    tpeak = float(tzl.max()) / TARGET_ZONE_CAPACITY if len(tzl) else 0.0
    tshare = float(tzl.sum() / v.sum())
    checks.append(Check("target: peak PM zone load (hist lines/day / 4147)", round(tpeak, 3), "<= 0.95", tpeak <= 0.95))
    checks.append(Check("target: PM share of hist lines", round(tshare, 3), "0.60..0.75", 0.60 <= tshare <= 0.75))
    checks.append(Check("info: target PM zone lines/day", [int(x) for x in tzl.reindex(range(1, 7), fill_value=0)], "balanced, <= 3800", True))
    # reslot size: changed slots and implied legs (destination occupied in current = swap through staging = 3 legs)
    tgt_loc, cur_loc = t["slotting_target"]["active_loc"], t["slotting_current"]["active_loc"]
    moved = tgt_loc[tgt_loc.to_numpy() != cur_loc.to_numpy()]
    n_changed = int(len(moved))
    n_swaps = int(moved.isin(set(cur_loc)).sum())
    legs = n_changed + 2 * n_swaps
    checks.append(Check("reslot: SKU active slots changed current->target", n_changed, "900..1300", 900 <= n_changed <= 1300))
    checks.append(Check("reslot: implied legs (swap 3, direct 1)", legs, "1800..2600", 1800 <= legs <= 2600))
    checks.append(Check("info: moves into a currently occupied slot (swaps)", n_swaps, "~400-550", True))
    lpo = len(lines) / max(1, len(orders))
    checks.append(Check("info: lines per order", round(lpo, 2), "~12", True))
    return checks


def print_report(checks: list[Check]) -> None:
    w = max(len(c.name) for c in checks)
    print(f"{'check':<{w}}  {'measured':>10}  {'target':<32}  result")
    for c in checks:
        print(f"{c.name:<{w}}  {c.measured!s:>10}  {c.target:<32}  {'PASS' if c.ok else 'FAIL'}")
    print("ALL PASS" if all(c.ok for c in checks) else "SOME CHECKS FAILED")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    a = ap.parse_args(argv)
    checks = verify(a.db or db_path_from_env())
    print_report(checks)
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
