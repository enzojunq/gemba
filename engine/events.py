"""Scenario loading, the scripted event calendar, and the inbox hook (CONTRACT 3.4, spec 9.5)."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

import contracts as c

log = logging.getLogger(__name__)

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"


def load_scenario(path_or_id: str | Path) -> c.Scenario:
    """Load and validate a scenario by id ("reslot_default" -> scenarios/reslot_default.yaml) or path."""
    p = Path(path_or_id)
    if not p.exists() and p.suffix == "":
        p = SCENARIOS_DIR / f"{path_or_id}.yaml"
    with open(p, encoding="utf-8") as f:
        return c.Scenario.model_validate(yaml.safe_load(f))


def event_id(kind: str, day: int) -> str:
    return f"evt-{kind}-{day}"


def build_calendar(scenario: c.Scenario, run_id: str) -> list[c.Event]:
    """ScenarioEvent -> Event (source scripted, root cause). Ids are run-independent so forks
    and re-runs share them; `run_id` is accepted for symmetry with the inbox/consequence ids."""
    out: list[c.Event] = []
    seen: dict[str, int] = {}
    for se in scenario.events:
        eid = event_id(se.kind, se.day)
        n = seen.get(eid, 0)
        seen[eid] = n + 1
        if n:  # two events of the same kind on the same day
            eid = f"{eid}-{n}"
        out.append(c.Event(event_id=eid, kind=se.kind, day=se.day, hour=se.hour, args=dict(se.args),
                           announced_day=c.announced_day(se.kind, se.day), source="scripted", caused_by=None))
    return out


def parse_inbox(text: str, *, run_id: str = "", day: int = 0, llm=None) -> c.Event | None:
    """Inbox text -> Event via the Event Interpreter (org/interpreter.py, Nano). Baseline
    policies carry no LLM, so without one the message is consumed, logged and has no effect."""
    if llm is None:
        log.info("inbox message ignored (policy has no LLM) run=%s day=%s: %r", run_id, day, text)
        return None
    from org.interpreter import parse_inbox as _interpret  # lazy: engine must import without org
    return _interpret(llm, text, day, run_id)
