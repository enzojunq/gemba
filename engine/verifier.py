"""Done-check verifier (spec 11.3).

This module owns `template(profile)`: code builds every check from the profile's targets and
hard limits, with the bounds the chair may pick a threshold within. The chair only picks
thresholds (DecisionPolicy.pick_thresholds); it never invents checks.

Check names are `<scope>.<metric>`, e.g. `daily.on_time_pct`, `final.lph_lift_pct`.

Derived metrics (contracts.DERIVED_METRICS), computed by the check runner, never by the twin:
- `expected_near_misses`: from the chosen option's what-if for that day.
- `lph_lift_pct`: 100 * (run mean LPH - baseline mean LPH) / baseline mean LPH, where the
  baseline is the do-nothing run on the same scenario.
- `score`: `org.scoring.score_kpis(aggregate_run(run_days), aggregate_run(ref_days), profile)
  .total` with REF = the static-rules baseline run on the same scenario. Because the formula
  measures the run against its REF, "scorecard >= static-rules baseline" is exactly
  `score >= 0` (the baseline scored against itself is 0).

`check_day` / `check_final` return one `CheckResult` per check in scope; a derived metric
missing from `derived` is skipped (`passed=None, value=None`).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from contracts import DERIVED_METRICS, DoneCheck, Kpis, Profile

DEFAULT_MOVES_BUDGET = 200
"""Nightly reslot move budget (spec 7.1 default 200 legs/night; 11.3 "moves <= budget").
The chair may pick between 150 and the physical crew max of 280 (spec 7.3)."""
MIN_MOVES_BUDGET = 150
MAX_MOVES_BUDGET = 280


def _check(scope: str, metric: str, op: str, threshold: float, lo: float, hi: float) -> DoneCheck:
    return DoneCheck(
        check_name=f"{scope}.{metric}", scope=scope, metric=metric, op=op,
        threshold=float(threshold), min_threshold=float(lo), max_threshold=float(hi),
    )


def template(profile: Profile) -> list[DoneCheck]:
    """Build the daily and final done-checks for `profile`. Thresholds default to the natural
    value; [min_threshold, max_threshold] are the bounds the chair may choose within."""
    t = profile.targets
    hl = profile.hard_limits
    on_time = t.get("on_time_pct", 97.0)
    mispick = t.get("mispick_rate", 0.5)
    lph_lift = t.get("lph_lift_pct", 8.0)
    safety = hl.get("safety_risk_max", 1.0)
    pm_load = hl.get("pm_peak_zone_load", 1.10)
    near_miss = hl.get("expected_near_misses", 0.5)

    daily = [
        _check("daily", "on_time_pct", ">=", on_time - 2, on_time - 4, on_time),
        _check("daily", "mispick_rate", "<=", mispick, mispick / 2, mispick * 1.5),
        _check("daily", "jam_hours", "<=", 0, 0, 0),
        _check("daily", "moves_done", "<=", DEFAULT_MOVES_BUDGET, MIN_MOVES_BUDGET, MAX_MOVES_BUDGET),
        _check("daily", "safety_risk_max", "<=", safety, safety, safety),
        _check("daily", "pm_peak_zone_load", "<=", pm_load, pm_load, pm_load),
        _check("daily", "expected_near_misses", "<=", near_miss, near_miss, near_miss),
    ]
    final = [
        _check("final", "on_time_pct", ">=", on_time, on_time - 2, on_time + 1),
        _check("final", "mispick_rate", "<=", mispick, mispick / 2, mispick),
        _check("final", "lph_lift_pct", ">=", lph_lift, 4, 12),
        _check("final", "reslot_progress_pct", ">=", 90, 80, 100),
        _check("final", "score", ">=", 0, 0, 0),
    ]
    return daily + final


class CheckResult(BaseModel):
    """One evaluated check. `passed`/`value` are None when a derived metric was unavailable
    (the check is skipped, not failed). One `checks` row per result per day."""
    check_name: str
    scope: str
    passed: bool | None
    value: float | None
    threshold: float

    def row(self) -> dict:
        return self.model_dump()


def _value(metric: str, kpis: Kpis, derived: dict[str, Any] | None) -> float | None:
    if metric in DERIVED_METRICS:
        v = (derived or {}).get(metric)
        return None if v is None else float(v)
    return float(getattr(kpis, metric))


def _run(checks: list[DoneCheck], scope: str, kpis: Kpis, derived: dict[str, Any] | None) -> list[CheckResult]:
    out = []
    for ch in checks:
        if ch.scope != scope:
            continue
        v = _value(ch.metric, kpis, derived)
        passed = None if v is None else (v <= ch.threshold + EPS if ch.op == "<=" else v >= ch.threshold - EPS)
        out.append(CheckResult(check_name=ch.check_name, scope=ch.scope, passed=passed, value=v,
                               threshold=ch.threshold))
    return out


EPS = 1e-9


def check_day(checks: list[DoneCheck], kpis: Kpis, derived: dict[str, Any] | None = None) -> list[CheckResult]:
    """Evaluate every daily-scope check against one day's KPIs (+ derived metrics, e.g.
    `expected_near_misses` from the chosen what-if). Returns ALL results; filter on
    `passed is False` for failures."""
    return _run(checks, "daily", kpis, derived)


def check_final(checks: list[DoneCheck], kpis: Kpis, derived: dict[str, Any] | None = None) -> list[CheckResult]:
    """Evaluate every final-scope check against the aggregated run KPIs (`aggregate_run`) +
    derived metrics (`score` vs the REF run, `lph_lift_pct` vs the do-nothing run)."""
    return _run(checks, "final", kpis, derived)
