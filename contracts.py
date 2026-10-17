"""Shared contract between Person A (floor/data/console) and Person B (org/engine/learning).

See CONTRACT.md. Changes after 11:45 require a joint `contract:` commit.

Conventions: ids are strings; days are integers 1-90; hours 0-23 (the night belongs to
the day it starts on); percentages are 0-100 everywhere.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Protocol, Union, runtime_checkable

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------- 3.1 ids

DEC = "dec:"
EVT = "evt:"
MTG = "mtg:"
CON = "con:"
KPI = "kpi:"
NODE_PREFIXES = (DEC, EVT, MTG, CON, KPI)

CausedBy = str | None
"""A typed node id (see prefixes above), or None for root causes (scripted events)."""


def node_id(prefix: str, id: str) -> str:
    """`node_id(DEC, "d1") -> "dec:d1"`. Accepts the prefix with or without the trailing colon."""
    p = prefix if prefix.endswith(":") else prefix + ":"
    if p not in NODE_PREFIXES:
        raise ValueError(f"unknown node prefix {prefix!r}")
    return p + id


def kpi_node_id(run_id: str, day: int, kpi_name: str) -> str:
    """`kpi:<run_id>:<day>:<kpi_name>`."""
    return f"{KPI}{run_id}:{day}:{kpi_name}"


# --------------------------------------------------------------------------- literals

Pool = Literal["pm", "forklift", "pallet", "replen", "consolidation", "loading", "night_move", "temp"]
Shift = Literal["day", "swing", "night"]

ExogenousKind = Literal[
    "labor_shortage", "demand_surge", "rack_damage", "pm_zone_down", "leadership_visit",
    "key_customer_week", "new_sku_launch", "returns_spike", "wms_sync_delay", "forklift_breakdown",
]
ConsequenceKind = Literal[
    "customer_escalation", "absenteeism", "staging_congestion", "safety_review",
    "aisle_closure", "director_escalation", "replen_alert",
]
EventKind = Literal[
    # exogenous (spec 9.5)
    "labor_shortage", "demand_surge", "rack_damage", "pm_zone_down", "leadership_visit",
    "key_customer_week", "new_sku_launch", "returns_spike", "wms_sync_delay", "forklift_breakdown",
    # consequence (spec 9.4)
    "customer_escalation", "absenteeism", "staging_congestion", "safety_review",
    "aisle_closure", "director_escalation", "replen_alert",
]

# --------------------------------------------------------------------------- 3.2 levers


class _LeverBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    decision_id: str


class ReslotTonight(_LeverBase):
    kind: Literal["reslot_tonight"] = "reslot_tonight"
    n_moves: int = Field(ge=0)
    order: Literal["value", "zone", "family"] = "value"
    include_zones: list[int] = []
    exclude_zones: list[int] = []


class PauseReslot(_LeverBase):
    kind: Literal["pause_reslot"] = "pause_reslot"
    days: int = Field(ge=1)


class LaborAllocate(_LeverBase):
    kind: Literal["labor_allocate"] = "labor_allocate"
    shift: Shift
    pool: Pool
    headcount: int = Field(ge=0)


class Overtime(_LeverBase):
    kind: Literal["overtime"] = "overtime"
    shift: Shift
    pool: Pool
    hours: float = Field(ge=0)


class HireTemps(_LeverBase):
    kind: Literal["hire_temps"] = "hire_temps"
    count: int = Field(ge=1)
    start_day: int
    days: int = Field(ge=1)


class Protect(_LeverBase):
    kind: Literal["protect"] = "protect"
    target: Literal["zone", "skus", "customer"]
    ids: list[str]
    days: int = Field(ge=1)


class ResizeSlots(_LeverBase):
    kind: Literal["resize_slots"] = "resize_slots"
    skus: list[str]
    days_cover: float = Field(gt=0)


class WavePriority(_LeverBase):
    kind: Literal["wave_priority"] = "wave_priority"
    mode: Literal["rush_first", "by_cutoff", "by_zone"]


class CloseAisle(_LeverBase):
    kind: Literal["close_aisle"] = "close_aisle"
    aisle: int
    days: int = Field(ge=1)


class ReopenAisle(_LeverBase):
    kind: Literal["reopen_aisle"] = "reopen_aisle"
    aisle: int


class StagingClearance(_LeverBase):
    kind: Literal["staging_clearance"] = "staging_clearance"
    n_movers: int = Field(ge=1)


Lever = Annotated[
    Union[
        ReslotTonight, PauseReslot, LaborAllocate, Overtime, HireTemps, Protect,
        ResizeSlots, WavePriority, CloseAisle, ReopenAisle, StagingClearance,
    ],
    Field(discriminator="kind"),
]
"""Floor levers only. `set_profile` and `escalate` never reach the twin (handled by B)."""

LEVER_KINDS: tuple[str, ...] = (
    "reslot_tonight", "pause_reslot", "labor_allocate", "overtime", "hire_temps", "protect",
    "resize_slots", "wave_priority", "close_aisle", "reopen_aisle", "staging_clearance",
)


class LeverRejection(BaseModel):
    """Physical invalidity only (unknown aisle/zone/SKU, headcount above roster, ...).
    Authority is checked by B before the twin is called."""
    lever: Lever
    reason: str


# --------------------------------------------------------------------------- 3.4 events


class ScenarioEvent(BaseModel):
    """An event as written in a scenario YAML (no event_id/announced_day/source/caused_by)."""
    kind: EventKind
    day: int
    hour: int = Field(default=6, ge=0, le=23)
    args: dict[str, Any] = {}


class Event(ScenarioEvent):
    event_id: str
    announced_day: int
    source: Literal["scripted", "inbox", "consequence"]
    caused_by: CausedBy = None


EVENT_ARGS: dict[str, list[str]] = {
    # exogenous (spec 9.5). demand_surge takes exactly one of skus | family | customer.
    "labor_shortage": ["pool", "pct", "days"],
    "demand_surge": ["skus", "family", "customer", "factor", "days"],
    "rack_damage": ["aisle", "days"],
    "pm_zone_down": ["zone", "hours"],
    "leadership_visit": ["no_moves_in_aisles"],  # the visit day is Event.day
    "key_customer_week": ["customer", "days"],
    "new_sku_launch": ["n"],  # the launch day is Event.day
    "returns_spike": ["pct", "days"],
    "wms_sync_delay": ["days"],
    "forklift_breakdown": ["count", "days"],
    # consequence (spec 9.4)
    "customer_escalation": ["customer"],     # C2
    "absenteeism": ["pool", "pct", "days"],  # C3: +15%, 3 days
    "staging_congestion": ["occupancy"],     # C4: staging occupancy % at 06:00
    "safety_review": ["aisle"],              # C5
    "aisle_closure": ["aisle", "days"],      # C5: second near-miss within 5 days
    "director_escalation": ["metric"],       # C6: e.g. "on_time_pct"
    "replen_alert": ["stockout_pct"],        # C7
}

ANNOUNCE_LEAD_DAYS: dict[str, int] = {
    "demand_surge": 2, "key_customer_week": 3, "leadership_visit": 5, "returns_spike": 1,
    "new_sku_launch": 3, "labor_shortage": 0, "rack_damage": 0, "pm_zone_down": 0,
    "wms_sync_delay": 0, "forklift_breakdown": 0,
    "customer_escalation": 0, "absenteeism": 0, "staging_congestion": 0, "safety_review": 0,
    "aisle_closure": 0, "director_escalation": 0, "replen_alert": 0,
}


def announced_day(kind: str, day: int) -> int:
    return day - ANNOUNCE_LEAD_DAYS[kind]


def visible(events: list[Event], today: int) -> list[Event]:
    """Events agents and what-ifs may see: announced_day <= today."""
    return [e for e in events if e.announced_day <= today]


def active(events: list[Event], day: int) -> list[Event]:
    """Events that start on `day` (what `step_day` receives). A multi-day event (e.g.
    rack_damage for 3 days) is delivered once, on its start day; the twin holds the effect
    in its state for the duration."""
    return [e for e in events if e.day == day]


# --------------------------------------------------------------------------- 3.5 demand


class DayDemand(BaseModel):
    """One day's demand.

    orders columns: order_id, release_hour, customer_id, priority ("normal"|"rush"), cutoff_hour
    lines  columns: order_id, line_no, sku_id, qty
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)
    day: int
    orders: pd.DataFrame
    lines: pd.DataFrame


