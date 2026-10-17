# `DayResult.hourly` format (owned by Person A)

Written by `floor/sim.py`, read by `console/static/floor.js`, stored by B as opaque JSON.
Native Python types only (no numpy scalars or arrays). Keep it small: target < 60 KB per day.

Static geometry (28 aisles, PM zones, consolidator, dock, staging) is **not** in the payload;
the console hardcodes it from spec 7.1. The payload carries only what changes hour to hour.

```json
{
  "version": 1,
  "day": 61,
  "hours": [
    {
      "hour": 6,                       // 0-23; the day runs 6..23 then 0..5 (night of the same day)
      "phase": "day",                  // "day" (06-14) | "swing" (14-22) | "night" (22-06)
      "pools": {                       // one entry per contracts.Pool that has anyone scheduled
        "pm":           {"available": 12, "busy": 11, "queue": 420, "served": 1700},
        "forklift":     {"available": 14, "busy": 14, "queue": 900, "served": 1250},
        "pallet":       {"available": 3,  "busy": 2,  "queue": 10,  "served": 55},
        "replen":       {"available": 4,  "busy": 4,  "queue": 6,   "served": 38},
        "consolidation":{"available": 10, "busy": 9,  "queue": 180, "served": 160},
        "loading":      {"available": 6,  "busy": 5,  "queue": 90,  "served": 140},
        "night_move":   {"available": 0,  "busy": 0,  "queue": 0,   "served": 0},
        "temp":         {"available": 0,  "busy": 0,  "queue": 0,   "served": 0}
      },
      "pm_zones": [0.92, 1.05, 0.88, 0.97, 0.80, 0.91],   // load ratio per zone 1..6 (1.0 = capacity)
      "aisles": [0.3, 0.4, ...],        // 28 floats: forklift work in the aisle / its share of capacity
      "aisle_people": [1, 2, ...],      // 28 ints: people + trucks present (safety uses this)
      "safety_risk": [0.3, 0.6, ...],   // 28 floats: aisle_people / safe limit (3)
      "frozen_aisles": [7],             // closed by rack_damage / close_aisle / leadership_visit
      "consolidator_open": 310,         // open orders at end of hour
      "jam": false,                     // consolidator_open > 400 this hour
      "trucks": {"loaded": 140, "late": 3, "next_cutoff": 13},   // next_cutoff null after 21:30
      "staging_occupancy": 62.0,        // % of the 150 staging slots
      "stockouts": 12,                  // lines waiting on replenishment this hour
      "mispicks": 9,                    // mis-picks drawn this hour
      "near_miss_aisle": null,          // int aisle if a near-miss was drawn this hour
      "moves": {                        // night hours only; zeros/empty by day
        "done": 24,
        "sample": [{"sku": "S0412", "from": "A03-12-1", "to": "PM2-044"}]   // <= 10 per hour, for the animation
      }
    }
  ]
}
```

Notes
- `served` and `queue` are lines (orders for `consolidation` and `loading`).
- Night hours (22..5) carry `moves`; day hours carry zeros there. Night hours still report pools so the canvas can show the movers.
- Location ids: aisle slot `A<aisle:02>-<bay:02>-<level>`, PM slot `PM<zone>-<slot:03>`, staging `ST-<slot:03>`.
- Anything absent is treated as 0 / empty by the console. Never add numpy types.
