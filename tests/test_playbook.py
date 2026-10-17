import pytest

import contracts as c
from engine.store import RunStore
from learn import playbook as pb
from org.playbook_select import select_rules


@pytest.fixture
def store(tmp_path):
    s = RunStore(f"sqlite:///{tmp_path / 'runs.db'}")
    s.create_schema()
    return s


def rule(i, role="ops_manager", tags=("demand_surge",), advice=None, delta=0.1):
    advice = advice or f"distinct advice number {i} about topic{i} only"
    return {"rule_id": f"r{i:02d}", "role": role, "tags": list(tags), "trigger": "t", "advice": advice,
            "evidence": {"run_id": "bt-1", "day": 62, "fork_run_id": "fk-1", "score_delta": delta},
            "score_delta": delta, "status": "active"}


def test_v0_is_empty(store):
    assert pb.load_rules(store, "v0") == []
    pb.save_rules(store, "v0", [rule(1)])  # even if someone writes rows under v0
    assert pb.load_rules(store, "v0") == []


def test_save_load_round_trip(store):
    rules = [rule(1, delta=0.3), rule(2, role="quality", tags=["key_customer_week", "mispick_rate"])]
    pb.save_rules(store, "v1", rules)
    assert pb.load_rules(store, "v1") == rules
    pb.save_rules(store, "v1", rules[:1])  # replace, not append
    assert pb.load_rules(store, "v1") == rules[:1]


def test_load_status_rules(store):
    pb.save_rules(store, "v2", [dict(rule(1), status="candidate"), dict(rule(2), status="candidate")])
    assert len(pb.load_rules(store, "v2")) == 2  # under the gate: candidates load
    store.set_playbook_status("v2", "active", from_status="candidate")
    pb.save_rules(store, "v2", [rule(1), dict(rule(2), status="candidate")])
    assert [r["rule_id"] for r in pb.load_rules(store, "v2")] == ["r01"]  # active only once promoted
    store.set_playbook_status("v2", "rejected")
    assert pb.load_rules(store, "v2") == []


def test_merge_caps_at_12():
    out = pb.merge([rule(i, delta=i / 100) for i in range(8)], [rule(i, delta=i / 100) for i in range(8, 20)])
    assert len(out) == 12
    assert [r["rule_id"] for r in out] == [f"r{i:02d}" for i in range(19, 7, -1)]  # largest score_delta kept
    assert {r["status"] for r in out} == {"candidate"}


def test_merge_duplicates_keep_larger_delta():
    a = rule(1, tags=["demand_surge", "on_time_pct"], delta=0.05,
             advice="Do not move SKUs in the surging family within two days of a demand surge.")
    b = rule(2, tags=["demand_surge"], delta=0.20,
             advice="Do not move SKUs in the surging family within 2 days of a demand surge")
    other_role = dict(b, rule_id="r03", role="quality")
    assert pb.is_duplicate(a, b) and not pb.is_duplicate(a, other_role)
    out = pb.merge([a], [b, other_role])
    assert sorted(r["rule_id"] for r in out) == ["r02", "r03"]
    out2 = pb.merge([dict(a, score_delta=0.5)], [b])
    assert [r["rule_id"] for r in out2] == ["r01"]


def test_merge_accepts_lessons_and_drops_unfired_old_rules():
    old_fired, old_idle = rule(1), rule(2)
    lesson = c.Lesson(role="safety", tags=["rack_damage"], trigger="t", advice="Close the aisle the same day.")
    out = pb.merge([old_fired, old_idle], [lesson], fired_count={"r01": 3})
    roles = sorted(r["role"] for r in out)
    assert [r["rule_id"] for r in out if r["role"] == "ops_manager"] == ["r01"]
    assert roles == ["ops_manager", "safety"]  # the new lesson is kept although it never fired
    assert len(pb.merge([old_fired, old_idle], [])) == 2  # no fired counts -> nothing dropped


def test_tags_vocabulary_matches_standup():
    tags = pb.tags_for_day(["demand_surge"], ["mispick_rate", "on_time_pct"])
    assert tags == {"demand_surge", "mispick_rate", "on_time_pct"}
    assert set(c.EVENT_KINDS) <= pb.TAG_VOCABULARY
    assert pb.normalize_tags(["mispick_rate_high", "on_time_low", "Demand-Surge", "bogus"]) == \
        ["mispick_rate", "on_time_pct", "demand_surge"]
    assert pb.normalize_tags(["bogus"]) == ["bogus"]
    r = pb.lesson_rule(c.Lesson(role="quality", tags=["mispick_rate_high"], trigger="t", advice="Lock key SKUs."))
    assert select_rules([r], "quality", tags) == ["Lock key SKUs."]


def test_fired_counts(store):
    r = rule(1, advice="Pause a night.")
    msgs = [{"role": "system", "content": "x"},
            {"role": "user", "content": "## Playbook rules for your role\n- Pause a night.\n\n## Board\nb\n\n## Task\nt"}]
    store.write_llm_call(call_id="1", run_id="bt-1", role="ops_manager", request={"messages": msgs})
    store.write_llm_call(call_id="2", run_id="bt-1", role="ops_manager",
                         request={"messages": [{"role": "user", "content": "## Playbook rules for your role\nnone"}]})
    assert pb.fired_counts(store, ["bt-1"], [r]) == {"r01": 1}


def test_next_version():
    assert pb.next_version("v0") == "v1" and pb.next_version("v9") == "v10"
    with pytest.raises(ValueError):
        pb.next_version("draft")
