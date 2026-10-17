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


import pytest  # noqa: E402


@pytest.mark.parametrize("kind,args", [
    ("rack_damage", {"aisle": 40}),
    ("rack_damage", {"aisle": 0}),
    ("pm_zone_down", {"zone": 7, "hours": 4}),
    ("key_customer_week", {"customer": "Acme", "days": 5}),
    ("labor_shortage", {"pool": "robots", "pct": 10, "days": 2}),
    ("labor_shortage", {"pool": "pm", "pct": -5, "days": 2}),
    ("demand_surge", {"family": "F1", "factor": 1.5, "days": 2}),
    ("demand_surge", {"family": "F001", "factor": 0, "days": 2}),
    ("forklift_breakdown", {"count": 0, "days": 1}),
    ("wms_sync_delay", {"days": 1.5}),
    ("wms_sync_delay", {"days": True}),
])
def test_args_validated_against_real_data(kind, args):
    llm = OneShot(c.EventParse(kind=kind, args=args, confidence=0.9))
    assert parse_inbox(llm, "msg", 66, "r1", n=1) is None


@pytest.mark.parametrize("kind,args,expect", [
    ("rack_damage", {"aisle": 28}, {"aisle": 28, "days": 1}),
    ("pm_zone_down", {"zone": "6", "hours": 4}, {"zone": 6, "hours": 4}),
    ("key_customer_week", {"customer": "C001", "days": 5}, {"customer": "C001", "days": 5}),
    ("labor_shortage", {"pool": "forklift", "pct": 12.5, "days": 2}, {"pool": "forklift", "pct": 12.5, "days": 2}),
    ("demand_surge", {"family": "seasonal", "factor": 1.3}, {"family": "seasonal", "factor": 1.3, "days": 1}),
    ("demand_surge", {"family": "F012", "factor": 2}, {"family": "F012", "factor": 2.0, "days": 1}),
])
def test_valid_args_accepted(kind, args, expect):
    e = parse_inbox(OneShot(c.EventParse(kind=kind, args=args, confidence=0.9)), "msg", 66, "r1", n=1)
    assert e is not None and e.args == expect and e.day == 66 and e.announced_day == 66


def test_day_reaches_llm_calls_row():
    class Sink:
        def __init__(self):
            self.day, self.rows = 0, []

        def set_day(self, day):
            self.day = day

        def on_call(self, row):
            self.rows.append(self.day)

    sink = Sink()

    class Logged(OneShot):
        on_call = None

        def complete(self, *a, **kw):
            out = super().complete(*a, **kw)
            self.on_call({})
            return out

    llm = Logged(c.EventParse(kind="wms_sync_delay", args={}, confidence=0.9))
    llm.on_call = sink.on_call
    parse_inbox(llm, "wms lag", 71, "r1", n=1)
    assert sink.rows == [71]

    class WithDay(OneShot):
        def complete(self, tier, messages, schema=None, *, role, run_id, day=None, **kw):
            self.day = day
            return super().complete(tier, messages, schema, role=role, run_id=run_id, **kw)

    llm2 = WithDay(c.EventParse(kind="wms_sync_delay", args={}, confidence=0.9))
    parse_inbox(llm2, "wms lag", 72, "r1", n=1)
    assert llm2.day == 72


def test_ids_count_up_per_day():

    llm = OneShot(c.EventParse(kind="wms_sync_delay", args={}, confidence=0.8))
    a, b = parse_inbox(llm, "wms lag", 70, "rx"), parse_inbox(llm, "wms lag again", 70, "rx")
    assert (a.event_id, b.event_id) == ("inbox-70-1", "inbox-70-2")
