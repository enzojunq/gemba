"""The one scoring formula (spec 10.4), used for options, days, runs, eval and the gate.

    score = sum_k w_k * clip(sign_k * (KPI_k - REF_k) / SCALE_k, -1, 1)

| KPI k               | sign | SCALE                 | run aggregation |
|---------------------|------|-----------------------|-----------------|
| on_time_pct         | +    | 5 points              | mean            |
| mispick_rate        | -    | 0.5 points            | mean            |
| lph                 | +    | 10% of REF (relative) | mean            |
| reslot_progress_pct | +    | 100 points (REF = 0)  | end value       |
| labor_cost_usd      | -    | 10% of REF (relative) | sum             |

REF for an option = the do-nothing what-if over the same horizon on the same forecast;
REF for a day or a run = the do-nothing baseline run on the same scenario.

Resolved ambiguities:
- reslot_progress_pct uses REF = 0 whatever the reference says, i.e. its term is
  w * clip(progress / 100, -1, 1): absolute progress is rewarded, not progress relative to
  the do-nothing reference.
- Relative scales (lph, labor_cost_usd) with REF == 0 fall back to SCALE = 1.0 so the
  formula never divides by zero (the clip still bounds the term to +-w).
- Weight keys must be KPI names AND have a row in the table above (spec: "weight keys are
  exactly the KPI names above"); anything else is rejected when the profile loads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from contracts import KPI_NAMES, Kpis, Profile, WhatIf

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"

# --------------------------------------------------------------------------- scoring table

SCORING_TABLE: dict[str, tuple[int, float, bool]] = {
    # kpi: (sign, scale, relative). relative=True means SCALE = scale * |REF|.
    "on_time_pct": (+1, 5.0, False),
    "mispick_rate": (-1, 0.5, False),
    "lph": (+1, 0.10, True),
    "reslot_progress_pct": (+1, 100.0, False),
    "labor_cost_usd": (-1, 0.10, True),
}
ZERO_REF_KPIS: tuple[str, ...] = ("reslot_progress_pct",)
"""KPIs scored against REF = 0 (absolute), whatever the reference run says."""

AGGREGATION: dict[str, Literal["mean", "sum", "max", "end"]] = {
    "lines": "sum",
    "lph": "mean",
    "throughput_lph_site": "mean",
    "on_time_pct": "mean",
    "mispick_rate": "mean",
    "pm_peak_zone_load": "max",
    "consolidator_queue_max": "max",
    "jam_hours": "sum",
    "replen_trips": "sum",
    "stockout_lines": "sum",
    "overtime_hours": "sum",
    "labor_cost_usd": "sum",
    "moves_done": "sum",
    "moves_queued": "end",
    "reslot_progress_pct": "end",
    "staging_occupancy_max": "max",
    "safety_risk_max": "max",
    "near_misses": "sum",
    "mispick_rate_by_customer": "mean",  # per customer, over the days that customer appears
}
"""Same mapping as the `contracts.WhatIf` docstring, so WhatIf.kpis_total and run
aggregation agree."""

assert set(AGGREGATION) == set(KPI_NAMES) | {"mispick_rate_by_customer"}


class Score(BaseModel):
    total: float
    terms: dict[str, float]
    """Per-KPI weighted contribution (for the UI); sums to `total`."""


class Feasibility(BaseModel):
    feasible: bool
    breaches: dict[str, float]
    """metric -> value - limit, only for limits that are breached."""

    @property
    def total_breach(self) -> float:
        return sum(self.breaches.values())


# --------------------------------------------------------------------------- profiles


def validate_profile(profile: Profile) -> Profile:
    unknown = [k for k in profile.weights if k not in KPI_NAMES]
    if unknown:
        raise ValueError(f"profile {profile.id}: weight keys are not KPI names: {unknown}")
    unscored = [k for k in profile.weights if k not in SCORING_TABLE]
    if unscored:
        raise ValueError(f"profile {profile.id}: no scoring rule for weight keys {unscored}")
    total = sum(profile.weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"profile {profile.id}: weights sum to {total}, expected 1.0")
    return profile


def load_profile(path_or_id: str | Path) -> Profile:
    """Load and validate a profile by id ("customer_first" -> profiles/customer_first.yaml)
    or by path."""
    p = Path(path_or_id)
    if not p.exists() and p.suffix == "":
        p = PROFILES_DIR / f"{path_or_id}.yaml"
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return validate_profile(Profile.model_validate(data))


# --------------------------------------------------------------------------- scoring


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_kpis(kpis: Kpis, ref: Kpis, profile: Profile) -> Score:
    terms: dict[str, float] = {}
    for k, w in profile.weights.items():
        sign, scale, relative = SCORING_TABLE[k]
        value = float(getattr(kpis, k))
        ref_value = 0.0 if k in ZERO_REF_KPIS else float(getattr(ref, k))
        if relative:
            scale = scale * abs(ref_value) if ref_value != 0 else 1.0
        terms[k] = w * _clip(sign * (value - ref_value) / scale)
    return Score(total=sum(terms.values()), terms=terms)


def score_whatif(option: WhatIf, ref: WhatIf, profile: Profile) -> Score:
    return score_kpis(option.kpis_total, ref.kpis_total, profile)


def aggregate_run(days: list[Kpis]) -> Kpis:
    """Aggregate per-day KPIs into one Kpis using AGGREGATION (int fields are rounded)."""
    if not days:
        raise ValueError("aggregate_run needs at least one day")
    out: dict[str, object] = {}
    for name in KPI_NAMES:
        vals = [float(getattr(d, name)) for d in days]
        how = AGGREGATION[name]
        if how == "mean":
            v = sum(vals) / len(vals)
        elif how == "sum":
            v = sum(vals)
        elif how == "max":
            v = max(vals)
        else:  # end
            v = vals[-1]
        if Kpis.model_fields[name].annotation in (int, "int"):
            v = int(round(v))
        out[name] = v
    by_cust: dict[str, list[float]] = {}
    for d in days:
        for c, r in d.mispick_rate_by_customer.items():
            by_cust.setdefault(c, []).append(r)
    out["mispick_rate_by_customer"] = {c: sum(v) / len(v) for c, v in by_cust.items()}
    return Kpis.model_validate(out)


# --------------------------------------------------------------------------- feasibility


def feasibility(option: WhatIf, profile: Profile, ref: WhatIf | None = None) -> Feasibility:
    """Check an option's what-if against the profile hard limits (spec 10.4): infeasible if
    expected near-misses, safety_risk_max or PM peak zone load exceed their limits.

    With `ref` (the do-nothing what-if over the same horizon): when the reference already
    breaches a limit, the floor is in breach whatever the org does, so an option breaches
    only if it is worse than the reference: `value > max(limit, ref_value)`, and the breach
    amount is `value - max(limit, ref_value)`."""
    values = _limit_values(option)
    ref_values = _limit_values(ref) if ref is not None else {}
    breaches: dict[str, float] = {}
    for m, limit in profile.hard_limits.items():
        if m not in values:
            continue
        bar = max(limit, ref_values.get(m, limit))
        if values[m] > bar:
            breaches[m] = values[m] - bar
    return Feasibility(feasible=not breaches, breaches=breaches)


def _limit_values(w: WhatIf) -> dict[str, float]:
    return {
        "expected_near_misses": w.expected_near_misses,
        "safety_risk_max": w.safety_risk_max,
        "pm_peak_zone_load": w.pm_peak_zone_load,
    }


def least_bad(options: list[Feasibility]) -> int:
    """Index of the option with the smallest total breach (first wins ties). The standup uses
    it when every option is infeasible: the chair takes that one and escalates."""
    if not options:
        raise ValueError("least_bad needs at least one option")
    return min(range(len(options)), key=lambda i: options[i].total_breach)
