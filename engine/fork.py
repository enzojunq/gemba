"""Forks (spec 11.2, CONTRACT 8): branch a run at `day` with a forced decision.

The child gets a new `runs` row (parent_run_id, fork_day, fork_decision), a copy of the
parent's store rows before `day`, and resumes from the parent's snapshot at the end of
`day - 1`. On `day` the forced decision replaces the policy; from `day + 1` the policy
decides normally. The loop itself is `engine.run.run(..., fork=ForkSpec(...))`.

CLI (Makefile `make fork`):
    python -m engine.fork --run <parent_run_id> --day 69 --levers '[{"kind":"pause_reslot","days":5}]'
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

import contracts as c
from engine import events as ev
from engine import snapshot
from engine.run import ForkSpec, make_policy, make_twin, run
from engine.store import RunStore
from org.scoring import load_profile

_LEVERS = TypeAdapter(list[c.Lever])


def _levers(levers: list[Any]) -> list[c.Lever]:
    return _LEVERS.validate_python([lv.model_dump() if hasattr(lv, "model_dump") else lv for lv in levers])


def _constraints(cons: list[Any]) -> list[c.Event]:
    return [x if isinstance(x, c.Event) else c.Event.model_validate(x) for x in cons]


def parent_snapshot(store: RunStore, parent_run_id: str, day: int) -> dict | None:
    """The parent's end-of-(day-1) snapshot, or None when `day` is the parent's first day."""
    rows = {r["day"]: r for r in store.day_results(parent_run_id)}
    if day - 1 not in rows:
        if rows and day > min(rows):
            raise ValueError(f"parent {parent_run_id} has no day {day - 1}")
        return None
    path = rows[day - 1].get("snapshot_path") or str(snapshot.path_for(parent_run_id, day - 1))
    if not Path(path).exists():
        raise FileNotFoundError(f"snapshot missing for {parent_run_id} day {day - 1}: {path}")
    return snapshot.load(path)


def prepare_fork(parent_run_id: str, day: int, levers: list = (), constraints: list = (), *,
                 store: RunStore, profile: c.Profile | str | None = None, policy_name: str | None = None):
    """Create the child row and copy the prefix. Returns (child_run_id, ForkSpec, scenario,
    profile, policy_name). Nothing is simulated yet."""
    parent = store.get_run(parent_run_id)
    if parent is None:
        raise KeyError(f"unknown run {parent_run_id}")
    payload = parent_snapshot(store, parent_run_id, day)
    scenario = payload["scenario"] if payload else ev.load_scenario(parent["scenario_id"])
    if not scenario.days[0] <= day <= scenario.days[1]:
        raise ValueError(f"fork day {day} outside the parent's range {scenario.days}")
    prof = profile if isinstance(profile, c.Profile) else load_profile(profile or parent["profile_id"])
    policy_name = policy_name or (payload or {}).get("policy_name") or "static"
    lvs, cons = _levers(list(levers)), _constraints(list(constraints))
    child = store.create_run(
        "fork", scenario.id, prof.id, parent["playbook_version"], parent["seed"],
        parent_run_id=parent_run_id, fork_day=day, status="queued",
        fork_decision={"levers": [lv.model_dump(mode="json") for lv in lvs],
                       "constraints": [e.model_dump(mode="json") for e in cons], "policy": policy_name})
    store.copy_run_prefix(parent_run_id, child, before_day=day)
    spec = ForkSpec(parent_run_id=parent_run_id, child_run_id=child, day=day, levers=lvs, constraints=cons,
                    payload=payload)
    return child, spec, scenario, prof, policy_name


def fork_run(parent_run_id: str, day: int, levers: list = (), constraints: list = (), *,
             twin: c.FloorTwin | None = None, profile: c.Profile | str | None = None,
             policy_name: str | None = None, store: RunStore | None = None,
             snapshot_dir: str | None = None, horizon: int | None = None,
             policy_kwargs: dict | None = None) -> str:
    """Fork synchronously and return the child run id.

    `horizon` (added for learn/retro): simulate only days `day .. day + horizon - 1` instead of
    the parent's remaining range. `policy_kwargs` are passed to `make_policy` (e.g. `llm`,
    `store`, `profile`, `playbook_rules` for "org"; baselines ignore them)."""
    store = store or RunStore()
    store.create_schema()
    child, spec, scenario, prof, policy_name = prepare_fork(
        parent_run_id, day, levers, constraints, store=store, profile=profile, policy_name=policy_name)
    if horizon is not None:
        if horizon < 1:
            raise ValueError("horizon must be >= 1")
        scenario = scenario.model_copy(update={"days": (scenario.days[0], min(scenario.days[1], day + horizon - 1))})
    run(scenario, prof, make_policy(policy_name, child, **(policy_kwargs or {})), twin or make_twin(), mode="fork",
        playbook_version=store.get_run(child)["playbook_version"] or "v0", store=store, fork=spec,
        snapshot_dir=snapshot_dir)
    return child


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engine.fork")
    ap.add_argument("--run", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--levers", default="[]", help="JSON list of levers")
    ap.add_argument("--constraints", default="[]", help="JSON list of events")
    ap.add_argument("--policy", default=None)
    ap.add_argument("--twin", default=None)
    ap.add_argument("--store", default=None)
    a = ap.parse_args(argv)
    store = RunStore(a.store)
    child = fork_run(a.run, a.day, json.loads(a.levers or "[]"), json.loads(a.constraints or "[]"),
                     twin=make_twin(a.twin), policy_name=a.policy, store=store)
    from engine.run import summary
    print(child)
    for line in summary(store, child):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
