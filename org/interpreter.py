"""Event Interpreter (Nano): one inbox message -> one typed `contracts.Event`, or None.

The LLM proposes `EventParse(kind, args, confidence)`; code validates it against
`contracts.EVENT_ARGS` (unknown keys dropped, `days` defaults to 1 where the kind takes it,
demand_surge needs exactly one of skus|family|customer) and against the real floor data
(`pool` is a `contracts.Pool`, aisle 1-28, zone 1-6, customer `C###`, family `F###` or
`seasonal`, days/hours/count/n positive ints, pct/factor positive numbers), then builds the Event. Messages are
interpreted as effective today (`day = announced_day = today`, source="inbox").
Returns None (and logs why) when confidence < 0.5 or validation fails.
"""
from __future__ import annotations

import inspect
import logging
import math
import re
import threading
from typing import Any, get_args

from pydantic import ValidationError

from contracts import EVENT_ARGS, Event, EventParse, Pool
from llm.prompt import build_messages
from org.roles import meta_prompt

log = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.5
POSITIVE_INT_ARGS = ("days", "hours", "count", "n")
POSITIVE_NUM_ARGS = ("pct", "factor")
RANGES = {"aisle": (1, 28), "zone": (1, 6)}
POOLS: tuple[str, ...] = get_args(Pool)
CUSTOMER_RE = re.compile(r"^C\d{3}$")
FAMILY_RE = re.compile(r"^F\d{3}$")
SURGE_TARGETS = ("skus", "family", "customer")

_counter: dict[tuple[str, int], int] = {}
_lock = threading.Lock()


def _next_n(run_id: str, day: int) -> int:
    with _lock:
        n = _counter.get((run_id, day), 0) + 1
        _counter[(run_id, day)] = n
        return n


def _as_int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() else None
    if isinstance(v, str) and re.fullmatch(r"\s*-?\d+\s*", v):
        return int(v)
    return None


def _as_num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _check(key: str, v: Any) -> tuple[bool, Any]:
    """(ok, cleaned value) for one argument."""
    if key in POSITIVE_INT_ARGS:
        x = _as_int(v)
        return (x is not None and x >= 1), x
    if key in RANGES:
        x, (lo, hi) = _as_int(v), RANGES[key]
        return (x is not None and lo <= x <= hi), x
    if key in POSITIVE_NUM_ARGS:
        x = _as_num(v)
        return (x is not None and x > 0), x
    if key == "pool":
        return (isinstance(v, str) and v in POOLS), v
    if key == "customer":
        return (isinstance(v, str) and bool(CUSTOMER_RE.match(v.strip()))), str(v).strip()
    if key == "family":
        return (isinstance(v, str) and (v.strip() == "seasonal" or bool(FAMILY_RE.match(v.strip())))), str(v).strip()
    if key == "skus":
        ok = isinstance(v, list) and bool(v) and all(isinstance(x, str) and x.strip() for x in v)
        return ok, [x.strip() for x in v] if ok else v
    if key == "no_moves_in_aisles":
        xs = [_as_int(x) for x in v] if isinstance(v, list) else [None]
        return all(x is not None and 1 <= x <= 28 for x in xs), xs
    return True, v


def validate_parse(parse: EventParse) -> dict[str, Any] | None:
    """Cleaned args for `parse`, or None if they cannot make a valid event."""
    allowed = EVENT_ARGS[parse.kind]
    args = {k: v for k, v in (parse.args or {}).items() if k in allowed and v is not None}
    if "days" in allowed and "days" not in args:
        args["days"] = 1
    for k in list(args):
        ok, cleaned = _check(k, args[k])
        if not ok:
            log.info("inbox parse %s: invalid %s=%r", parse.kind, k, args[k])
            return None
        args[k] = cleaned
    if parse.kind == "demand_surge" and sum(k in args for k in SURGE_TARGETS) != 1:
        return None
    return args


def _accepts_day(fn: Any) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "day" in params


def _tell_sink_the_day(llm: Any, day: int) -> None:
    """The client's `on_call` sink stamps `llm_calls.day`. When the sink belongs to an OrgPolicy
    (its `_on_call`), set the policy's current day first so the row is dated today, not the
    previous meeting's day (the interpreter runs before `decide`)."""
    owner = getattr(getattr(llm, "on_call", None), "__self__", None)
    set_day = getattr(owner, "set_day", None)
    if callable(set_day):
        set_day(day)


def parse_inbox(llm, text: str, day: int, run_id: str, n: int | None = None) -> Event | None:
    """Interpret one inbox message with the Nano tier. `n` numbers the event id
    (`inbox-{day}-{n}`); by default a per-(run, day) counter."""
    task = (f"Today is day {day}. Interpret this inbox message as exactly one event.\n\n"
            f"Message:\n\"\"\"\n{text.strip()}\n\"\"\"")
    msgs = build_messages(meta_prompt("event_interpreter"), [], f"Day {day}", task)
    fallback = EventParse(kind="wms_sync_delay", args={}, confidence=0.0)
    _tell_sink_the_day(llm, day)
    extra = {"day": day} if _accepts_day(llm.complete) else {}
    try:
        parse, _meta = llm.complete("nano", msgs, EventParse, role="event_interpreter", run_id=run_id,
                                    fallback=fallback, **extra)
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
