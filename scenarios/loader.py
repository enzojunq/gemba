"""Scenario YAML loader and validator (CONTRACT 3.9). `POST /scenarios` uses `validate_scenario` too."""

from __future__ import annotations

from pathlib import Path

import yaml

import contracts as c

N_AISLES, N_ZONES = 28, 6
POOLS = set(c.Pool.__args__)  # type: ignore[attr-defined]
SCENARIO_DIR = Path(__file__).resolve().parent


def validate_scenario(raw: dict) -> list[str]:
    """Return a list of problems (empty when valid). Checks ids, days inside 1-90 with events inside the
    window, known event kinds and args, aisles 1-28, zones 1-6, pools valid."""
    errs: list[str] = []
    if not isinstance(raw, dict):
        return ["scenario must be a mapping"]
    for key in ("id", "seed", "days"):
        if key not in raw:
            errs.append(f"missing `{key}`")
    days = raw.get("days")
    if not (isinstance(days, (list, tuple)) and len(days) == 2 and all(isinstance(x, int) for x in days)):
        errs.append("`days` must be [start, end]")
        d0, d1 = 1, 90
    else:
        d0, d1 = int(days[0]), int(days[1])
        if not (1 <= d0 <= d1 <= 90):
            errs.append(f"`days` must satisfy 1 <= start <= end <= 90, got {days}")
    for i, e in enumerate(raw.get("events") or []):
        kind = e.get("kind")
        if kind not in c.EVENT_KINDS:
            errs.append(f"event {i}: unknown kind {kind!r}")
            continue
        day = e.get("day")
        if not isinstance(day, int) or not (d0 <= day <= d1):
            errs.append(f"event {i} ({kind}): day {day} outside [{d0}, {d1}]")
        args = e.get("args") or {}
        extra = set(args) - set(c.EVENT_ARGS[kind])
        if extra:
            errs.append(f"event {i} ({kind}): unknown args {sorted(extra)}")
        for key in ("aisle",):
            if key in args and not (isinstance(args[key], int) and 1 <= args[key] <= N_AISLES):
                errs.append(f"event {i} ({kind}): aisle {args[key]} not in 1-{N_AISLES}")
        if "no_moves_in_aisles" in args:
            bad = [a for a in args["no_moves_in_aisles"] if not (isinstance(a, int) and 1 <= a <= N_AISLES)]
            if bad:
                errs.append(f"event {i} ({kind}): aisles {bad} not in 1-{N_AISLES}")
        if "zone" in args and not (isinstance(args["zone"], int) and 1 <= args["zone"] <= N_ZONES):
            errs.append(f"event {i} ({kind}): zone {args['zone']} not in 1-{N_ZONES}")
        if "pool" in args and args["pool"] not in POOLS:
            errs.append(f"event {i} ({kind}): pool {args['pool']!r} not a Pool")
        if kind == "demand_surge" and sum(k in args for k in ("skus", "family", "customer")) != 1:
            errs.append(f"event {i} (demand_surge): needs exactly one of skus | family | customer")
        if "days" in args and not (isinstance(args["days"], int) and args["days"] >= 1):
            errs.append(f"event {i} ({kind}): days must be an int >= 1")
    for shift, pools in (raw.get("overrides", {}).get("headcounts") or {}).items():
        if shift not in c.Shift.__args__:  # type: ignore[attr-defined]
            errs.append(f"overrides.headcounts: unknown shift {shift!r}")
        for p in pools or {}:
            if p not in POOLS:
                errs.append(f"overrides.headcounts.{shift}: unknown pool {p!r}")
    return errs


def scenario_from_dict(raw: dict) -> c.Scenario:
    errs = validate_scenario(raw)
    if errs:
        raise ValueError("invalid scenario: " + "; ".join(errs))
    events = [c.ScenarioEvent(kind=e["kind"], day=int(e["day"]), hour=int(e.get("hour", 6)), args=e.get("args") or {})
              for e in raw.get("events") or []]
    return c.Scenario(id=str(raw["id"]), seed=int(raw["seed"]), days=(int(raw["days"][0]), int(raw["days"][1])),
                      events=events, perturbation=raw.get("perturbation") or {}, overrides=raw.get("overrides") or {})


def load_scenario(path: str | Path) -> c.Scenario:
    """Load and validate a scenario YAML. A bare name (`reslot_default`) resolves inside `scenarios/`."""
    p = Path(path)
    if not p.exists():
        p = SCENARIO_DIR / f"{path}.yaml"
    with open(p) as f:
        raw = yaml.safe_load(f)
    return scenario_from_dict(raw)


def scenario_events(scenario: c.Scenario, prefix: str = "evt") -> list[c.Event]:
    """Turn the scenario calendar into `Event`s (scripted, no cause). Helper for A's own loops and tests."""
    return [c.Event(event_id=f"{prefix}-{e.kind}-{e.day}-{i}", kind=e.kind, day=e.day, hour=e.hour, args=e.args,
                    announced_day=c.announced_day(e.kind, e.day), source="scripted", caused_by=None)
            for i, e in enumerate(scenario.events)]
