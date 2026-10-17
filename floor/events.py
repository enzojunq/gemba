"""Apply exogenous and consequence events to the state (spec 9.5 / 9.4).

`step_day` receives each event once, on its start day; durations are held in `state.effects`.
Kinds used by `scenarios/reslot_default.yaml` are implemented fully; the rest have a minimal effect.
"""

from __future__ import annotations

import contracts as c
from floor import constants as K
from floor.model import ActiveEffect, FloorState


def apply_events(state: FloorState, events: list[c.Event], day: int) -> None:
    """Mutates `state` (callers pass a copy)."""
    for e in events:
        days = int(e.args.get("days", 1))
        until = day + days - 1
        cause = c.node_id(c.EVT, e.event_id)
        kind = e.kind
        if kind == "rack_damage":
            # the aisle stays open (picking continues at extra risk, no moves) until the org closes it
            a = int(e.args.get("aisle", 0))
            if 1 <= a <= K.N_AISLES:
                prev = state.damaged_aisles.get(a, (0, None))[0]
                state.damaged_aisles[a] = (max(prev, until), cause)
        elif kind == "aisle_closure":
            a = int(e.args.get("aisle", 0))
            if 1 <= a <= K.N_AISLES:
                prev = state.closed_aisles.get(a, (0, None))[0]
                state.closed_aisles[a] = (max(prev, day + int(e.args.get("days", 3)) - 1), cause)
        elif kind == "leadership_visit":
            for a in e.args.get("no_moves_in_aisles", []):
                if 1 <= int(a) <= K.N_AISLES:
                    state.frozen_aisles[int(a)] = (day, cause)
        elif kind == "safety_review":
            a = int(e.args.get("aisle", 0))
            if 1 <= a <= K.N_AISLES:
                state.frozen_aisles[a] = (max(state.frozen_aisles.get(a, (0, None))[0], day), cause)
        elif kind == "pm_zone_down":
            state.effects.append(ActiveEffect(kind=kind, event_id=e.event_id, until_day=day, args=dict(e.args),
                                              start_day=day, start_hour=int(e.hour)))
        elif kind in ("labor_shortage", "absenteeism", "forklift_breakdown", "demand_surge", "key_customer_week",
                      "wms_sync_delay", "returns_spike", "new_sku_launch"):
            if kind == "demand_surge" and state.surge_shift and e.source == "scripted":
                continue  # already in `effects`, shifted by the scenario perturbation
            if kind == "new_sku_launch":
                until = day + 6   # unslotted for a week (stub)
            state.effects.append(ActiveEffect(kind=kind, event_id=e.event_id, until_day=until, args=dict(e.args),
                                              start_day=day, start_hour=int(e.hour)))
        elif kind in ("customer_escalation", "director_escalation", "replen_alert", "staging_congestion"):
            pass  # org-side consequences; no physical effect on the floor
    state.effects = [x for x in state.effects if x.until_day >= day - 1]
