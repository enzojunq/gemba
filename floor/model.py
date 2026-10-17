"""`FloorState`: private, picklable, copy-on-write at day granularity (CONTRACT 3.6).

Every mutating entry point in the twin calls `state.copy()` first and mutates the copy.
Numpy arrays are per-SKU (indexed by `Inputs.sku_index`) so the hourly sim stays vectorized.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np

import contracts as c
from floor import constants as K


@dataclass
class Move:
    """One planned SKU move. `legs` is 1 (direct) or 3 (swap through staging). `decision_id` is set
    when approved for a night; `legs_done` counts executed legs; `staging_slot` the slot held mid-swap."""
    sku: int
    to_loc: int
    legs: int
    value: float            # seconds saved per day
    decision_id: str = ""
    legs_done: int = 0
    staging_slot: int = -1
    approved: bool = False
    seq: int = 0            # approval order (select_tonight order)
    from_loc: int = -1      # source slot once the first leg has started


@dataclass
class ActiveEffect:
    kind: str
    event_id: str
    until_day: int          # inclusive
    args: dict
    start_day: int
    start_hour: int = 6


@dataclass
class FloorState:
    seed: int
    day: int
    scenario_id: str = ""
    # slotting
    sku_loc: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int32))
    sku_cap: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=float))
    inventory: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=float))
    loc_occupant: dict[int, int] = field(default_factory=dict)   # loc idx -> sku idx (active slots only)
    # reslot
    moves: list[Move] = field(default_factory=list)              # the full plan (queue + done)
    moves_target_total: int = 0                                  # legs in the plan at t0
    moves_done_total: int = 0                                    # legs executed so far
    staging: dict[int, tuple[int, str]] = field(default_factory=dict)   # slot -> (sku idx, decision_id)
    staging_movers: int = 0                                      # staging_clearance lever (tonight)
    paused_until: int = 0
    # labor
    labor: dict[str, dict[str, int]] = field(default_factory=dict)        # shift -> pool -> headcount
    overtime_today: dict[tuple[str, str], tuple[float, str]] = field(default_factory=dict)  # (shift,pool) -> (hours, dec)
    overtime_history: list[tuple[int, str, float, str]] = field(default_factory=list)  # (day, pool, hours, dec)
    temps: list[tuple[int, int, int, str]] = field(default_factory=list)   # (count, start, end, dec)
    wave_mode: str = "rush_first"
    # aisles & protection
    closed_aisles: dict[int, tuple[int, str | None]] = field(default_factory=dict)   # aisle -> (until, cause)
    frozen_aisles: dict[int, tuple[int, str | None]] = field(default_factory=dict)   # no moves; aisle -> (until, cause)
    protections: list[c.Protection] = field(default_factory=list)
    # dips and timers
    dip_day: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int32))   # day the SKU was moved (night of)
    dip_decision: dict[int, str] = field(default_factory=dict)                            # sku idx -> decision id
    carry_work: dict[str, float] = field(default_factory=dict)   # pool -> seconds of backlog from yesterday
    # events
    effects: list[ActiveEffect] = field(default_factory=list)
    near_misses: list[tuple[int, int, str | None]] = field(default_factory=list)   # (day, aisle, cause)
    # history
    kpi_history: list[dict] = field(default_factory=list)       # last 7 days: day, on_time, key_mispick, ...
    recal: dict[str, float] = field(default_factory=dict)       # family id -> factor
    violations_pending: list[str] = field(default_factory=list)
    key_customer: str = ""
    surge_shift: int = 0        # perturbation: scripted demand_surge windows are pre-shifted in `effects`

    def copy(self) -> "FloorState":
        return copy.deepcopy(self)

    # ------------------------------------------------------------------ derived
    def closed_now(self, day: int) -> list[int]:
        return sorted(a for a, (until, _) in self.closed_aisles.items() if until >= day)

    def frozen_now(self, day: int) -> list[int]:
        return sorted(a for a, (until, _) in self.frozen_aisles.items() if until >= day)

    def temps_active(self, day: int) -> int:
        return min(K.TEMP_POOL_MAX, sum(n for n, a, b, _ in self.temps if a <= day <= b))

    def temps_ramping(self, day: int) -> int:
        """Temps still inside their first 5 working days."""
        return min(K.TEMP_POOL_MAX, sum(n for n, a, b, _ in self.temps if a <= day <= b and day - a < K.TEMP_RAMP_DAYS))

    def dip_mask(self, day: int) -> np.ndarray:
        """SKUs in a learning dip on `day`: moved on the night of day-1 or day-2."""
        return (self.dip_day > 0) & (day - self.dip_day >= 1) & (day - self.dip_day <= K.DIP_DAYS)

    def effect_active(self, kind: str, day: int) -> list[ActiveEffect]:
        return [e for e in self.effects if e.kind == kind and e.start_day <= day <= e.until_day]

    def protected_skus(self) -> set[int]:
        out: set[int] = set()
        for p in self.protections:
            if p.target == "skus":
                out.update(int(i) for i in p.ids if str(i).isdigit())
        return out

    def queue_legs(self) -> int:
        return sum(m.legs - m.legs_done for m in self.moves if m.legs_done < m.legs)

    def staging_pct(self) -> float:
        return 100.0 * len(self.staging) / K.STAGING_SLOTS

    def protections_active(self, day: int) -> list[c.Protection]:
        return [p for p in self.protections if p.until_day >= day]
