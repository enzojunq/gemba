import pytest

from contracts import EVENT_ARGS, KPI_NAMES, LEVER_KINDS, ExogenousKind
from org.roles import (
    Role, chair, director, hire_triggers, load_roles, meta_prompt, parse_condition, role_prompt,
)

EXPECTED = {
    "plant_director": "Dana", "program_manager": "Megan", "ops_manager": "Ryan",
    "reslotting_engineer": "Luke", "analytics": "Paige", "safety": "Sam", "quality": "Ellen",
    "shift_lead": "Marcus", "replenishment": "Tom",
}
QUIET_KPIS = {"mispick_rate": 0.4, "safety_risk_max": 0.7, "on_time_pct": 97.5}


@pytest.fixture(scope="module")
def roles() -> dict[str, Role]:
    return load_roles()


def test_all_nine_load(roles):
    assert {rid: r.name for rid, r in roles.items()} == EXPECTED


def test_one_chair_one_director(roles):
    assert [r.id for r in roles.values() if r.power == "chair"] == ["program_manager"]
    assert [r.id for r in roles.values() if r.power == "director"] == ["plant_director"]
    assert chair(roles).name == "Megan" and director(roles).name == "Dana"


def test_levers_and_kpis_valid(roles):
    for r in roles.values():
        assert set(r.levers_can_propose) <= set(LEVER_KINDS)
        assert set(r.levers_can_issue) <= set(LEVER_KINDS)
        assert set(r.owns_kpis) <= set(KPI_NAMES)
    assert roles["analytics"].runs_simulator and not roles["analytics"].levers_can_propose
    assert set(roles["plant_director"].meta_powers) == {"set_profile", "override_lock", "break_tie"}


def test_bad_lever_rejected():
    with pytest.raises(ValueError):
        Role(id="x", name="X", title="X", model_tier="nano", character="c", power="vote",
             levers_can_propose=["set_profile"], attends="always")


def test_bad_trigger_rejected():
    with pytest.raises(ValueError):
        Role(id="x", name="X", title="X", model_tier="nano", character="c", power="vote",
             attends="on_trigger", triggers=["not_a_kpi > 1"])


@pytest.mark.parametrize("rid", sorted(EXPECTED) + ["event_interpreter", "retro_coach"])
def test_prompt_exists_and_short(roles, rid):
    text = role_prompt(roles[rid]) if rid in roles else meta_prompt(rid)
    assert 0 < len(text.split()) < 250
    if rid in roles:
        assert roles[rid].name in text
        assert "evidence id" in text and "JSON only" in text and "60 words" in text


def test_role_prompts_list_their_levers(roles):
    for r in roles.values():
        text = role_prompt(r)
        for lv in r.levers_can_propose:
            assert lv in text, (r.id, lv)


def test_interpreter_prompt_in_sync():
    text = meta_prompt("event_interpreter")
    for kind in ExogenousKind.__args__:
        line = next((ln for ln in text.splitlines() if f"`{kind}`" in ln), None)
        assert line is not None, kind
        for arg in EVENT_ARGS[kind]:
            assert arg in line, (kind, arg)


def test_retro_prompt_mentions_lesson_cap():
    text = meta_prompt("retro_coach")
    assert "40 words" in text and "regret" in text


def test_parse_condition():
    assert parse_condition("mispick_rate > 0.6") == ("mispick_rate", ">", 0.6)
    assert parse_condition("rack_damage") is None


def test_hire_safety_on_rack_damage(roles):
    assert hire_triggers(roles, ["rack_damage"], QUIET_KPIS) == {"safety": "rack_damage"}


def test_hire_quality_on_mispick(roles):
    got = hire_triggers(roles, [], {**QUIET_KPIS, "mispick_rate": 0.7})
    assert got == {"quality": "mispick_rate > 0.6"}


def test_hire_safety_on_risk_kpi(roles):
    got = hire_triggers(roles, [], {**QUIET_KPIS, "safety_risk_max": 1.2})
    assert got == {"safety": "safety_risk_max > 1.0"}


def test_hire_shift_lead_on_labor_shortage(roles):
    assert hire_triggers(roles, ["labor_shortage"], QUIET_KPIS) == {"shift_lead": "labor_shortage"}


def test_hire_multiple_and_director(roles):
    got = hire_triggers(roles, ["demand_surge", "director_escalation", "overtime_requested"], {})
    assert got == {"replenishment": "demand_surge", "plant_director": "director_escalation",
                   "shift_lead": "overtime_requested"}


def test_quiet_day_hires_nobody(roles):
    assert hire_triggers(roles, [], QUIET_KPIS) == {}
    assert hire_triggers(roles, ["leadership_visit"], {}) == {}


def test_hire_deterministic(roles):
    a = hire_triggers(roles, ["rack_damage", "wms_sync_delay"], {"mispick_rate": 0.9})
    b = hire_triggers(roles, ["wms_sync_delay", "rack_damage"], {"mispick_rate": 0.9})
    assert a == b == {"safety": "rack_damage", "quality": "mispick_rate > 0.6"}
