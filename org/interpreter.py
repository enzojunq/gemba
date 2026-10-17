"""Event Interpreter (Nano): one inbox message -> one typed `contracts.Event`, or None.

The LLM proposes `EventParse(kind, args, confidence)`; code validates it against
`contracts.EVENT_ARGS` (unknown keys dropped, `days` defaults to 1 where the kind takes it,
demand_surge needs exactly one of skus|family|customer) and builds the Event. Messages are
interpreted as effective today (`day = announced_day = today`, source="inbox").
Returns None (and logs why) when confidence < 0.5 or validation fails.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from pydantic import ValidationError

from contracts import EVENT_ARGS, Event, EventParse
from llm.prompt import build_messages
from org.roles import meta_prompt

log = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.5
INT_ARGS = ("days", "aisle", "zone", "hours", "n", "count")
SURGE_TARGETS = ("skus", "family", "customer")

_counter: dict[tuple[str, int], int] = {}
_lock = threading.Lock()


def _next_n(run_id: str, day: int) -> int:
    with _lock:
        n = _counter.get((run_id, day), 0) + 1
        _counter[(run_id, day)] = n
        return n


def validate_parse(parse: EventParse) -> dict[str, Any] | None:
    """Cleaned args for `parse`, or None if they cannot make a valid event."""
    allowed = EVENT_ARGS[parse.kind]
    args = {k: v for k, v in (parse.args or {}).items() if k in allowed and v is not None}
    if "days" in allowed and "days" not in args:
        args["days"] = 1
    for k in INT_ARGS:
        if k in args:
            try:
                args[k] = int(args[k])
            except (TypeError, ValueError):
                return None
    if "days" in args and args["days"] < 1:
        return None
    if parse.kind == "demand_surge" and sum(k in args for k in SURGE_TARGETS) != 1:
        return None
    return args


def parse_inbox(llm, text: str, day: int, run_id: str, n: int | None = None) -> Event | None:
    """Interpret one inbox message with the Nano tier. `n` numbers the event id
    (`inbox-{day}-{n}`); by default a per-(run, day) counter."""
    task = (f"Today is day {day}. Interpret this inbox message as exactly one event.\n\n"
            f"Message:\n\"\"\"\n{text.strip()}\n\"\"\"")
    msgs = build_messages(meta_prompt("event_interpreter"), [], f"Day {day}", task)
    fallback = EventParse(kind="wms_sync_delay", args={}, confidence=0.0)
    try:
        parse, _meta = llm.complete("nano", msgs, EventParse, role="event_interpreter", run_id=run_id,
                                    fallback=fallback)
    except Exception as e:  # noqa: BLE001 - an inbox message must never crash the run
        log.warning("event interpreter failed (run=%s day=%s): %s", run_id, day, e)
        return None
    if not isinstance(parse, EventParse) or parse.confidence < MIN_CONFIDENCE:
        log.info("inbox message ignored (confidence %.2f < %.2f): %r",
                 getattr(parse, "confidence", 0.0), MIN_CONFIDENCE, text)
        return None
    args = validate_parse(parse)
    if args is None:
        log.info("inbox message ignored (invalid args %s for %s): %r", parse.args, parse.kind, text)
        return None
    try:
        return Event(event_id=f"inbox-{day}-{n if n is not None else _next_n(run_id, day)}", kind=parse.kind,
                     day=day, announced_day=day, source="inbox", caused_by=None, args=args)
    except ValidationError as e:
        log.info("inbox message ignored (event validation): %s", e)
        return None
