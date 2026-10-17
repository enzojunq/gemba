"""Scenario designer (META_HARNESS plan B): plain words -> a validated scenario dict for the console.

The LLM proposes a `ScenarioDraft`; code does everything that matters:
  1. confidence >= 0.5, else "not a scenario request";
  2. per event: only the 10 exogenous kinds (a consequence kind is an error); args cleaned by
     `org.interpreter.validate_parse` (unknown keys dropped, `days` defaulted, ranges, ids). An event
     whose args cannot be made valid (e.g. customer "C9") is DROPPED with a reason, not an error;
  3. window `61 <= start <= end <= 90`; an event outside it is an error (never silently moved);
  4. id = slug (suffixed with a text hash when it collides with a protected or existing scenario),
     seed = the draft's or `1000 + crc32(text) % 9000` (outside the train/test seeds);
  5. `scenarios.loader.validate_scenario` (A's rules) then `contracts.Scenario`;
  6. one repair round with the errors fed back (2 LLM calls at most).

Never writes a file: the console's `POST /scenarios` saves. Deterministic for the same text
(temperature 0 and the response cache).
"""
from __future__ import annotations

import logging
import re
import zlib
from typing import Any, get_args

import yaml
from pydantic import BaseModel, Field

import contracts as c
from llm.prompt import build_messages
from org.interpreter import MIN_CONFIDENCE, POOLS, validate_parse
from org.roles import meta_prompt
from scenarios.loader import SCENARIO_DIR, validate_scenario

log = logging.getLogger(__name__)

WINDOW: tuple[int, int] = (61, 90)
DEFAULT_DAYS: tuple[int, int] = (61, 70)
MAX_EVENTS = 8
TIER = "super"
MAX_TOKENS = 1200
EXOGENOUS: tuple[str, ...] = get_args(c.ExogenousKind)
PROTECTED = {"reslot_default", "train_1", "train_2", "train_3", "test_1", "test_2", "test_3"}
PERTURBATION_KEYS = ("family_scale_pct", "seasonal_group_seed", "surge_shift_days")
OVERRIDE_KEYS = ("headcounts", "budgets")

ARG_TYPES = {
    "pool": " | ".join(POOLS), "pct": "number 0-100", "days": "int >= 1, duration", "hours": "int >= 1",
    "count": "int >= 1", "n": "int >= 1, new SKUs", "aisle": "int 1-28", "zone": "int 1-6",
    "customer": "C###, C001-C003 are key accounts", "family": "F### or seasonal", "skus": "list of SKU ids",
    "factor": "number > 0, x2 = 2", "no_moves_in_aisles": "list of int 1-28",
}


class EventDraft(BaseModel):
    kind: c.EventKind  # exogenous kinds only, enforced in code
    day: int
    hour: int = Field(6, ge=0, le=23)
    args: dict[str, Any] = {}


class ScenarioDraft(BaseModel):
    slug: str
    summary: str
    days: tuple[int, int] = DEFAULT_DAYS
    events: list[EventDraft] = Field(default_factory=list, max_length=MAX_EVENTS)
    perturbation: dict[str, Any] = {}
    overrides: dict[str, Any] = {}
    seed: int | None = None
    confidence: float = Field(ge=0, le=1)


def kind_table() -> str:
    rows = [f"- {k}: " + (", ".join(f"{a} ({ARG_TYPES.get(a, 'value')})" for a in c.EVENT_ARGS[k]) or "no args")
            for k in EXOGENOUS]
    return "## Event kinds and their args\n" + "\n".join(rows)


SYSTEM = meta_prompt("scenario_designer") + "\n\n" + kind_table()  # type: ignore[arg-type]  # static: caches


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "_", (s or "").strip().lower()).strip("_-")
    return s[:48] or "described"


def _crc(text: str) -> int:
    return zlib.crc32(text.strip().encode("utf-8"))


def text_seed(text: str) -> int:
    return 1000 + _crc(text) % 9000


def _scenario_id(slug: str, text: str, existing: set[str] | None) -> str:
    sid = slugify(slug)
    taken = PROTECTED | (existing if existing is not None else {p.stem for p in SCENARIO_DIR.glob("*.yaml")})
    if sid in taken:
        sid = f"{sid}-{_crc(text) & 0xFFFF:04x}"
    return sid


