from __future__ import annotations

import contracts as c
from org.board import MAX_TOKENS, Board, EvidenceRow, StanceMemory, approx_tokens, snapshot_text
from org.playbook_select import select_rules


def _view(day: int = 66) -> c.FloorView:
    return c.FloorView(day=day, frozen_aisles=[3], closed_aisles=[5],
                       protected=[c.Protection(target="customer", ids=["KEY1"], until_day=68, decision_id="d1")],
                       staging_occupancy=55.0, move_queue=1800, moves_done_total=600, moves_target_total=2400,
                       reslot_progress_pct=25.0, labor_available={"day": {"pm": 12}}, temps_active=0,
                       learning_dip_skus=200, overtime_days_last4={"pm": 2})


def _board(n_evidence: int, n_events: int) -> Board:
    k = c.Kpis(lines=6000, lph=98.5, on_time_pct=95.1, mispick_rate=0.52, reslot_progress_pct=25.0,
               labor_cost_usd=24000.0, near_misses=0.1)
    return Board(
        day=66, view=_view(), yesterday=k, predicted=k, targets={"on_time_pct": 97.0, "mispick_rate": 0.5},
        hard_limits={"safety_risk_max": 1.0},
        events=[("demand_surge", 66 + i % 5, {"family": f"F{i}", "factor": 1.3, "days": 3}) for i in range(n_events)],
        constraints=["reslot paused until day 67"], agreements=["no moves in aisle 3 during the visit"],
        evidence=[EvidenceRow.from_kpis(f"ev-run-66-{i}", f"option {i} with a fairly long label", k, 0.3)
                  for i in range(n_evidence)],
        inbox=["Rack in aisle 5 got hit by a forklift this morning, please check it out " * 5] * 8,
        present={"program_manager": "Megan", "ops_manager": "Ryan", "safety": "Sam"},
        drift=["stall: day 65: no progress for 3 days"], hires_today=["Sam (rack_damage)"])


def test_snapshot_under_budget_with_many_rows():
    text = snapshot_text(_board(50, 30))
    assert approx_tokens(text) <= MAX_TOKENS
    assert "ev-run-66-49" in text and "ev-run-66-44" not in text  # last 5 evidence rows only (newest kept)
    assert "Megan" in text and "KEY1" in text and "aisle 5 closed" in text


def test_snapshot_small_board_complete():
    text = snapshot_text(_board(2, 2))
    assert "ev-run-66-0" in text and "ev-run-66-1" in text and "demand_surge" in text
    assert "Yesterday actual" in text and "Drift flags" in text


def test_stance_memory_keeps_last_three():
    m = StanceMemory()
    for d in range(5):
        m.add("ops_manager", d, f"o{d}", "support", f"reason {d}\nsecond line")
    rows = m.get("ops_manager")
    assert [r[0] for r in rows] == [2, 3, 4] and "\n" not in rows[-1][3]
    assert "day 4: support o4" in m.render("ops_manager") and m.render("safety") == ""


def test_select_rules_by_role_and_tag_overlap():
    rules = [
        {"rule_id": "r3", "role": "ops_manager", "tags": ["rack_damage"], "advice": "a"},
        {"rule_id": "r1", "role": "ops_manager", "tags": ["rack_damage", "demand_surge"], "advice": "b"},
        {"rule_id": "r2", "role": "ops_manager", "tags": ["rack_damage"], "advice": "c"},
        {"rule_id": "r4", "role": "safety", "tags": ["rack_damage"], "advice": "d"},
        {"rule_id": "r5", "role": "ops_manager", "tags": ["leadership_visit"], "advice": "e"},
        {"rule_id": "r6", "role": "ops_manager", "tags": ["rack_damage"], "advice": "f", "status": "retired"},
    ]
    assert select_rules(rules, "ops_manager", {"rack_damage", "demand_surge"}) == ["b", "c", "a"]
    assert select_rules(rules, "ops_manager", {"rack_damage"}, max_n=1) == ["b"]
    assert select_rules([], "ops_manager", {"rack_damage"}) == []
