"""Drift detectors (spec 11.4). One `DriftDetector` per run; its counters are plain data so
they pickle into the day snapshot and a fork resumes them.

`check(day, ctx)` reads a small context dict the engine builds each day:
- `spec_hashes`: list[str]   what-if evidence spec hashes requested today (org only)
- `reslot_n_moves`: int | None   n_moves of today's reslot_tonight lever (None = no reslot lever)
- `moves_done`: int              moves executed tonight (from the day's KPIs)
- `explained_stall`: bool        an active constraint/event/pause explains no progress
- `actual`: Kpis, `predicted`: Kpis | None
- `llm_usd`: float, `budget_usd`: float | None

Resolutions: each detector fires once when its streak/count reaches the trigger, then its
streak resets (so a 6-day stall is two flags, not four). A kind flagged for the second time
(and later) gets `response="shrink_or_stop"`; the chair chooses, baselines just log it.
`overrun` fires once per run. `prediction_error` keeps `kind` but its first response is
`recalibrate`; the engine recalibrates on every prediction_error flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

REPEAT_WHATIF_N = 3
CHURN_DAYS = 3
STALL_DAYS = 3
PRED_ERR_PCT = 0.15
PRED_ERR_DAYS = 2
PRED_ERR_KPIS = ("on_time_pct", "lph")

DEFAULT_RESPONSE = {
    "repeat_whatif": "continue", "plan_churn": "replan", "stall": "replan",
    "prediction_error": "recalibrate", "overrun": "shrink",
}
RECUR = "shrink_or_stop"


class DriftFlag(BaseModel):
    kind: str
    detail: str
    response: str

    def row(self) -> dict:
        return self.model_dump()


@dataclass
class DriftDetector:
    whatif_counts: dict[str, int] = field(default_factory=dict)
    last_n_moves: int | None = None
    has_plan: bool = False
    churn_streak: int = 0
    stall_streak: int = 0
    pred_streak: int = 0
    overrun_fired: bool = False
    flag_counts: dict[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------------ snapshot
    def counters(self) -> dict[str, Any]:
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in self.__dict__.items()}

    @classmethod
    def from_counters(cls, d: dict[str, Any] | None) -> "DriftDetector":
        return cls(**{k: (dict(v) if isinstance(v, dict) else v) for k, v in (d or {}).items()})

    # ------------------------------------------------------------------ detectors
    def check(self, day: int, ctx: dict[str, Any]) -> list[DriftFlag]:
        raw: list[tuple[str, str]] = []
        raw += self._repeat_whatif(ctx.get("spec_hashes") or [])
        raw += self._plan_churn(ctx.get("reslot_n_moves"))
        raw += self._stall(int(ctx.get("moves_done") or 0), bool(ctx.get("explained_stall")))
        raw += self._prediction_error(ctx.get("actual"), ctx.get("predicted"))
        raw += self._overrun(float(ctx.get("llm_usd") or 0.0), ctx.get("budget_usd"))
        out = []
        for kind, detail in raw:
            n = self.flag_counts.get(kind, 0) + 1
            self.flag_counts[kind] = n
            out.append(DriftFlag(kind=kind, detail=f"day {day}: {detail}",
                                 response=RECUR if n >= 2 else DEFAULT_RESPONSE[kind]))
        return out

    def _repeat_whatif(self, hashes: list[str]) -> list[tuple[str, str]]:
        out = []
        for h in hashes:
            n = self.whatif_counts.get(h, 0) + 1
            self.whatif_counts[h] = n
            if n == REPEAT_WHATIF_N:
                out.append(("repeat_whatif", f"what-if spec {h} requested {n} times"))
        return out

    def _plan_churn(self, n_moves: int | None) -> list[tuple[str, str]]:
        cur = n_moves or 0
        if self.has_plan and cur != (self.last_n_moves or 0):
            self.churn_streak += 1
        else:
            self.churn_streak = 0
        prev = self.last_n_moves
        self.last_n_moves, self.has_plan = cur, True
        if self.churn_streak >= CHURN_DAYS:
            self.churn_streak = 0
            return [("plan_churn", f"reslot plan changed {CHURN_DAYS} days in a row ({prev} -> {cur})")]
        return []

    def _stall(self, moves_done: int, explained: bool) -> list[tuple[str, str]]:
        if moves_done > 0 or explained:
            self.stall_streak = 0
            return []
        self.stall_streak += 1
        if self.stall_streak >= STALL_DAYS:
            self.stall_streak = 0
            return [("stall", f"no reslot progress for {STALL_DAYS} days, no constraint explains it")]
        return []

    def _prediction_error(self, actual, predicted) -> list[tuple[str, str]]:
        if actual is None or predicted is None:
            self.pred_streak = 0
            return []
        gaps = {}
        for k in PRED_ERR_KPIS:
            p, a = float(getattr(predicted, k)), float(getattr(actual, k))
            if p != 0 and abs(a - p) / abs(p) > PRED_ERR_PCT:
                gaps[k] = round(abs(a - p) / abs(p) * 100, 1)
        if not gaps:
            self.pred_streak = 0
            return []
        self.pred_streak += 1
        if self.pred_streak >= PRED_ERR_DAYS:
            self.pred_streak = 0
            return [("prediction_error", f"actual vs predicted gap > {PRED_ERR_PCT:.0%} for "
                                         f"{PRED_ERR_DAYS} days: {gaps} (%)")]
        return []

    def _overrun(self, usd: float, budget: float | None) -> list[tuple[str, str]]:
        if budget is None or self.overrun_fired or usd <= budget:
            return []
        self.overrun_fired = True
        return [("overrun", f"LLM spend ${usd:.2f} above budget ${budget:.2f}")]
