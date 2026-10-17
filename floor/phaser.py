"""`rank_moves` and `select_tonight` (spec 9.6): which SKU moves, in what order, tonight."""

from __future__ import annotations

import numpy as np

from floor import constants as K
from floor.inputs import Inputs
from floor.model import FloorState, Move


def build_plan(inputs: Inputs, state: FloorState, target_loc: np.ndarray, velocity: np.ndarray) -> list[Move]:
    """Diff current slotting vs target into `Move`s. A move into an occupied slot is a swap (3 legs
    through staging); into an empty slot it is 1 leg. Value = seconds saved per day."""
    moves: list[Move] = []
    occupied = {int(l) for l in state.sku_loc if l >= 0}
    for s in range(len(target_loc)):
        t, cur = int(target_loc[s]), int(state.sku_loc[s])
        if t < 0 or t == cur:
            continue
        legs = 3 if t in occupied else 1
        saved = float(velocity[s]) * float(inputs.base_service[cur] - inputs.base_service[t]) if cur >= 0 else 0.0
        moves.append(Move(sku=s, to_loc=t, legs=legs, value=saved))
    return moves


def rank_moves(inputs: Inputs, state: FloorState, moves: list[Move] | None = None) -> list[dict]:
    """Rank pending moves by value per leg (seconds saved per day / legs). Returns dicts
    `{sku_id, from, to, legs, seconds_saved_per_day, value_per_leg, payback_days, decision_id}`
    sorted best first. Payback = move cost / daily saving in wage terms."""
    moves = state.moves if moves is None else moves
    sku_ids = inputs.skus["sku_id"].tolist()
    loc_ids = inputs.locs["loc_id"].tolist()
    out = []
    usd_per_sec = K.HOURLY_WAGE / 3600.0
    for m in moves:
        if m.legs_done >= m.legs:
            continue
        legs_left = m.legs - m.legs_done
        cost = legs_left * K.MOVE_COST_USD
        gain = m.value * usd_per_sec
        out.append({"sku_id": sku_ids[m.sku], "from": loc_ids[int(state.sku_loc[m.sku])] if state.sku_loc[m.sku] >= 0 else None,
                    "to": loc_ids[m.to_loc], "legs": legs_left, "seconds_saved_per_day": round(m.value, 1),
                    "value_per_leg": m.value / legs_left, "payback_days": round(cost / gain, 1) if gain > 0 else float("inf"),
                    "decision_id": m.decision_id, "_move": m})
    out.sort(key=lambda r: (-r["value_per_leg"], r["sku_id"]))
    return out


def select_tonight(ranked: list[dict], budget: int, include_zones: list[int] | None = None,
                   exclude_zones: list[int] | None = None, protect_skus: set[str] | None = None,
                   frozen: list[int] | None = None, order: str = "value", inputs: Inputs | None = None) -> list[dict]:
    """Pick tonight's moves from `ranked` (output of `rank_moves`) within `budget` legs.

    Skips SKUs in `protect_skus`, moves whose source or destination aisle is in `frozen`, and
    PM destinations outside `include_zones` / inside `exclude_zones`. `order` = value (default),
    zone (group by destination zone) or family (group by family id). Partially-executed swaps
    (legs already done) are always taken first so staging gets freed."""
    include = set(int(z) for z in (include_zones or []))
    exclude = set(int(z) for z in (exclude_zones or []))
    protect = protect_skus or set()
    froz = set(frozen or [])

    def aisle_of(loc: str | None) -> int:
        return int(loc[1:3]) if loc and loc.startswith("A") else 0

    def zone_of(loc: str | None) -> int:
        return int(loc[2]) if loc and loc.startswith("PM") else 0

    cands = []
    for r in ranked:
        if r["sku_id"] in protect:
            continue
        if aisle_of(r["from"]) in froz or aisle_of(r["to"]) in froz:
            continue
        z = zone_of(r["to"])
        if include and z and z not in include:
            continue
        if z in exclude and z:
            continue
        cands.append(r)
    started = [r for r in cands if r["_move"].legs_done > 0]
    rest = [r for r in cands if r["_move"].legs_done == 0]
    if order == "zone":
        rest.sort(key=lambda r: (zone_of(r["to"]) or 99, -r["value_per_leg"]))
    elif order == "family" and inputs is not None:
        fam = inputs.skus.set_index("sku_id")["family_id"]
        rest.sort(key=lambda r: (fam.get(r["sku_id"], ""), -r["value_per_leg"]))
    chosen, legs = [], 0
    for r in started + rest:
        if legs + r["legs"] > budget:
            continue
        chosen.append(r)
        legs += r["legs"]
        if legs >= budget:
            break
    return chosen