# --------------------------------------------------------------------------- 3.6 floor view


class Protection(BaseModel):
    target: Literal["zone", "skus", "customer"]
    ids: list[str]
    until_day: int
    decision_id: str


class FloorView(BaseModel):
    """What B may read about the floor. B never reads FloorState."""
    day: int
    frozen_aisles: list[int]
    closed_aisles: list[int]
    protected: list[Protection]
    staging_occupancy: float
    move_queue: int
    moves_done_total: int
    moves_target_total: int
    reslot_progress_pct: float
    labor_available: dict[Shift, dict[Pool, int]]
    temps_active: int
    learning_dip_skus: int
    overtime_days_last4: dict[Pool, int]


# --------------------------------------------------------------------------- 3.7 results

KPI_NAMES: tuple[str, ...] = (
    "lines", "lph", "throughput_lph_site", "on_time_pct", "mispick_rate", "pm_peak_zone_load",
    "consolidator_queue_max", "jam_hours", "replen_trips", "stockout_lines", "overtime_hours",
    "labor_cost_usd", "moves_done", "moves_queued", "reslot_progress_pct",
    "staging_occupancy_max", "safety_risk_max", "near_misses",
)


class Kpis(BaseModel):
    """Raw day KPIs (spec 9.3). Percent fields are 0-100. In a WhatIf, near_misses is an
    expected value (hence float)."""
    lines: int = 0
    lph: float = 0.0  # lines per productive labor hour (PM, forklift, pallet, replen; no night moves)
    throughput_lph_site: float = 0.0  # lines per clock hour
    on_time_pct: float = 0.0
    mispick_rate: float = 0.0  # %
    pm_peak_zone_load: float = 0.0  # ratio, 1.0 = zone at capacity (profile hard limit 1.10)
    consolidator_queue_max: float = 0.0
    jam_hours: int = 0
    replen_trips: int = 0
    stockout_lines: int = 0
    overtime_hours: float = 0.0
    labor_cost_usd: float = 0.0
    moves_done: int = 0
    moves_queued: int = 0
    reslot_progress_pct: float = 0.0
    staging_occupancy_max: float = 0.0  # %
    safety_risk_max: float = 0.0  # people-and-trucks per aisle-hour / safe limit; > 1.0 is risk
    near_misses: float = 0.0
    mispick_rate_by_customer: dict[str, float] = {}  # %