def clean_draft(draft: ScenarioDraft, text: str, *, window: tuple[int, int] = WINDOW,
                existing: set[str] | None = None) -> tuple[dict, list[str], list[str]]:
    """Draft -> (raw scenario dict, errors, dropped). Pure: no LLM, no files written."""
    errors: list[str] = []
    dropped: list[str] = []
    if draft.confidence < MIN_CONFIDENCE:
        errors.append(f"not a scenario request (confidence {draft.confidence:.2f} < {MIN_CONFIDENCE})")
    d0, d1 = int(draft.days[0]), int(draft.days[1])
    if not (window[0] <= d0 <= d1 <= window[1]):
        errors.append(f"`days` must satisfy {window[0]} <= start <= end <= {window[1]}, got [{d0}, {d1}]")
    events: list[dict] = []
    for i, e in enumerate(draft.events):
        if e.kind not in EXOGENOUS:
            errors.append(f"event {i}: {e.kind!r} is a consequence the floor produces, not a scripted event; "
                          f"use one of {', '.join(EXOGENOUS)}")
            continue
        if not (d0 <= e.day <= d1):
            errors.append(f"event {i} ({e.kind}): day {e.day} outside the window [{d0}, {d1}]")
            continue
        unknown = sorted(set(e.args) - set(c.EVENT_ARGS[e.kind]))
        args = validate_parse(c.EventParse(kind=e.kind, args=e.args, confidence=1.0))
        if args is None:
            dropped.append(f"event {i} ({e.kind} day {e.day}) dropped: invalid args {e.args}")
            continue
        if unknown:
            dropped.append(f"event {i} ({e.kind} day {e.day}): unknown args {unknown} removed")
        ev: dict[str, Any] = {"kind": e.kind, "day": e.day}
        if e.hour != 6:
            ev["hour"] = e.hour
        ev["args"] = {k: int(v) if isinstance(v, float) and v.is_integer() else v for k, v in args.items()}
        events.append(ev)
    perturbation: dict[str, Any] = {}
    for k, v in (draft.perturbation or {}).items():
        if k in PERTURBATION_KEYS:
            perturbation[k] = v
        else:
            dropped.append(f"perturbation.{k} removed (allowed: {', '.join(PERTURBATION_KEYS)})")
    overrides: dict[str, Any] = {}
    for k, v in (draft.overrides or {}).items():
        if k in OVERRIDE_KEYS:
            if v:
                overrides[k] = v
        else:
            dropped.append(f"overrides.{k} removed (allowed: {', '.join(OVERRIDE_KEYS)})")
    # Demo-sized windows: the model often returns the full [61, 90] even for a one-week story. Keep at
    # least 10 days and 3 days past the last event, and trim the rest (a judge-typed scenario should
    # run in minutes, not a 30-day backtest).
    if events and not errors:
        last_end = max(e["day"] + int(e["args"].get("days", 1) or 1) - 1 for e in events)
        d1 = max(min(d1, last_end + 3), min(d1, d0 + 9))
    raw = {"id": _scenario_id(draft.slug, text, existing),
           "seed": int(draft.seed) if draft.seed is not None else text_seed(text),
           "days": [d0, d1], "events": events, "perturbation": perturbation, "overrides": overrides}
    if not errors:
        errors.extend(validate_scenario(raw))
    if not errors:
        try:
            c.Scenario.model_validate({**raw, "days": tuple(raw["days"])})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"contract: {exc}")
    return raw, errors, dropped


def to_yaml(raw: dict) -> str:
    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, default_flow_style=None)


def _messages(text: str, window: tuple[int, int]) -> list[dict]:
    board = f"Window: days {window[0]}-{window[1]} (default [{DEFAULT_DAYS[0]}, {DEFAULT_DAYS[1]}])."
    task = f'Design the scenario for this request.\n\nRequest:\n"""\n{text.strip()}\n"""'
    return build_messages(SYSTEM, [], board, task)


def _call(llm: Any, msgs: list[dict]) -> ScenarioDraft:
    draft, _meta = llm.complete(TIER, msgs, ScenarioDraft, role="scenario_designer", run_id=None,
                                max_tokens=MAX_TOKENS)
    if not isinstance(draft, ScenarioDraft):
        raise TypeError(f"expected ScenarioDraft, got {type(draft).__name__}")
    return draft


def describe(text: str, *, llm: Any = None, window: tuple[int, int] = WINDOW,
             existing: set[str] | None = None) -> dict:
    """Full result: {ok, yaml, scenario, errors, dropped, summary, confidence, draft, calls}."""
    out: dict[str, Any] = {"ok": False, "yaml": "", "scenario": {}, "errors": [], "dropped": [], "summary": "",
                           "confidence": None, "draft": None, "calls": 0}
    if not (text or "").strip():
        out["errors"] = ["empty request"]
        return out
    if llm is None:
        from llm.client import LLM
        llm = LLM()
    msgs = _messages(text, window)
    try:
        out["calls"] = 1
        draft = _call(llm, msgs)
    except Exception as exc:  # noqa: BLE001 - the console shows the error and keeps the manual dialog
        log.warning("scenario designer LLM call failed: %s", exc)
        out["errors"] = [f"scenario designer LLM call failed: {exc}"]
        return out
    raw, errors, dropped = clean_draft(draft, text, window=window, existing=existing)
    if errors:  # one repair round, the errors fed back
        repair = msgs + [
            {"role": "assistant", "content": draft.model_dump_json()},
            {"role": "user", "content": "That scenario failed validation:\n" + "\n".join(f"- {e}" for e in errors)
                                        + "\nFix these problems and reply with only the corrected JSON."}]
        try:
            out["calls"] = 2
            draft2 = _call(llm, repair)
            raw2, errors2, dropped2 = clean_draft(draft2, text, window=window, existing=existing)
            if len(errors2) <= len(errors):
                draft, raw, errors, dropped = draft2, raw2, errors2, dropped2
        except Exception as exc:  # noqa: BLE001
            log.warning("scenario designer repair call failed: %s", exc)
            errors = errors + [f"repair call failed: {exc}"]
    out.update(ok=not errors, yaml=to_yaml(raw), scenario=raw, errors=errors, dropped=dropped,
               summary=draft.summary, confidence=draft.confidence, draft=draft)
    return out


def design_scenario(llm: Any, text: str, *, window: tuple[int, int] = WINDOW,
                    existing: set[str] | None = None) -> tuple[dict, list[str], ScenarioDraft | None]:
    """Console API (`POST /scenarios/describe`): (raw scenario dict, errors, draft or None on LLM failure).
    Dropped-event notes are not errors (the scenario still validates); `describe()` returns them."""
    r = describe(text, llm=llm, window=window, existing=existing)
    return r["scenario"], r["errors"], r["draft"]
