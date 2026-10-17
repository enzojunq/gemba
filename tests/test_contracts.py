"""Guards the A/B boundary: anything here breaking means CONTRACT.md changed."""
import pickle

import pandas as pd
import pytest
from pydantic import TypeAdapter, ValidationError

import contracts as c

LEVER = TypeAdapter(c.Lever)


def test_lever_union_parses_and_rejects_non_floor_levers():
    lever = LEVER.validate_python({"kind": "close_aisle", "aisle": 7, "days": 3, "decision_id": "d1"})
    assert isinstance(lever, c.CloseAisle)
    with pytest.raises(ValidationError):
        LEVER.validate_python({"kind": "set_profile", "profile": "x", "decision_id": "d1"})
    with pytest.raises(ValidationError):  # unknown field
        LEVER.validate_python({"kind": "pause_reslot", "days": 2, "night": 1, "decision_id": "d1"})
    assert set(c.LEVER_KINDS) == {m.model_fields["kind"].default for m in c.Lever.__origin__.__args__}  # type: ignore[attr-defined]


def test_event_tables_cover_every_kind():
    kinds = set(c.EVENT_KINDS)
    assert len(kinds) == 17
    with pytest.raises(ValidationError):  # args must match EVENT_ARGS[kind]
        c.ScenarioEvent(kind="rack_damage", day=66, args={"bogus": 1})
    assert c.Scenario(id="s", seed=1, days=(61, 63)).day_range == range(61, 64)
    assert set(c.EVENT_ARGS) == kinds
    assert set(c.ANNOUNCE_LEAD_DAYS) == kinds
    assert c.announced_day("leadership_visit", 78) == 73
    assert c.announced_day("rack_damage", 66) == 66


def test_visible_and_active_filters():
    e1 = c.Event(event_id="e1", kind="demand_surge", day=69, announced_day=67, source="scripted", args={})
    e2 = c.Event(event_id="e2", kind="rack_damage", day=66, announced_day=66, source="scripted", args={})
    assert [e.event_id for e in c.visible([e1, e2], 66)] == ["e2"]
    assert [e.event_id for e in c.visible([e1, e2], 67)] == ["e1", "e2"]
    assert [e.event_id for e in c.active([e1, e2], 69)] == ["e1"]


def test_kpis_match_spec_names():
    assert len(c.KPI_NAMES) == 18
    assert set(c.KPI_NAMES) | {"mispick_rate_by_customer"} == set(c.Kpis.model_fields)


def test_done_check_bounds_and_metric():
    c.DoneCheck(check_name="ot", scope="daily", metric="on_time_pct", op=">=", threshold=95, min_threshold=93, max_threshold=97)
    c.DoneCheck(check_name="score", scope="final", metric="score", op=">=", threshold=0, min_threshold=0, max_threshold=0)
    with pytest.raises(ValidationError):
        c.DoneCheck(check_name="x", scope="daily", metric="nope", op=">=", threshold=1, min_threshold=0, max_threshold=2)
    with pytest.raises(ValidationError):
        c.DoneCheck(check_name="x", scope="daily", metric="lph", op=">=", threshold=5, min_threshold=0, max_threshold=2)


def test_decision_and_demand_round_trip():
    d = c.Decision(decision_id="d1", day=61, levers=[c.PauseReslot(days=1, decision_id="d1")], decided_by="static_rules", rationale="event day")
    assert c.Decision.model_validate_json(d.model_dump_json()) == d
    dd = c.DayDemand(day=61, orders=pd.DataFrame({"order_id": [1]}), lines=pd.DataFrame({"order_id": [1], "line_no": [1], "sku_id": ["s"], "qty": [2]}))
    assert pickle.loads(pickle.dumps(dd)).day == 61


def test_node_ids():
    assert c.node_id(c.DEC, "d1") == "dec:d1"
    assert c.kpi_node_id("r1", 63, "on_time_pct") == "kpi:r1:63:on_time_pct"
    with pytest.raises(ValueError):
        c.node_id("xyz", "1")
