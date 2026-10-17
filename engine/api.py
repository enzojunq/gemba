"""Engine API (CONTRACT section 8): what A's console calls.

Runs execute in a background daemon thread (SQLite is in WAL mode, so the console reads
while the engine writes). `start_run` / `fork_run` return the run id as soon as its `runs`
row exists (status "queued"); the thread flips it to "running", then "done" or "failed".
A failed run's reason (repr of the exception) is in `runs.error`; read it with `run_error`.
The store is `RunStore()` ($RUN_STORE_URL, default sqlite:///runs.db) unless one is passed.

Single runs stay on threads (one run at a time per console click, and the store / LLM client
are shared in-process); eval's parallel crew runs use processes (eval/run_eval.py).
"""

from __future__ import annotations

import logging
import threading
from typing import Literal

import contracts as c
from engine import events as ev
from engine.fork import prepare_fork
from engine.run import make_policy, make_twin, policy_kwargs, run
from engine.store import RunStore
from org.scoring import load_profile

log = logging.getLogger(__name__)

Status = Literal["queued", "running", "done", "failed"]
_threads: dict[str, threading.Thread] = {}


def _store(store: RunStore | None) -> RunStore:
    s = store or RunStore()
    s.create_schema()
    return s


def _spawn(run_id: str, store: RunStore, target, *args, **kwargs) -> None:
    def body() -> None:
        try:
            target(*args, **kwargs)
        except BaseException as exc:  # run() already marked it failed if it got that far
            log.exception("background run %s failed", run_id)
            if (store.get_run(run_id) or {}).get("status") != "failed":
                store.finish_run(run_id, "failed", error=repr(exc)[:500])

    t = threading.Thread(target=body, name=f"run-{run_id}", daemon=True)
    _threads[run_id] = t
    t.start()


def start_run(scenario: str, profile: str, playbook: str, policy: str = "org", seed: int | None = None, *,
              store: RunStore | None = None, twin: c.FloorTwin | None = None,
              days: tuple[int, int] | None = None) -> str:
    """Start a run in the background; returns its run_id immediately."""
    store = _store(store)
    sc = ev.load_scenario(scenario)
    if seed is not None:
        sc = sc.model_copy(update={"seed": seed})
    if days is not None:
        sc = c.Scenario.model_validate({**sc.model_dump(), "days": days})
    prof = load_profile(profile)
    run_id = store.create_run("backtest", sc.id, prof.id, playbook, sc.seed, status="queued")

    def go() -> None:
        pol = make_policy(policy, run_id, **policy_kwargs(prof, store, playbook, sc))
        run(sc, prof, pol, twin or make_twin(), playbook_version=playbook, store=store, run_id=run_id)

    _spawn(run_id, store, go)
    return run_id


def fork_run(parent_run_id: str, day: int, levers: list[c.Lever] = [], constraints: list[c.Event] = [], *,
             store: RunStore | None = None, twin: c.FloorTwin | None = None) -> str:
    """Fork in the background; the child row and copied prefix exist when this returns."""
    store = _store(store)
    child, spec, sc, prof, policy_name = prepare_fork(parent_run_id, day, list(levers), list(constraints),
                                                      store=store)

    def go() -> None:
        playbook = store.get_run(child)["playbook_version"] or "v0"
        pol = make_policy(policy_name, child, **policy_kwargs(prof, store, playbook, sc))
        run(sc, prof, pol, twin or make_twin(), mode="fork", playbook_version=playbook, store=store, fork=spec)

    _spawn(child, store, go)
    return child


def run_status(run_id: str, *, store: RunStore | None = None) -> Status:
    r = (store or RunStore()).get_run(run_id)
    if r is None:
        raise KeyError(f"unknown run {run_id}")
    return r["status"]


def run_error(run_id: str, *, store: RunStore | None = None) -> str | None:
    """The failure reason of a failed run (repr of the exception), else None."""
    return (store or RunStore()).run_error(run_id)


def wait(run_id: str, timeout: float | None = None) -> None:
    """Join the background thread (tests, scripts)."""
    t = _threads.get(run_id)
    if t is not None:
        t.join(timeout)
