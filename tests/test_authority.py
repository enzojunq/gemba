import pytest

from contracts import (
    CloseAisle, HireTemps, LaborAllocate, Overtime, PauseReslot, Protect, ReslotTonight,
    ResizeSlots, WavePriority,
)
from org.authority import (
    capacity_truth, commit_plan, filter_proposal, issuer_for, lock_allowed, veto_allowed,
)
from org.roles import load_roles

ALL = {"plant_director", "program_manager", "ops_manager", "reslotting_engineer", "analytics",
       "safety", "quality", "shift_lead", "replenishment"}
CORE = {"program_manager", "ops_manager", "reslotting_engineer", "analytics"}


@pytest.fixture(scope="module")
def roles():
    return load_roles()


def test_engineer_close_aisle_filtered(roles):
    levers = [ReslotTonight(n_moves=150), CloseAisle(aisle=4, days=2)]
    kept, rejected = filter_proposal(roles["reslotting_engineer"], levers)
    assert [lv.kind for lv in kept] == ["reslot_tonight"]
    assert len(rejected) == 1
    r = rejected[0]
    assert r.lever.kind == "close_aisle" and r.role_id == "reslotting_engineer"
    assert "close_aisle" in r.reason


def test_analytics_proposes_nothing(roles):
    kept, rejected = filter_proposal(roles["analytics"], [PauseReslot(days=1)])
    assert kept == [] and len(rejected) == 1


def test_protect_short_goes_to_quality(roles):
    r = issuer_for(Protect(target="customer", ids=["C1"], days=2), roles, ALL)
    assert r.issuer_role_id == "quality" and r.needs_hire is None and not r.needs_director


def test_protect_long_goes_to_chair(roles):
    r = issuer_for(Protect(target="customer", ids=["C1"], days=5), roles, CORE)
    assert r.issuer_role_id == "program_manager" and r.needs_hire is None


def test_overtime_within_budget_shift_lead(roles):
    r = issuer_for(Overtime(shift="day", pool="pm", hours=20), roles, ALL, overtime_remaining=40)
    assert r.issuer_role_id == "shift_lead" and not r.needs_director


def test_overtime_over_budget_director(roles):
    r = issuer_for(Overtime(shift="day", pool="pm", hours=60), roles, ALL, overtime_remaining=40)
    assert r.issuer_role_id == "plant_director" and r.needs_director


def test_quality_absent_needs_hire(roles):
    r = issuer_for(Protect(target="skus", ids=["S1"], days=1), roles, CORE)
    assert r.needs_hire == "quality" and r.issuer_role_id == "quality"


def test_hire_temps_escalates_when_director_absent(roles):
    lv = HireTemps(count=4, start_day=3, days=5)
    r = issuer_for(lv, roles, CORE)
    assert r.needs_director and r.issuer_role_id is None
    plan = commit_plan([lv], roles, CORE)
    assert plan.escalate and plan.issuable == []


def test_hire_temps_director_present(roles):
    plan = commit_plan([HireTemps(count=4, start_day=3, days=5)], roles, CORE | {"plant_director"})
    assert not plan.escalate and plan.issuable[0][1] == "plant_director"


def test_always_roles_implicitly_present(roles):
    assert issuer_for(PauseReslot(days=1), roles, set()).issuer_role_id == "program_manager"


def test_commit_plan_mixed(roles):
    option = [
        ReslotTonight(n_moves=120),
        LaborAllocate(shift="day", pool="pm", headcount=2),
        WavePriority(mode="by_cutoff"),
        ResizeSlots(skus=["S9"], days_cover=3.0),
        Protect(target="customer", ids=["C1"], days=2),   # quality absent -> hire
        CloseAisle(aisle=7, days=1),                       # safety present
        Overtime(shift="swing", pool="pm", hours=30),      # shift_lead absent -> hire
    ]
    plan = commit_plan(option, roles, CORE | {"safety"}, overtime_remaining=100)
    assert [(lv.kind, who) for lv, who in plan.issuable] == [
        ("reslot_tonight", "program_manager"), ("labor_allocate", "ops_manager"),
        ("wave_priority", "ops_manager"), ("resize_slots", "program_manager"),
        ("close_aisle", "safety"),
    ]
    assert plan.hires == ["quality", "shift_lead"]
    assert not plan.escalate
    assert len(plan.reasons) == len(option)


def test_commit_plan_overtime_draws_down_budget(roles):
    option = [Overtime(shift="day", pool="pm", hours=30), Overtime(shift="swing", pool="pm", hours=30)]
    plan = commit_plan(option, roles, ALL - {"plant_director"}, overtime_remaining=40)
    assert [who for _, who in plan.issuable] == ["shift_lead"]
    assert plan.escalate


def test_power_checks(roles):
    assert veto_allowed(roles["safety"]) and not veto_allowed(roles["quality"])
    assert lock_allowed(roles["quality"], 3) and not lock_allowed(roles["quality"], 4)
    assert not lock_allowed(roles["program_manager"], 2)
    assert capacity_truth(roles["shift_lead"]) and not capacity_truth(roles["ops_manager"])
