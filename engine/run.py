"""The day loop (CONTRACT section 6, spec 11.1). The engine owns the clock.

    run(scenario, profile, policy, twin, ...) -> run_id

`policy` and `twin` are instances (tests inject `StubTwin`; integration swaps in A's twin).
Forks reuse this loop through `fork=ForkSpec(...)` (see engine/fork.py). Nothing here reads
the wall clock in a way that affects results; run ids come from the store.

CLI:
    python -m engine.run --scenario reslot_default --profile customer_first --policy static --seed 1 [--days 61-70]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import contracts as c
from engine import events as ev
from engine import snapshot, verifier
from engine.drift import DriftDetector
from engine.store import RunStore
from org.scoring import aggregate_run, load_profile, score_kpis

log = logging.getLogger(__name__)

RECALIBRATE_WINDOW = 3  # spec 9.4a: actual / forecast over the last 3 days


# --------------------------------------------------------------------------- factories


def make_policy(name: str, run_id: str, llm: Any = None, **kwargs: Any) -> c.DecisionPolicy:
    """Baselines from `eval.POLICIES`; "org" imports `org.standup.OrgPolicy` lazily."""
    if name == "org":
        try:
            from org.standup import OrgPolicy  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError("policy 'org' needs org.standup.OrgPolicy, which is not built yet; "
                               "use one of the baselines: do_nothing, big_bang, static") from e
        return OrgPolicy(run_id=run_id, llm=llm, **kwargs)
    from eval import POLICIES
    if name not in POLICIES:
        raise ValueError(f"unknown policy {name!r}; choose org or one of {sorted(POLICIES)}")
    return POLICIES[name](run_id)


def make_twin(name: str | None = None) -> c.FloorTwin:
    """"stub" -> engine.stub_twin.StubTwin; "real" -> A's twin (`floor.twin.make_twin()` or
    `floor.twin.Twin()`); "auto" (default, or $GEMBA_TWIN) -> real if importable, else stub."""
    name = name or os.environ.get("GEMBA_TWIN", "auto")
    if name == "stub":
        from engine.stub_twin import StubTwin
        return StubTwin()
    try:
        import floor.twin as ft  # type: ignore[import-not-found]
    except ImportError:
        if name == "real":
            raise RuntimeError("twin 'real' needs floor.twin (Person A), which is not importable yet")
        from engine.stub_twin import StubTwin
        log.warning("floor.twin not importable; using StubTwin")
        return StubTwin()
    if hasattr(ft, "make_twin"):
        return ft.make_twin()
    if hasattr(ft, "Twin"):
        return ft.Twin()
    raise RuntimeError("floor.twin must expose make_twin() or Twin")


# --------------------------------------------------------------------------- forks


@dataclass
class ForkSpec:
    """Built by engine.fork.prepare_fork: the child run row exists and the parent's rows
    before `day` are already copied."""
    parent_run_id: str
    child_run_id: str
    day: int
    levers: list[c.Lever] = field(default_factory=list)
    constraints: list[c.Event] = field(default_factory=list)
    payload: dict | None = None  # parent's snapshot from the end of day - 1 (None if day is the first day)


def forced_decision(spec: ForkSpec) -> c.Decision:
    did = f"{spec.child_run_id}-fork"
    return c.Decision(decision_id=did, day=spec.day,
                      levers=[lv.model_copy(update={"decision_id": did}) for lv in spec.levers],
                      constraints=list(spec.constraints), decided_by="fork",
                      rationale=f"forced fork decision on day {spec.day} (parent {spec.parent_run_id})")


# --------------------------------------------------------------------------- helpers


def _event_end(e: c.Event) -> int:
    return e.day + max(1, int(e.args.get("days", 1))) - 1


def _predicted(obj: Any) -> tuple[c.Kpis | None, float | None]:
    """Decision.predicted may be a WhatIf (first day's KPIs + expected near-misses) or Kpis."""
    if obj is None:
        return None, None
    if isinstance(obj, c.WhatIf):
        return (obj.kpis_by_day[0] if obj.kpis_by_day else obj.kpis_total), obj.expected_near_misses
    if isinstance(obj, c.Kpis):
        return obj, obj.near_misses
    if isinstance(obj, dict):
        k = c.Kpis.model_validate(obj)
        return k, k.near_misses
    raise TypeError(f"Decision.predicted must be WhatIf | Kpis | dict, got {type(obj).__name__}")


def _stamp(decision: c.Decision) -> c.Decision:
    """Levers reaching the twin always carry the decision id (CONTRACT 3.2)."""
    if all(lv.decision_id for lv in decision.levers):
        return decision
    levers = [lv if lv.decision_id else lv.model_copy(update={"decision_id": decision.decision_id})
              for lv in decision.levers]
    return decision.model_copy(update={"levers": levers})


def _reslot_n(decision: c.Decision) -> int | None:
    ns = [lv.n_moves for lv in decision.levers if lv.kind == "reslot_tonight"]
    return sum(ns) if ns else None


def _dedupe(events: list[c.Event]) -> list[c.Event]:
    seen, out = set(), []
    for e in events:
        if e.event_id not in seen:
            seen.add(e.event_id)
            out.append(e)
    return out


@dataclass
class _Ctx:
    """Everything the loop carries between days; snapshotted after each day."""
    pending: list[c.Event] = field(default_factory=list)  # every consequence generated so far
    inbox_events: list[c.Event] = field(default_factory=list)
    constraints: list[c.Event] = field(default_factory=list)  # Decision.constraints still active
    checks: list[c.DoneCheck] = field(default_factory=list)
    drift: DriftDetector = field(default_factory=DriftDetector)
    paused_until: int = 0
    history: list[tuple[c.DayResult, c.Kpis]] = field(default_factory=list)  # (actual, predicted) pairs


# --------------------------------------------------------------------------- the loop


def run(scenario: c.Scenario | str, profile: c.Profile | str, policy: c.DecisionPolicy, twin: c.FloorTwin, *,
        playbook_version: str = "v0", mode: str = "backtest", seed: int | None = None,
        store: RunStore | None = None, fork: ForkSpec | None = None, run_id: str | None = None,
        ref_kpis: c.Kpis | None = None, do_nothing_kpis: c.Kpis | None = None,
        snapshot_dir: str | None = None) -> str:
    """Run the day loop and return the run id.

    - `run_id`: use an existing `runs` row (engine.api creates it first so it can return at once).
    - `ref_kpis`: aggregated KPIs of the REF run (static-rules baseline, see verifier) for the
      derived `final.score` check; `do_nothing_kpis` for `final.lph_lift_pct`. Without them
      those derived checks are recorded as skipped (passed=None).
    """
    scenario = ev.load_scenario(scenario) if isinstance(scenario, str) else scenario
    profile = load_profile(profile) if isinstance(profile, str) else profile
    if seed is not None and fork is None:
        scenario = scenario.model_copy(update={"seed": seed})
    store = store or RunStore()
    store.create_schema()

    if fork is not None:
        run_id = fork.child_run_id
    if run_id is not None:
        store.set_status(run_id, "running")
    else:
        run_id = store.create_run(mode, scenario.id, profile.id, playbook_version, scenario.seed)
    if getattr(policy, "run_id", None) == "":
        policy.run_id = run_id  # baselines prefix decision ids with the run id

    try:
        _loop(run_id, scenario, profile, policy, twin, store, fork, ref_kpis, do_nothing_kpis, snapshot_dir)
    except BaseException:
        log.exception("run %s failed", run_id)
        store.finish_run(run_id, "failed", llm_usd=store.llm_usd_total(run_id))
        raise
    return run_id


def _loop(run_id: str, scenario: c.Scenario, profile: c.Profile, policy: c.DecisionPolicy, twin: c.FloorTwin,
          store: RunStore, fork: ForkSpec | None, ref_kpis: c.Kpis | None, do_nothing_kpis: c.Kpis | None,
          snapshot_dir: str | None) -> None:
    calendar = ev.build_calendar(scenario, run_id)
    state = twin.initial_state(scenario)
    days = list(scenario.day_range)
    ctx = _Ctx()
    budget = profile.budgets.get("llm_usd_per_run")
    policy_name = getattr(policy, "name", type(policy).__name__)

    if fork is not None and fork.payload is not None:
        p = fork.payload
        state = p["state"]
        ctx.pending = list(p["pending_consequences"])
        ctx.inbox_events = list(p.get("known_inbox_events", []))
        ctx.constraints = list(p.get("active_constraints", []))
        ctx.checks = list(p["done_checks"])
        ctx.drift = DriftDetector.from_counters(p.get("drift_counters"))
        ctx.paused_until = p.get("paused_until", 0)
        ctx.history = list(p.get("predictions", []))
        # Their edges were copied (written before fork_day); their rows were not (day >= fork_day).
        store.relog_events(run_id, ctx.pending + ctx.inbox_events)
    else:
        ctx.checks = policy.pick_thresholds(verifier.template(profile))
    if fork is not None:
        days = [d for d in days if d >= fork.day]

    for day in days:
        # ---- events known this morning
        inbox_today = [e for t in store.inbox_pop(run_id, day)
                       if (e := ev.parse_inbox(t, run_id=run_id, day=day, llm=getattr(policy, "llm", None))) is not None]
        ctx.inbox_events += inbox_today
        today = _dedupe([e for e in calendar if e.day == day] + inbox_today
                        + [e for e in ctx.pending if e.day == day])
        known = _dedupe(calendar + ctx.inbox_events + ctx.pending + ctx.constraints)

        # ---- decide
        view = twin.view(state)
        if fork is not None and day == fork.day:
            decision = forced_decision(fork)
        else:
            decision = policy.decide(day, view, c.visible(known, day), twin, state)
        decision = _stamp(decision)
        ctx.constraints = _dedupe(ctx.constraints + list(decision.constraints))
        for lv in decision.levers:
            if lv.kind == "pause_reslot":
                ctx.paused_until = max(ctx.paused_until, day + lv.days - 1)

        # ---- act and simulate
        state, rejections = twin.apply_levers(state, decision.levers, day)
        step_events = c.active(_dedupe(known + list(decision.constraints)), day)
        state, result = twin.step_day(state, day, twin.actual_demand(day), step_events)
        new_cons = list(twin.consequences(state, result, run_id))
        for e in new_cons:
            if e.day <= day:
                log.warning("consequence %s is dated day %s <= today %s; it will never be delivered",
                            e.event_id, e.day, day)
        ctx.pending = _dedupe(ctx.pending + new_cons)

        # ---- verify, drift, recalibrate
        predicted, exp_nm = _predicted(getattr(decision, "predicted", None))
        if predicted is not None:
            ctx.history.append((result, predicted))
        derived = {"expected_near_misses": exp_nm} if exp_nm is not None else {}
        check_rows = [r.row() for r in verifier.check_day(ctx.checks, result.kpis, derived)]

        explained = (ctx.paused_until >= day or view.move_queue == 0
                     or any(e.day <= day <= _event_end(e) for e in known + list(decision.constraints)))
        evidence_today = [r["spec_hash"] for r in store.table("evidence", run_id) if r.get("created_day") == day]
        flags = ctx.drift.check(day, {
            "spec_hashes": evidence_today, "reslot_n_moves": _reslot_n(decision),
            "moves_done": result.kpis.moves_done, "explained_stall": explained,
            "actual": result.kpis, "predicted": predicted,
            "llm_usd": store.llm_usd_total(run_id), "budget_usd": budget,
        })
        responder = getattr(policy, "on_drift", None)
        for f in flags:
            if callable(responder):
                responder(day, f)  # the org chair chooses continue|replan|shrink|stop
            if f.kind == "prediction_error":
                window = ctx.history[-RECALIBRATE_WINDOW:]
                state = twin.recalibrate(state, [a for a, _ in window], [p for _, p in window])

        ctx.constraints = [e for e in ctx.constraints if _event_end(e) > day]

        # ---- snapshot (end of day) and the one-transaction day write
        hires = [h for h in store.table("hires", run_id) if h.get("released_day") is None]
        path = snapshot.save(run_id, day, {
            "state": state, "pending_consequences": ctx.pending, "active_constraints": ctx.constraints,
            "hires": hires, "done_checks": ctx.checks, "drift_counters": ctx.drift.counters(),
            "rng_state": None, "scenario": scenario, "known_inbox_events": ctx.inbox_events,
            "paused_until": ctx.paused_until, "predictions": ctx.history, "policy_name": policy_name,
            "profile_id": profile.id,
        }, base=snapshot_dir)
        store.write_day(run_id, day, decision, rejections, result, predicted=predicted, events=today,
                        new_consequences=new_cons, checks=check_rows, drift_flags=[f.row() for f in flags],
                        snapshot_path=path,
                        deviates_from_top_score=bool(getattr(decision, "deviates_from_top_score", False)))

    # ---- final scope
    rows = store.day_results(run_id)
    if rows:
        agg = aggregate_run([c.Kpis.model_validate(r["kpis"]) for r in rows])
        derived: dict[str, float] = {}
        if ref_kpis is not None:
            derived["score"] = score_kpis(agg, ref_kpis, profile).total
        if do_nothing_kpis is not None and do_nothing_kpis.lph:
            derived["lph_lift_pct"] = 100.0 * (agg.lph - do_nothing_kpis.lph) / do_nothing_kpis.lph
        final = verifier.check_final(ctx.checks, agg, derived)
        store.write_checks(run_id, rows[-1]["day"], [r.row() for r in final])
    store.finish_run(run_id, "done", llm_usd=store.llm_usd_total(run_id))


# --------------------------------------------------------------------------- CLI


def summary(store: RunStore, run_id: str) -> list[str]:
    rows = store.day_results(run_id)
    if not rows:
        return ["(no days)"]
    a = aggregate_run([c.Kpis.model_validate(r["kpis"]) for r in rows])
    return [
        f"on_time_pct (mean)        {a.on_time_pct:8.2f}",
        f"mispick_rate (mean)       {a.mispick_rate:8.3f}",
        f"lph (mean)                {a.lph:8.2f}",
        f"reslot_progress_pct (end) {a.reslot_progress_pct:8.2f}",
        f"labor_cost_usd (sum)      {a.labor_cost_usd:8.0f}",
    ]


def parse_days(s: str) -> tuple[int, int]:
    a, _, b = s.partition("-")
    return int(a), int(b or a)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engine.run", description=__doc__.split("\n")[0])
    ap.add_argument("--scenario", default="reslot_default")
    ap.add_argument("--profile", default="customer_first")
    ap.add_argument("--policy", default="static")
    ap.add_argument("--playbook", default="v0")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--days", type=parse_days, default=None, help="trim the scenario range, e.g. 61-70")
    ap.add_argument("--twin", default=None, help="stub | real | auto (default $GEMBA_TWIN or auto)")
    ap.add_argument("--mode", default="backtest")
    ap.add_argument("--store", default=None, help="SQLAlchemy URL (default $RUN_STORE_URL or sqlite:///runs.db)")
    args = ap.parse_args(argv)

    sc = ev.load_scenario(args.scenario)
    if args.days:
        sc = sc.model_copy(update={"days": args.days})
        sc = c.Scenario.model_validate(sc.model_dump())
    store = RunStore(args.store)
    store.create_schema()
    run_id = store.create_run(args.mode, sc.id, args.profile, args.playbook,
                              args.seed if args.seed is not None else sc.seed)
    policy = make_policy(args.policy, run_id)
    run(sc, args.profile, policy, make_twin(args.twin), playbook_version=args.playbook, mode=args.mode,
        seed=args.seed, store=store, run_id=run_id)
    print(run_id)
    for line in summary(store, run_id):
        print(line)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    raise SystemExit(main())
