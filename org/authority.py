"""Authority at commit time (spec 10.3, "How authority works at commit time").

The chair approves an *option*; each lever in it is then *issued* by its owner:
- owner in the room -> the owner issues it;
- owner is an on-call role who is absent -> the chair hires that role first (`needs_hire`);
- the lever needs the Director (hire_temps, overtime over budget) -> the meeting escalates
  unless the Director is already in the room.
Proposers may only table levers their role lists under `levers_can_propose`; others are
rejected before scoring and logged. Pure code, no LLM.
"""
from __future__ import annotations

import math
from pydantic import BaseModel

from contracts import Lever
from org.roles import LOCK_MAX_DAYS_ALONE, Role, always_present, chair, director

CHAIR_LEVERS = frozenset({"reslot_tonight", "pause_reslot", "resize_slots", "staging_clearance"})

_FIXED_OWNER: dict[str, str] = {
    "labor_allocate": "ops_manager", "wave_priority": "ops_manager",
    "close_aisle": "safety", "reopen_aisle": "safety",
}


class Rejected(BaseModel):
    lever: Lever
    role_id: str
    reason: str


class Ruling(BaseModel):
    issuer_role_id: str | None
    needs_hire: str | None = None
    needs_director: bool = False
    reason: str


class CommitPlan(BaseModel):
    issuable: list[tuple[Lever, str]]
    """(lever, issuer role id) that can be issued now, in option order."""
    hires: list[str]
    """On-call roles the chair must hire before their levers can be issued."""
    escalate: bool
    """True if any lever needs the Director and the Director is not in the room."""
    reasons: list[str]


def filter_proposal(role: Role, levers: list[Lever]) -> tuple[list[Lever], list[Rejected]]:
    """Drop levers `role` may not propose. Returns (kept, rejected)."""
    kept: list[Lever] = []
    rejected: list[Rejected] = []
    for lv in levers:
        if lv.kind in role.levers_can_propose:
            kept.append(lv)
        else:
            rejected.append(Rejected(
                lever=lv, role_id=role.id,
                reason=f"{role.id} may not propose {lv.kind} "
                       f"(may propose: {', '.join(role.levers_can_propose) or 'nothing'})",
            ))
    return kept, rejected


def _owner(lever: Lever, roles: dict[str, Role], overtime_remaining: float) -> tuple[str, str]:
    """(owner role id, reason) per the spec 10.3 'who may issue' column."""
    k = lever.kind
    if k in CHAIR_LEVERS:
        return chair(roles).id, f"{k} is issued by the chair"
    if k in _FIXED_OWNER:
        return _FIXED_OWNER[k], f"{k} is issued by {_FIXED_OWNER[k]}"
    if k == "overtime":
        if lever.hours <= overtime_remaining:
            return "shift_lead", (f"overtime {lever.hours:g}h within policy "
                                  f"({overtime_remaining:g}h remaining)")
        return director(roles).id, (f"overtime {lever.hours:g}h over budget "
                                    f"({overtime_remaining:g}h remaining) needs the director")
    if k == "hire_temps":
        return director(roles).id, "hire_temps needs the director"
    if k == "protect":
        if lever.days <= LOCK_MAX_DAYS_ALONE:
            return "quality", f"protect {lever.days}d <= {LOCK_MAX_DAYS_ALONE}d is issued by quality"
        return chair(roles).id, f"protect {lever.days}d > {LOCK_MAX_DAYS_ALONE}d is issued by the chair"
    raise ValueError(f"no issuer rule for lever kind {k!r}")


def issuer_for(
    lever: Lever,
    roles: dict[str, Role],
    present: set[str],
    overtime_remaining: float = math.inf,
) -> Ruling:
    """Who issues `lever`, and whether a hire or an escalation is needed first.

    `present`: role ids in the room (always-attending roles count as present regardless).
    `overtime_remaining`: hours left in the profile's `overtime_hours_per_week` budget.
    """
    owner_id, reason = _owner(lever, roles, overtime_remaining)
    owner = roles[owner_id]
    in_room = present | always_present(roles)
    if owner.power == "director":
        if owner_id in in_room:
            return Ruling(issuer_role_id=owner_id, needs_director=True, reason=reason)
        return Ruling(issuer_role_id=None, needs_director=True,
                      reason=reason + "; director absent -> escalate")
    if owner_id in in_room:
        return Ruling(issuer_role_id=owner_id, reason=reason)
    return Ruling(issuer_role_id=owner_id, needs_hire=owner_id,
                  reason=reason + f"; {owner_id} absent -> chair hires {owner_id}")


def commit_plan(
    option_levers: list[Lever],
    roles: dict[str, Role],
    present: set[str],
    overtime_remaining: float = math.inf,
) -> CommitPlan:
    """Authority check for an approved option. Overtime levers draw down the remaining
    budget in option order. A lever whose issuer must be hired is not `issuable` yet: the
    caller hires the role (which may accept or refuse) and re-runs with it in `present`."""
    issuable: list[tuple[Lever, str]] = []
    hires: list[str] = []
    reasons: list[str] = []
    escalate = False
    remaining = overtime_remaining
    for lv in option_levers:
        r = issuer_for(lv, roles, present, remaining)
        reasons.append(r.reason)
        if r.needs_hire:
            if r.needs_hire not in hires:
                hires.append(r.needs_hire)
            continue
        if r.issuer_role_id is None:
            escalate = True
            continue
        issuable.append((lv, r.issuer_role_id))
        if lv.kind == "overtime" and r.issuer_role_id == "shift_lead":
            remaining -= lv.hours
    return CommitPlan(issuable=issuable, hires=hires, escalate=escalate, reasons=reasons)


def veto_allowed(role: Role) -> bool:
    return role.power == "veto"


def lock_allowed(role: Role, days: int) -> bool:
    """Quality may lock alone for at most 3 days; longer locks go through the chair."""
    return role.power == "lock" and days <= LOCK_MAX_DAYS_ALONE


def capacity_truth(role: Role) -> bool:
    """True if this role's stated available labor caps labor_allocate and overtime."""
    return role.power == "capacity_truth"

