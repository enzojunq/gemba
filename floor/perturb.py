"""Scenario perturbation (spec 13.1): `family_scale_pct`, `seasonal_group_seed`, `surge_shift_days`.

Deterministic from `(scenario.seed, perturbation)`. An empty perturbation changes nothing, so the
default scenario stays byte-identical. Line scaling is a seeded resample: a line with factor f is
kept floor(f) times plus one more with probability frac(f), drawn from a hash so forks agree.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from floor.inputs import Inputs

SEASONAL_FACTOR = 3.0
FADING_FACTOR = 0.3
GROUP_SIZE = 120
MONTH3_START = 61


@dataclass
class Perturbation:
    seed: int
    family_scale_pct: float = 0.0
    seasonal_group_seed: int | None = None
    surge_shift_days: int = 0
    sku_factor: np.ndarray | None = None       # per sku: family scale (all days)
    regroup_factor: np.ndarray | None = None   # per sku: seasonal regroup (days >= 61 only)

    @property
    def empty(self) -> bool:
        return self.sku_factor is None and self.regroup_factor is None

    @classmethod
    def build(cls, inputs: Inputs, seed: int, raw: dict | None) -> "Perturbation":
        raw = raw or {}
        p = cls(seed=seed, family_scale_pct=float(raw.get("family_scale_pct", 0) or 0),
                seasonal_group_seed=raw.get("seasonal_group_seed"), surge_shift_days=int(raw.get("surge_shift_days", 0) or 0))
        n = len(inputs.sku_index)
        if p.family_scale_pct:
            rng = np.random.default_rng([int(seed), 4242, int(abs(p.family_scale_pct) * 100)])
            fam_factor = 1.0 + (p.family_scale_pct / 100.0) * rng.random(len(inputs.family_ids))
            p.sku_factor = fam_factor[inputs.family_of_sku]
        if p.seasonal_group_seed is not None:
            rng = np.random.default_rng([int(p.seasonal_group_seed), 777])
            pick = rng.choice(n, size=min(2 * GROUP_SIZE, n), replace=False)
            new_seasonal, new_fading = pick[:GROUP_SIZE], pick[GROUP_SIZE:]
            f = np.ones(n)
            if inputs.demand_group is not None:  # undo the baked-in month-3 shift first
                f[inputs.demand_group == "seasonal"] /= SEASONAL_FACTOR
                f[inputs.demand_group == "fading"] /= FADING_FACTOR
            f[new_seasonal] *= SEASONAL_FACTOR
            f[new_fading] *= FADING_FACTOR
            p.regroup_factor = f
        return p

    def factor_for(self, source_day: int) -> np.ndarray | None:
        """Per-SKU factor for lines that come from `source_day` (regroup applies to month-3 days only)."""
        if self.empty:
            return None
        f = self.sku_factor.copy() if self.sku_factor is not None else None
        if self.regroup_factor is not None and source_day >= MONTH3_START:
            f = self.regroup_factor.copy() if f is None else f * self.regroup_factor
        return f


def hash_u01_ints(*parts: np.ndarray | int) -> np.ndarray:
    from floor.sim import hash_u01
    return hash_u01(*parts)


def scale_lines(lines: pd.DataFrame, factor: np.ndarray, seed: int, day: int, salt: int,
                order_codes: np.ndarray) -> pd.DataFrame:
    """Resample `lines` so each line appears floor(f) times plus one with probability frac(f).
    `factor` is per line; `order_codes` are ints per line keying the draw with `(seed, day, salt)`."""
    if len(lines) == 0 or np.allclose(factor, 1.0):
        return lines
    reps = np.floor(factor).astype(int)
    frac = factor - reps
    u = hash_u01_ints(seed, day, salt, order_codes, lines["line_no"].to_numpy(dtype=np.int64))
    n = reps + (u < frac).astype(int)
    parts = [lines[n >= 1]]
    for k in range(2, int(n.max()) + 1 if len(n) else 1):
        sel = lines[n >= k].copy()
        if len(sel):
            sel["line_no"] = sel["line_no"] + 1000 * (k - 1)
            parts.append(sel)
    out = pd.concat(parts, ignore_index=True)
    return out.sort_values(["order_id", "line_no"], kind="stable").reset_index(drop=True)