class DayResult(BaseModel):
    day: int
    kpis: Kpis
    hourly: dict[str, Any] = {}  # owned by A; B stores it as opaque JSON
    violations: list[str] = []


class WhatIf(BaseModel):
    """Runs on forecast demand only; near-misses are expected values, not draws."""
    levers: list[Lever]
    days: int
    kpis_by_day: list[Kpis]
    kpis_total: Kpis
    expected_near_misses: float
    safety_risk_max: float
    pm_peak_zone_load: float


# --------------------------------------------------------------------------- 3.8 decision


class Decision(BaseModel):
    decision_id: str
    day: int
    levers: list[Lever]
    constraints: list[Event] = []
    decided_by: str
    rationale: str
    option_id: str | None = None  # None for baseline policies
    meeting_id: str | None = None


# --------------------------------------------------------------------------- 3.9 scenario / profile


class Scenario(BaseModel):
    id: str
    seed: int
    days: tuple[int, int]  # inclusive [start, end]
    events: list[ScenarioEvent] = []
    perturbation: dict[str, Any] = {}  # family_scale_pct, seasonal_group_seed, surge_shift_days
    overrides: dict[str, Any] = {}  # headcounts, budgets, ...

    @model_validator(mode="after")
    def _check_days(self) -> "Scenario":
        if self.days[0] > self.days[1]:
            raise ValueError("days must be [start, end] with start <= end")
        return self


class Profile(BaseModel):
    """Company scorecard (spec 10.4). Weight keys are KPI names; targets may include
    done-check-only keys such as lph_lift_pct; hard_limits may include expected_near_misses."""
    id: str
    weights: dict[str, float]
    targets: dict[str, float] = {}
    hard_limits: dict[str, float] = {}
    budgets: dict[str, float] = {}


