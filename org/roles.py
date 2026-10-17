"""Role specs (spec 10.2): load `org/roles/*.yaml`, validate them, load prompts, decide hires.

Hiring is decided by code (spec 10.5): `hire_triggers` is pure and deterministic. The LLM
only narrates it.
"""
from __future__ import annotations

import operator
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from contracts import EVENT_KINDS, KPI_NAMES, LEVER_KINDS

ORG_DIR = Path(__file__).resolve().parent
ROLES_DIR = ORG_DIR / "roles"
PROMPTS_DIR = ORG_DIR / "prompts"

Power = Literal["vote", "veto", "lock", "chair", "director", "capacity_truth", "none"]

META_POWERS: tuple[str, ...] = (
    "set_profile", "override_lock", "break_tie",           # director
    "escalate", "set_agenda", "hire_roles", "decide",       # chair
)
"""Non-floor powers. They never become `Lever`s and never reach the twin."""

SIGNAL_TRIGGERS: tuple[str, ...] = ("chair_escalates", "veto_deadlock", "overtime_requested")
"""Trigger strings that are meeting signals rather than `EventKind`s. The engine/standup
passes them in `events_today` when they happen."""

LOCK_MAX_DAYS_ALONE = 3

_COND = re.compile(r"^\s*([a-z_]+)\s*(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")
_OPS = {">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le}


def parse_condition(trigger: str) -> tuple[str, str, float] | None:
    """`"mispick_rate > 0.6"` -> `("mispick_rate", ">", 0.6)`; None if not a KPI condition."""
    m = _COND.match(trigger)
    return (m.group(1), m.group(2), float(m.group(3))) if m else None


class Role(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    title: str
    model_tier: Literal["ultra", "super", "nano"]
    character: str
    owns_kpis: list[str] = []
    power: Power
    levers_can_propose: list[str] = []
    levers_can_issue: list[str] = []
    meta_powers: list[str] = []
    runs_simulator: bool = False
    attends: Literal["always", "on_trigger"]
    triggers: list[str] = []
    red_lines: list[str] = []
    prompt_path: str | None = None

    @field_validator("owns_kpis")
    @classmethod
    def _kpis(cls, v: list[str]) -> list[str]:
        bad = [k for k in v if k not in KPI_NAMES]
        if bad:
            raise ValueError(f"unknown KPI names {bad}")
        return v

    @field_validator("levers_can_propose", "levers_can_issue")
    @classmethod
    def _levers(cls, v: list[str]) -> list[str]:
        bad = [k for k in v if k not in LEVER_KINDS]
        if bad:
            raise ValueError(f"unknown lever kinds {bad}")
        return v

    @field_validator("meta_powers")
    @classmethod
    def _meta(cls, v: list[str]) -> list[str]:
        bad = [k for k in v if k not in META_POWERS]
        if bad:
            raise ValueError(f"unknown meta powers {bad}")
        return v

    @field_validator("triggers")
    @classmethod
    def _triggers(cls, v: list[str]) -> list[str]:
        for t in v:
            cond = parse_condition(t)
            if cond is not None:
                if cond[0] not in KPI_NAMES:
                    raise ValueError(f"trigger {t!r}: unknown KPI {cond[0]!r}")
            elif t not in EVENT_KINDS and t not in SIGNAL_TRIGGERS:
                raise ValueError(f"trigger {t!r} is not an event kind, signal or KPI condition")
        return v


def load_roles(dir: str | Path = ROLES_DIR) -> dict[str, Role]:
    """Load and validate every `<id>.yaml` in `dir`. Raises ValueError on an invalid org."""
    d = Path(dir)
    if not d.is_absolute() and not d.exists():
        d = ORG_DIR.parent / d
    roles: dict[str, Role] = {}
    for f in sorted(d.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        data.setdefault("prompt_path", str(PROMPTS_DIR / f"{data['id']}.md"))
        role = Role(**data)
        if role.id != f.stem:
            raise ValueError(f"{f.name}: id {role.id!r} does not match file name")
        roles[role.id] = role
    _validate_org(roles)
    return roles


def _validate_org(roles: dict[str, Role]) -> None:
    for power in ("chair", "director"):
        n = sum(r.power == power for r in roles.values())
        if n != 1:
            raise ValueError(f"expected exactly one {power}, found {n}")
    if sum(r.runs_simulator for r in roles.values()) != 1:
        raise ValueError("expected exactly one role with runs_simulator: true")
    for r in roles.values():
        if r.attends == "always" and r.triggers:
            raise ValueError(f"{r.id}: attends always but has triggers")
        if r.attends == "on_trigger" and not r.triggers:
            raise ValueError(f"{r.id}: attends on_trigger but has no triggers")


def chair(roles: dict[str, Role]) -> Role:
    return next(r for r in roles.values() if r.power == "chair")


def director(roles: dict[str, Role]) -> Role:
    return next(r for r in roles.values() if r.power == "director")


def always_present(roles: dict[str, Role]) -> set[str]:
    return {r.id for r in roles.values() if r.attends == "always"}


def role_prompt(role: Role) -> str:
    """The static system prompt for `role` (KV-cache stable; see llm.prompt.build_messages)."""
    path = Path(role.prompt_path) if role.prompt_path else PROMPTS_DIR / f"{role.id}.md"
    return path.read_text(encoding="utf-8")


def meta_prompt(name: Literal["event_interpreter", "retro_coach"]) -> str:
    """Prompts for the meta agents that are not in the org chart."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def trigger_fires(trigger: str, events_today: set[str], kpis_yesterday: dict[str, float]) -> bool:
    cond = parse_condition(trigger)
    if cond is None:
        return trigger in events_today
    name, op, x = cond
    value = kpis_yesterday.get(name)
    return value is not None and _OPS[op](float(value), x)


def hire_triggers(
    roles: dict[str, Role], events_today: list[str], kpis_yesterday: dict[str, float]
) -> dict[str, str]:
    """`{role_id: trigger}` for every on-trigger role whose trigger fired.

    `events_today`: event kinds of today's and newly announced events, plus any meeting
    signals (`SIGNAL_TRIGGERS`). `kpis_yesterday`: yesterday's KPI values by name. The first
    matching trigger in the role's YAML order is reported. Pure and deterministic.
    """
    seen = set(events_today)
    fired: dict[str, str] = {}
    for rid in sorted(roles):
        role = roles[rid]
        if role.attends != "on_trigger":
            continue
        for t in role.triggers:
            if trigger_fires(t, seen, kpis_yesterday):
                fired[rid] = t
                break
    return fired
