"""Tunable constants of the floor twin (spec 7, 9.2, 9.3a). Tune here, never in the traps."""

from __future__ import annotations

N_AISLES = 28
N_ZONES = 6
PM_SLOTS_PER_ZONE = 120
STAGING_SLOTS = 150
CONSOLIDATOR_CAP = 400          # open orders; above this the consolidator jams
JAM_SLOWDOWN = 1.5              # closing time x1.5 while jammed
DOORS = 6
LOADERS_PER_DOOR = 2
ORDERS_PER_DOOR_HOUR = 120
PACK_LOAD_MIN = 40              # minutes from consolidated to loadable
CUTOFFS = (13.0, 17.0, 21.5)
NEXT_DAY_FIRST_CUTOFF = 24 + 13.0

# service times (seconds per line)
PM_SECONDS = 25.0
FORKLIFT_BASE = 25.0
FORKLIFT_PER_M = 0.65           # spec 0.8; tuned so normal days keep forklift at 80-95% without jamming the consolidator
FORKLIFT_PER_LEVEL = 4.0
PALLET_SECONDS = 180.0
CONSOLIDATE_SECONDS = 40.0
REPLEN_TRIP_SECONDS = 300.0
MOVE_LEG_SECONDS = 360.0
NIGHT_PRODUCTIVE_HOURS = 7
PRODUCTIVE_FACTOR = 0.9         # breaks
CARRY_HOURS = 2.0               # backlog carried to the next day is capped at this many hours per pool

# multipliers
DIP_TIME = 1.15
DIP_MISPICK = 3.0
DIP_DAYS = 2
CREW_LEGS_PER_NIGHT = 280       # 4 movers x 7 h x 10 legs: a night beyond this overloads putaway
DIP_OVERLOAD_MAX = 2.0          # dip multipliers (time and mis-pick) scale up to this x on overloaded nights
TEMP_TIME = 1.43
TEMP_PRODUCTIVITY = 0.7
TEMP_MISPICK = 2.0
TEMP_RAMP_DAYS = 5
CONGESTION_TIME = 1.10
CONGESTION_THRESHOLD = 90.0     # staging % above which adjacent aisles slow down
STAGING_ADJACENT_AISLES = (1, 2)
FROZEN_TO_PALLET = 1.5
PM_FLEX_PENALTY = 1.25          # a picker helping another zone / the PM works this much slower
FORKLIFT_HELPS_PM = True        # cross-trained forklift pickers pick in the PM when their queue is empty
FORKLIFT_HELP_FRACTION = 0.5    # share of idle forklift time that can go to the PM (set per calibration sweep)
WMS_MISPICK = 4.0
MISPICK_PM = 0.003
MISPICK_FORKLIFT = 0.005
MISPICK_PALLET = 0.005

# safety
SAFE_LIMIT = 3.0
NEAR_MISS_P = 0.03

# replenishment
REPLEN_TRIGGER = 0.10           # request a trip when inventory < this fraction of capacity (spec 9.2: lines wait at 0)
REPLEN_TRIP_SECONDS_CAL = 300.0 # calibration: 5 min/trip until lane A1 resizes slot capacities (spec says 6)
DEFAULT_DAYS_COVER = 1.5

# labor economics
HOURLY_WAGE = 22.0
TEMP_WAGE = 20.0
OVERTIME_MULT = 1.5
SHIFT_HOURS = 8
TEMP_POOL_MAX = 8
MOVE_COST_USD = 36.0            # one leg: 6 min of a mover plus handling

# consequences
C2_KEY_MISPICK_PCT = 0.8
C6_ON_TIME = 95.0               # profile target 97 - 2 (PROGRESS_A.md)
C4_STAGING = 90.0
C7_STOCKOUT_PCT = 1.0
C5_WINDOW_DAYS = 5

DEFAULT_HEADCOUNT: dict[str, dict[str, int]] = {
    "day": {"pm": 12, "forklift": 14, "replen": 4, "pallet": 3, "consolidation": 10, "loading": 6, "night_move": 0, "temp": 0},
    "swing": {"pm": 12, "forklift": 14, "replen": 4, "pallet": 3, "consolidation": 10, "loading": 6, "night_move": 0, "temp": 0},
    "night": {"pm": 0, "forklift": 0, "replen": 0, "pallet": 0, "consolidation": 0, "loading": 0, "night_move": 4, "temp": 0},
}
SHIFT_OF_HOUR = {**{h: "day" for h in range(6, 14)}, **{h: "swing" for h in range(14, 22)},
                 **{h: "night" for h in (22, 23, 0, 1, 2, 3, 4, 5)}}
DAY_HOURS = list(range(6, 24)) + list(range(0, 6))   # simulation order 06:00 -> 05:00
