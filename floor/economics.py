"""`price_option`: labor cost, overtime cost and expected payback of an option (spec 9.6)."""

from __future__ import annotations

from floor import constants as K


def price_option(option: dict, evidence: dict | None = None) -> dict:
    """Price an option (a dict with `levers`: list of lever dicts or contract levers).

    Returns `{labor_cost_usd, overtime_cost_usd, temp_cost_usd, move_cost_usd, total_usd,
    seconds_saved_per_day, payback_days}`. `evidence` may carry `seconds_saved_per_day` (from
    `phaser.rank_moves`) and `kpis_total` (a `Kpis` or dict) to refine the payback.
    """
    levers = option.get("levers", []) if isinstance(option, dict) else getattr(option, "levers", [])
    ot = temp = moves = 0.0
    for lv in levers:
        g = (lambda k, d=None: lv.get(k, d)) if isinstance(lv, dict) else (lambda k, d=None: getattr(lv, k, d))
        kind = g("kind")
        if kind == "overtime":
            ot += float(g("hours", 0)) * K.HOURLY_WAGE * K.OVERTIME_MULT * 4  # ~4 people-hours per approved hour
        elif kind == "hire_temps":
            temp += int(g("count", 0)) * int(g("days", 0)) * K.SHIFT_HOURS * K.TEMP_WAGE
        elif kind == "reslot_tonight":
            moves += int(g("n_moves", 0)) * K.MOVE_COST_USD
        elif kind == "labor_allocate":
            pass
    saved = float((evidence or {}).get("seconds_saved_per_day", 0.0))
    usd_per_sec = K.HOURLY_WAGE / 3600.0
    daily_gain = saved * usd_per_sec
    total = ot + temp + moves
    payback = total / daily_gain if daily_gain > 0 else float("inf")
    return {"labor_cost_usd": round(ot + temp, 2), "overtime_cost_usd": round(ot, 2), "temp_cost_usd": round(temp, 2),
            "move_cost_usd": round(moves, 2), "total_usd": round(total, 2),
            "seconds_saved_per_day": round(saved, 1), "payback_days": round(payback, 1)}
