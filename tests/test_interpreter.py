from __future__ import annotations

import contracts as c
from llm.client import CallMeta
from org.interpreter import parse_inbox


class OneShot:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def complete(self, tier, messages, schema=None, *, role, run_id, reasoning=False, max_tokens=800,
                 temperature=0.0, fallback=None):
        self.calls.append((tier, role, messages))
        return (self.answer if self.answer is not None else fallback), CallMeta(model="fake", prompt_hash="x")


def test_valid_parse_builds_event_with_allowed_args():
    llm = OneShot(c.EventParse(kind="rack_damage", args={"aisle": "7", "colour": "red"}, confidence=0.9))
    e = parse_inbox(llm, "Forklift clipped the rack in aisle 7.", 66, "r1", n=1)
    assert isinstance(e, c.Event)
    assert e.event_id == "inbox-66-1" and e.kind == "rack_damage" and e.source == "inbox"
    assert e.day == 66 and e.announced_day == 66 and e.caused_by is None
    assert e.args == {"aisle": 7, "days": 1}  # unknown key dropped, days defaulted
    tier, role, msgs = llm.calls[0]
    assert tier == "nano" and role == "event_interpreter" and "aisle 7" in msgs[-1]["content"]


def test_low_confidence_returns_none():
    llm = OneShot(c.EventParse(kind="demand_surge", args={"family": "F1", "factor": 1.2}, confidence=0.3))
    assert parse_inbox(llm, "things might get busy?", 66, "r1") is None


def test_invalid_args_return_none():
    llm = OneShot(c.EventParse(kind="demand_surge", args={"family": "F1", "customer": "KEY1"}, confidence=0.9))
    assert parse_inbox(llm, "surge", 66, "r1") is None
    llm = OneShot(c.EventParse(kind="rack_damage", args={"aisle": "seven"}, confidence=0.9))
    assert parse_inbox(llm, "rack", 66, "r1") is None


def test_schema_failure_fallback_returns_none():
    assert parse_inbox(OneShot(None), "garbled", 66, "r1") is None


def test_ids_count_up_per_day():
    llm = OneShot(c.EventParse(kind="wms_sync_delay", args={}, confidence=0.8))
    a, b = parse_inbox(llm, "wms lag", 70, "rx"), parse_inbox(llm, "wms lag again", 70, "rx")
    assert (a.event_id, b.event_id) == ("inbox-70-1", "inbox-70-2")
