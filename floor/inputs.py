"""Loader for `inputs.db` (spec 8.1). Loads everything once per twin instance into numpy/pandas.

Only `contracts`, stdlib, numpy, pandas and sqlalchemy are imported here (CONTRACT rule 4).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import sqlalchemy as sa

import contracts as c
from floor import constants as K

LOC_RE = re.compile(r"^(?:A(?P<aisle>\d{2})-(?P<bay>\d{2})-(?P<level>\d)|PM(?P<zone>\d)-(?P<pslot>\d{3})|ST-(?P<sslot>\d{3}))$")


def parse_loc(loc_id: str) -> tuple[str, int, int, int]:
    """-> (kind, aisle_or_zone_or_slot, bay, level). kind in pm|aisle|staging."""
    m = LOC_RE.match(loc_id)
    if not m:
        raise ValueError(f"bad location id {loc_id!r}")
    if m.group("aisle"):
        return "aisle", int(m.group("aisle")), int(m.group("bay")), int(m.group("level"))
    if m.group("zone"):
        return "pm", int(m.group("zone")), int(m.group("pslot")), 1
    return "staging", int(m.group("sslot")), 0, 1


def aisle_distance_m(aisle: int) -> float:
    """Spec 7.1: aisles 14/15 are 10 m from the consolidator, +4 m per aisle away."""
    return 10.0 + 4.0 * (min(abs(aisle - 14), abs(aisle - 15)))


@dataclass
class Inputs:
    """Everything the twin needs from the WMS export, indexed for numpy."""
    url: str
    skus: pd.DataFrame                      # index sku_id; family_id, units_per_pallet, ...
    sku_index: dict[str, int]
    family_of_sku: np.ndarray               # int family code per sku idx
    family_ids: list[str]
    locs: pd.DataFrame                      # index loc_id; kind, aisle, zone, bay, level, distance_m, capacity_units
    loc_index: dict[str, int]
    loc_kind: np.ndarray                    # 0 pm, 1 aisle, 2 staging
    loc_aisle: np.ndarray                   # aisle (1..28) or 0
    loc_zone: np.ndarray                    # zone (1..6) or 0
    loc_level: np.ndarray
    loc_distance: np.ndarray
    loc_capacity: np.ndarray
    base_service: np.ndarray                # seconds per line at each loc (pm 25 / forklift formula)
    current_loc: np.ndarray                 # loc idx per sku (slotting_current)
    current_cap: np.ndarray                 # active capacity units per sku
    target_loc: np.ndarray                  # loc idx per sku (slotting_target), -1 if none
    target_cap: np.ndarray
    customers: pd.DataFrame
    key_customers: list[str]
    orders: pd.DataFrame                    # all days; columns order_id, day, release_hour, customer_id, priority, cutoff_hour
    lines: pd.DataFrame                     # all days; order_id, line_no, sku_id, qty, day
    day_slices: dict[int, tuple[slice, slice]] = field(default_factory=dict)  # day -> (orders slice, lines slice)
    headcount: dict[str, dict[str, int]] = field(default_factory=dict)   # shift -> pool -> default from roster
    calendar: pd.DataFrame | None = None    # labor_calendar
    cutoffs: tuple[float, ...] = K.CUTOFFS
    velocity_hist: np.ndarray | None = None  # lines/day per sku over days 1..60
    history_days: int = 60
    demand_group: np.ndarray | None = None  # per sku: seasonal | fading | normal (None if the column is absent)

    # ------------------------------------------------------------------ aliases (scenario ids)
    def family_mask(self, family: str) -> np.ndarray:
        """Boolean mask over sku idx for a family id, or the aliases `seasonal` / `fading`
        (resolved through `skus.demand_group`; no match when the column is absent)."""
        if family in ("seasonal", "fading"):
            if self.demand_group is None:
                return np.zeros(len(self.sku_index), dtype=bool)
            return self.demand_group == family
        return self.family_of_sku == (self.family_ids.index(family) if family in self.family_ids else -1)

    def customer_alias(self, customer: str) -> str:
        """`KEY1` (B's placeholder) means the hero key customer (C001)."""
        if customer in ("KEY1", "key") and self.key_customers:
            return self.key_customers[0]
        return customer

    # ------------------------------------------------------------------ demand
    def demand(self, day: int) -> c.DayDemand:
        """Actual demand for `day` (held-out or history); empty frames when the day is absent."""
        if day not in self.day_slices:
            return c.DayDemand(day=day, orders=self.orders.iloc[0:0].drop(columns=["day"]).copy(),
                               lines=self.lines.iloc[0:0].drop(columns=["day"]).copy())
        so, sl = self.day_slices[day]
        orders = self.orders.iloc[so].drop(columns=["day"]).reset_index(drop=True)
        lines = self.lines.iloc[sl].drop(columns=["day"]).reset_index(drop=True)
        return c.DayDemand(day=day, orders=orders, lines=lines)

    def sku_velocity(self, days: range) -> np.ndarray:
        """Lines per day per sku over `days` (only days present in the data)."""
        out = np.zeros(len(self.sku_index), dtype=float)
        n = 0
        for d in days:
            if d in self.day_slices:
                sl = self.day_slices[d][1]
                idx = self.lines["sku_idx"].values[sl]
                np.add.at(out, idx, 1.0)
                n += 1
        return out / max(n, 1)

    def sku_daily_units(self, days: range) -> np.ndarray:
        out = np.zeros(len(self.sku_index), dtype=float)
        n = 0
        for d in days:
            if d in self.day_slices:
                sl = self.day_slices[d][1]
                np.add.at(out, self.lines["sku_idx"].values[sl], self.lines["qty"].values[sl].astype(float))
                n += 1
        return out / max(n, 1)


def _read(engine: sa.Engine, table: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql_table(table, conn)


def load_inputs(url: str | None = None) -> Inputs:
    url = url or os.environ.get("INPUTS_DB_URL", "sqlite:///inputs.db")
    engine = sa.create_engine(url)
    insp = sa.inspect(engine)
    tables = set(insp.get_table_names())
    if "skus" not in tables:
        raise FileNotFoundError(f"inputs db at {url} has no `skus` table")

    skus = _read(engine, "skus").sort_values("sku_id").reset_index(drop=True)
    sku_index = {s: i for i, s in enumerate(skus["sku_id"].tolist())}
    fam_codes, fam_ids = pd.factorize(skus["family_id"], sort=True)

    locs = _read(engine, "locations").sort_values("loc_id").reset_index(drop=True)
    loc_index = {l: i for i, l in enumerate(locs["loc_id"].tolist())}
    parsed = [parse_loc(l) for l in locs["loc_id"].tolist()]
    kind_map = {"pm": 0, "aisle": 1, "staging": 2}
    loc_kind = np.array([kind_map[p[0]] for p in parsed], dtype=np.int8)
    loc_aisle = np.array([p[1] if p[0] == "aisle" else 0 for p in parsed], dtype=np.int16)
    loc_zone = np.array([p[1] if p[0] == "pm" else 0 for p in parsed], dtype=np.int16)
    loc_level = np.array([p[3] for p in parsed], dtype=np.int16)
    dist_table: dict[int, float] = {}
    if "aisles" in tables:
        adf = _read(engine, "aisles")
        if {"aisle_id", "distance_m"} <= set(adf.columns):
            dist_table = {int(a): float(d) for a, d in zip(adf["aisle_id"], adf["distance_m"])}
    loc_distance = np.array([(dist_table.get(int(a), aisle_distance_m(int(a))) if a else 0.0) for a in loc_aisle], dtype=float)
    cap_col = locs["capacity_units"] if "capacity_units" in locs else pd.Series(np.full(len(locs), 200.0))
    loc_capacity = cap_col.fillna(200).to_numpy(dtype=float)
    base_service = np.where(loc_kind == 0, K.PM_SECONDS,
                            K.FORKLIFT_BASE + K.FORKLIFT_PER_M * loc_distance + K.FORKLIFT_PER_LEVEL * (loc_level - 1))
    base_service = np.where(loc_kind == 2, K.FORKLIFT_BASE + K.FORKLIFT_PER_M * 70.0, base_service).astype(float)

    def slot_arrays(table: str) -> tuple[np.ndarray, np.ndarray]:
        loc_arr = np.full(len(skus), -1, dtype=np.int32)
        cap_arr = np.full(len(skus), 100.0, dtype=float)
        if table not in tables:
            return loc_arr, cap_arr
        df = _read(engine, table)
        for sid, loc, cap in zip(df["sku_id"], df["active_loc"], df.get("active_capacity_units", pd.Series([100.0] * len(df)))):
            if sid in sku_index and isinstance(loc, str) and loc in loc_index:
                loc_arr[sku_index[sid]] = loc_index[loc]
                cap_arr[sku_index[sid]] = float(cap) if pd.notna(cap) else 100.0
        return loc_arr, cap_arr

    current_loc, current_cap = slot_arrays("slotting_current")
    target_loc, target_cap = slot_arrays("slotting_target")

    customers = _read(engine, "customers") if "customers" in tables else pd.DataFrame({"customer_id": [], "tier": []})
    key_customers = customers.loc[customers.get("tier", pd.Series(dtype=str)) == "key", "customer_id"].tolist()

    orders = _read(engine, "orders")
    lines = _read(engine, "order_lines")
    if "priority" not in orders:
        orders["priority"] = "normal"
    if "line_no" not in lines:
        lines["line_no"] = lines.groupby("order_id").cumcount() + 1
    if "day" not in lines:
        lines["day"] = lines["order_id"].map(orders.set_index("order_id")["day"])
    orders = orders.sort_values(["day", "release_hour", "order_id"], kind="stable").reset_index(drop=True)
    lines = lines.sort_values(["day", "order_id", "line_no"], kind="stable").reset_index(drop=True)
    lines["sku_idx"] = lines["sku_id"].map(sku_index).fillna(-1).astype(np.int32)
    lines["qty"] = lines["qty"].astype(np.int32)
    orders["release_hour"] = orders["release_hour"].astype(float)
    orders["cutoff_hour"] = orders["cutoff_hour"].astype(float)
    orders["day"] = orders["day"].astype(int)
    lines["day"] = lines["day"].astype(int)

    day_slices: dict[int, tuple[slice, slice]] = {}
    od = orders["day"].to_numpy()
    ld = lines["day"].to_numpy()
    for d in np.unique(od):
        o0, o1 = np.searchsorted(od, d, "left"), np.searchsorted(od, d, "right")
        l0, l1 = np.searchsorted(ld, d, "left"), np.searchsorted(ld, d, "right")
        day_slices[int(d)] = (slice(int(o0), int(o1)), slice(int(l0), int(l1)))

    headcount = {s: dict(p) for s, p in K.DEFAULT_HEADCOUNT.items()}
    if "labor_roster" in tables:
        roster = _read(engine, "labor_roster")
        if {"role", "shift"} <= set(roster.columns):
            reg = roster[~roster.get("is_temp", pd.Series(False, index=roster.index)).astype(bool)]
            counts = reg.groupby(["shift", "role"]).size()
            for (shift, role), n in counts.items():
                if shift in headcount and role in headcount[shift]:
                    headcount[shift][role] = int(n)
    calendar = _read(engine, "labor_calendar") if "labor_calendar" in tables else None

    cutoffs: tuple[float, ...] = K.CUTOFFS
    if "site" in tables:
        site = _read(engine, "site")
        if len(site) and "cutoffs" in site:
            raw = site["cutoffs"].iloc[0]
            try:
                cutoffs = tuple(float(x) for x in (json.loads(raw) if isinstance(raw, str) else raw))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass

    inp = Inputs(url=url, skus=skus, sku_index=sku_index, family_of_sku=fam_codes.astype(np.int32),
                 family_ids=list(fam_ids), locs=locs, loc_index=loc_index, loc_kind=loc_kind, loc_aisle=loc_aisle,
                 loc_zone=loc_zone, loc_level=loc_level, loc_distance=loc_distance, loc_capacity=loc_capacity,
                 base_service=base_service, current_loc=current_loc, current_cap=current_cap,
                 target_loc=target_loc, target_cap=target_cap, customers=customers, key_customers=key_customers,
                 orders=orders, lines=lines, day_slices=day_slices, headcount=headcount, calendar=calendar,
                 cutoffs=cutoffs)
    if "demand_group" in skus.columns:
        inp.demand_group = skus["demand_group"].fillna("normal").to_numpy(dtype=object)
    inp.velocity_hist = inp.sku_velocity(range(1, inp.history_days + 1))
    return inp