# --------------------------------------------------------------------------- done-checks (spec 11.3)


DERIVED_METRICS: tuple[str, ...] = ("score", "lph_lift_pct", "expected_near_misses")
"""Non-KPI metrics a DoneCheck may target (spec 11.3 final scope): the profile scorecard,
LPH lift vs the do-nothing baseline, and expected near-misses from the chosen what-if.
Computed by B's verifier from KPIs + profile; never produced by the twin."""


class DoneCheck(BaseModel):
    """Built by code (verifier.template); the chair picks `threshold` within
    [min_threshold, max_threshold]. `metric` is a KPI name or one of DERIVED_METRICS."""
    check_name: str
    scope: Literal["daily", "final"]
    metric: str
    op: Literal["<=", ">="]
    threshold: float
    min_threshold: float
    max_threshold: float

    @field_validator("metric")
    @classmethod
    def _metric_is_kpi(cls, v: str) -> str:
        if v not in KPI_NAMES and v not in DERIVED_METRICS:
            raise ValueError(f"metric {v!r} is not a KPI name or derived metric")
        return v

    @model_validator(mode="after")
    def _threshold_in_bounds(self) -> "DoneCheck":
        if not (self.min_threshold <= self.threshold <= self.max_threshold):
            raise ValueError("threshold outside [min_threshold, max_threshold]")
        return self


# --------------------------------------------------------------------------- 4/5 protocols

FloorState = Any  # private to Person A: picklable, immutable per day (copy on write). B never inspects it.


@runtime_checkable
class FloorTwin(Protocol):
    def initial_state(self, scenario: Scenario) -> FloorState: ...
    def view(self, state: FloorState) -> FloorView: ...
    def apply_levers(self, state: FloorState, levers: list[Lever], day: int) -> tuple[FloorState, list[LeverRejection]]: ...
    def actual_demand(self, day: int) -> DayDemand: ...
    def step_day(self, state: FloorState, day: int, demand: DayDemand, events: list[Event]) -> tuple[FloorState, DayResult]: ...
    def consequences(self, state: FloorState, result: DayResult, run_id: str) -> list[Event]: ...  # future-dated, caused_by set
    def forecast_demand(self, state: FloorState, day: int, horizon: int, known_events: list[Event]) -> list[DayDemand]: ...
    def whatif(self, state: FloorState, levers: list[Lever], days: int, known_events: list[Event]) -> WhatIf: ...
    def recalibrate(self, state: FloorState, actual: list[DayResult], predicted: list[Kpis]) -> FloorState: ...


@runtime_checkable
class DecisionPolicy(Protocol):
    def pick_thresholds(self, checks: list[DoneCheck]) -> list[DoneCheck]: ...
    def decide(self, day: int, view: FloorView, events: list[Event], twin: FloorTwin, state: FloorState) -> Decision: ...


# --------------------------------------------------------------------------- agent outputs (spec 10.6)


class Proposal(BaseModel):
    option_label: str
    levers: list[Lever]
    rationale: str  # <= 60 words


class Position(BaseModel):
    option_id: str
    stance: Literal["support", "oppose", "amend"]
    amendment: list[Lever] | None = None
    rationale: str
    evidence_ids: list[str]


class Veto(BaseModel):
    option_id: str
    rule: str
    evidence_ids: list[str]


class Lock(BaseModel):
    target: Literal["zone", "skus", "customer"]
    ids: list[str]
    days: int
    reason: str


class Hire(BaseModel):
    role: str
    trigger: str


class ChairTriage(BaseModel):
    meeting: bool
    agenda: list[str]
    hire: list[Hire]
    release: list[str]


class ChairDecision(BaseModel):
    option_id: str | None
    escalate: bool
    rationale: str
    evidence_ids: list[str]


class DirectorRuling(BaseModel):
    option_id: str | None
    set_profile: str | None
    override_lock: str | None
    rationale: str


class EventParse(BaseModel):
    """Inbox text -> typed event."""
    kind: str
    args: dict[str, Any]
    confidence: float


class Lesson(BaseModel):
    role: str
    tags: list[str]
    trigger: str
    advice: str  # <= 40 words
