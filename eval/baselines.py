"""LLM-free `contracts.DecisionPolicy` baselines (spec 15; decision 21: static rules use 200 moves/night)."""

from __future__ import annotations

import contracts as c


class _Baseline:
    name = "baseline"

    def __init__(self, run_id: str = ""):
        self.run_id = run_id

    def pick_thresholds(self, checks: list[c.DoneCheck]) -> list[c.DoneCheck]:
        return list(checks)

    def _decision(self, day: int, levers: list[c.Lever], rationale: str) -> c.Decision:
        did = f"{self.run_id}-d{day}" if self.run_id else f"d{day}"
        return c.Decision(decision_id=did, day=day, levers=[lv.model_copy(update={"decision_id": did}) for lv in levers],
                          decided_by=self.name, rationale=rationale, option_id=None)


class DoNothing(_Baseline):
    name = "do_nothing"

    def decide(self, day, view, events, twin, state) -> c.Decision:
        return self._decision(day, [], "Do nothing: current layout, default labor.")


class NaiveBigBang(_Baseline):
    """Reslot 1200/night on the run's first two days, temps once for those two days; ignores events."""
    name = "big_bang"

    def __init__(self, run_id: str = ""):
        super().__init__(run_id)
        self.first_day: int | None = None

    def decide(self, day, view, events, twin, state) -> c.Decision:
        if self.first_day is None:
            self.first_day = day
        levers: list[c.Lever] = []
        if day - self.first_day < 2:
            levers.append(c.ReslotTonight(n_moves=1200, order="value"))
        if day == self.first_day:
            levers.append(c.HireTemps(count=8, start_day=self.first_day, days=2))
        return self._decision(day, levers, "Big bang: move everything to target now, ignore constraints."
                              if levers else "Big bang: done, no further levers.")


class StaticRules(_Baseline):
    name = "static"
    MOVES_PER_NIGHT = 200

    def decide(self, day, view, events, twin, state) -> c.Decision:
        # Pause on exogenous events only (spec 15 "pause on any event day"); a baseline that
        # pauses on its own consequences (replen_alert, director_escalation) would be unfair
        # to itself (A/B agreement 13:20).
        if any(e.day == day and e.source != "consequence" for e in events):
            return self._decision(day, [c.PauseReslot(days=1)], "Static rules: event today, pause reslot.")
        return self._decision(day, [c.ReslotTonight(n_moves=self.MOVES_PER_NIGHT, order="value")],
                              f"Static rules: {self.MOVES_PER_NIGHT} moves tonight by value.")
