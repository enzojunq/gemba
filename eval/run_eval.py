"""Eval harness (spec 15): run every contender on every scenario and seed, score them, store
`eval_results`, print the report table.

Contenders: `do_nothing`, `big_bang`, `static` (LLM-free baselines), `crew` (policy "org",
playbook v0 = empty) and `crew_vN` (policy "org" with playbook vN).

Scoring REF (spec 10.4): REF for a run = the do-nothing baseline run on the same scenario and
seed, so `do_nothing` always runs first for each (scenario, seed).

Seeds: an explicit seed overrides the scenario's own seed (as `engine.run --seed` does);
`seeds=None` runs each scenario once with the seed written in its YAML.

Parallel crews: the twin is CPU-bound, so with `parallel > 1` crew runs go to a
`ProcessPoolExecutor` (spawn-safe entry `_crew_job`, plain args only; each process opens its
own store from the URL, builds its own twin by name and its own LLM client). That needs a
file-backed store, a twin given by name (None/"stub"/"real"/"auto") and no in-memory `llm`
object; otherwise (tests with fake LLMs / twin factories, `:memory:` stores) a thread pool is
used, as before.

CLI:
    python -m eval.run_eval --scenario reslot_default --profile customer_first \
        --contenders do_nothing,big_bang,static[,crew] --seeds 1 [--days 61-70] [--parallel 4] [--out eval_out.md]
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import sys
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any

from pydantic import BaseModel

import contracts as c
from engine import events as ev
from engine.run import make_policy, parse_days, policy_kwargs, run, twin_factory
from engine.store import RunStore
from org.scoring import aggregate_run, load_profile, score_kpis

log = logging.getLogger(__name__)

BASELINES: tuple[str, ...] = ("do_nothing", "big_bang", "static")
DEFAULT_CONTENDERS: tuple[str, ...] = BASELINES  # `make baselines` stays LLM-free
METRIC_KEYS: tuple[str, ...] = (
    "score", "score_vs_static", "lph_lift_pct", "on_time_pct", "mispick_rate", "violations",
    "near_misses", "overtime_hours", "reslot_progress_pct", "llm_usd", "meetings_held", "hires",
)
"""Spec 15's report list, in report order."""


class EvalRow(BaseModel):
    scenario_id: str
    profile_id: str
    contender: str
    playbook_version: str | None
    seed: int
    run_id: str
    metrics: dict[str, float | None]


# --------------------------------------------------------------------------- contenders


def parse_contender(contender: str) -> tuple[str, str | None]:
    """contender -> (policy name, playbook version). Baselines have no playbook."""
    if contender in BASELINES:
        return contender, None
    if contender == "crew":
        return "org", "v0"
    if contender.startswith("crew_") and len(contender) > 5:
        return "org", contender[5:]
    raise ValueError(f"unknown contender {contender!r}; choose {', '.join(BASELINES)}, crew or crew_vN")


def load_playbook_rules(version: str | None, store: RunStore) -> list[dict]:
    """The rules of playbook `version` (v0 / None = the empty playbook), as the org gets them
    through `engine.run.policy_kwargs`."""
    if not version or version == "v0":
        return []
    from learn.playbook import load_rules
    return load_rules(store, version)


_twin_factory = twin_factory  # old name; see engine.run.twin_factory


def _scenario(scenario_id: str, seed: int | None, days: tuple[int, int] | None) -> c.Scenario:
    sc = ev.load_scenario(scenario_id)
    upd: dict[str, Any] = {}
    if seed is not None:
        upd["seed"] = seed
    if days is not None:
        upd["days"] = tuple(days)
    return c.Scenario.model_validate({**sc.model_dump(), **upd}) if upd else sc


def run_contender(contender: str, scenario_id: str, profile_id: str, seed: int | None, *, twin: Any = None,
                  store: RunStore | None = None, days: tuple[int, int] | None = None, playbook: str = "v0",
                  ref_kpis: c.Kpis | None = None, llm: Any = None, snapshot_dir: str | None = None) -> str:
    """Run one contender once and return its run id (mode "eval").

    `playbook` applies to `crew` (a `crew_vN` contender names its own version). `ref_kpis`
    (the do-nothing run's aggregated KPIs) feeds the run's final `score`/`lph_lift_pct` checks."""
    policy_name, pb = parse_contender(contender)
    if contender == "crew":
        pb = playbook
    store = store or RunStore()
    store.create_schema()
    sc = _scenario(scenario_id, seed, days)
    profile = load_profile(profile_id)
    run_id = store.create_run("eval", sc.id, profile.id, pb, sc.seed)
    if policy_name == "org":
        policy = make_policy("org", run_id, llm=llm, **policy_kwargs(profile, store, pb, sc))
    else:
        policy = make_policy(policy_name, run_id)
    tw = twin_factory(twin)()
    run(sc, profile, policy, tw, playbook_version=pb or "v0", mode="eval", store=store, run_id=run_id,
        ref_kpis=ref_kpis, do_nothing_kpis=ref_kpis, snapshot_dir=snapshot_dir)
    return run_id


def _crew_job(contender: str, scenario_id: str, profile_id: str, seed: int | None, playbook: str,
              store_url: str, twin_name: str | None, days: tuple[int, int] | None = None,
              ref_kpis: dict | None = None, snapshot_dir: str | None = None) -> str:
    """Process-pool entry (spawn-safe: module level, plain picklable args). Opens its own
    store, twin and (inside OrgPolicy) LLM client; returns the run id."""
    store = RunStore(store_url)
    ref = c.Kpis.model_validate(ref_kpis) if ref_kpis is not None else None
    return run_contender(contender, scenario_id, profile_id, seed, twin=twin_name, store=store,
                         days=tuple(days) if days else None, playbook=playbook, ref_kpis=ref,
                         snapshot_dir=snapshot_dir)


def _use_processes(parallel: int, n_jobs: int, store: RunStore, twin: Any, llm: Any) -> bool:
    url = getattr(store, "url", "") or ""
    in_memory = url.startswith("sqlite") and (":memory:" in url or url.rstrip("/") in ("sqlite:", "sqlite:/"))
    return parallel > 1 and n_jobs > 1 and llm is None and (twin is None or isinstance(twin, str)) \
        and not in_memory


# --------------------------------------------------------------------------- metrics


def run_kpis(store: RunStore, run_id: str) -> c.Kpis:
    rows = store.day_results(run_id)
    if not rows:
        raise RuntimeError(f"run {run_id} has no day_results")
    return aggregate_run([c.Kpis.model_validate(r["kpis"]) for r in rows])


def violations(store: RunStore, run_id: str, profile: c.Profile) -> int:
    """Failed daily checks at the profile's TEMPLATE thresholds (the same bar for every contender;
    these include the hard limits safety_risk_max and pm_peak_zone_load) plus any violations the
    twin reported in `DayResult.violations`. The `checks` rows are not used here: the org's chair
    picks its own, often stricter, thresholds (97 on-time vs the template's 95 in the first hero
    run), which is right for "the org writes its own done-checks" and wrong for a comparison."""
    from engine.verifier import check_day, template  # lazy: eval must import without the engine
    checks = template(profile)
    failed = 0
    rows = store.day_results(run_id)
    for r in rows:
        kpis = c.Kpis.model_validate(r["kpis"])
        failed += sum(1 for res in check_day(checks, kpis) if res.passed is False)
    twin_reported = sum(len(r.get("violations") or []) for r in rows)
    return failed + twin_reported


def run_metrics(store: RunStore, run_id: str, ref: c.Kpis, profile: c.Profile) -> dict[str, float | None]:
    """Spec 15's metrics for one run against the do-nothing REF (score_vs_static filled later)."""
    agg = run_kpis(store, run_id)
    return {
        "score": score_kpis(agg, ref, profile).total,
        "score_vs_static": None,
        "lph_lift_pct": 100.0 * (agg.lph - ref.lph) / ref.lph if ref.lph else 0.0,
        "on_time_pct": agg.on_time_pct,
        "mispick_rate": agg.mispick_rate,
        "violations": float(violations(store, run_id, profile)),
        "near_misses": agg.near_misses,
        "overtime_hours": agg.overtime_hours,
        "reslot_progress_pct": agg.reslot_progress_pct,
        "llm_usd": store.llm_usd_total(run_id),
        "meetings_held": float(sum(1 for m in store.meetings(run_id) if m.get("kind") != "quiet")),
        "hires": float(len(store.table("hires", run_id))),
    }


# --------------------------------------------------------------------------- evaluate


def evaluate(scenario_ids: list[str], profile_id: str, contenders: list[str], seeds: list[int] | None = None, *,
             parallel: int = 4, days: tuple[int, int] | None = None, store: RunStore | None = None,
             twin: Any = None, llm: Any = None, snapshot_dir: str | None = None) -> list[EvalRow]:
    """Run every (scenario, contender, seed); return one EvalRow each, also written to
    `eval_results`. Baselines run sequentially (do_nothing first, it is the REF); crew runs
    go through a pool of `parallel` workers (processes when possible, see the module doc).
    `do_nothing` and `static` are always run (REF and the "beats static" column) but only
    requested contenders are returned. `twin`: a name, a factory, or a StubTwin instance
    (deep-copied per run); see `engine.run.twin_factory`."""
    twin_factory(twin)  # validate early (a real-twin instance is refused)
    for ct in contenders:
        parse_contender(ct)  # fail fast on a typo
    store = store or RunStore()
    store.create_schema()
    profile = load_profile(profile_id)
    seed_list: list[int | None] = list(seeds) if seeds else [None]
    needed = list(dict.fromkeys(["do_nothing", "static", *contenders]))
    baselines = [ct for ct in needed if ct in BASELINES]
    crews = [ct for ct in needed if ct not in BASELINES]
    common = dict(twin=twin, store=store, days=days, snapshot_dir=snapshot_dir)

    run_ids: dict[tuple[str, int, str], str] = {}  # (scenario, seed, contender) -> run_id
    refs: dict[tuple[str, int], c.Kpis] = {}
    eff_seed: dict[tuple[str, int | None], int] = {}
    for sid in scenario_ids:
        for s in seed_list:
            seed = _scenario(sid, s, None).seed
            eff_seed[(sid, s)] = seed
            for ct in baselines:  # do_nothing is first in `needed`
                ref = refs.get((sid, seed))
                rid = run_contender(ct, sid, profile_id, seed, ref_kpis=ref, **common)
                run_ids[(sid, seed, ct)] = rid
                if ct == "do_nothing":
                    refs[(sid, seed)] = run_kpis(store, rid)

    if crews:
        jobs = [(sid, eff_seed[(sid, s)], ct) for sid in scenario_ids for s in seed_list for ct in crews]
        if _use_processes(parallel, len(jobs), store, twin, llm):
            ctx = multiprocessing.get_context("spawn")
            with ProcessPoolExecutor(max_workers=min(parallel, len(jobs)), mp_context=ctx) as pool:
                futs = {}
                for sid, seed, ct in jobs:
                    pbv = parse_contender(ct)[1] or "v0"
                    futs[(sid, seed, ct)] = pool.submit(
                        _crew_job, ct, sid, profile_id, seed, pbv, store.url, twin, days,
                        refs[(sid, seed)].model_dump(mode="json"), snapshot_dir)
                for job, f in futs.items():
                    run_ids[job] = f.result()
        else:
            with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
                futs = {job: pool.submit(run_contender, job[2], job[0], profile_id, job[1],
                                         ref_kpis=refs[(job[0], job[1])], llm=llm, **common) for job in jobs}
                for job, f in futs.items():
                    run_ids[job] = f.result()

    metrics = {k: run_metrics(store, rid, refs[(k[0], k[1])], profile) for k, rid in run_ids.items()}
    rows: list[EvalRow] = []
    for (sid, seed, ct), m in metrics.items():
        m["score_vs_static"] = m["score"] - metrics[(sid, seed, "static")]["score"]
        if ct not in contenders:
            continue
        pb = parse_contender(ct)[1]
        row = EvalRow(scenario_id=sid, profile_id=profile.id, contender=ct, playbook_version=pb, seed=seed,
                      run_id=run_ids[(sid, seed, ct)], metrics={k: m[k] for k in METRIC_KEYS})
        store.write_eval_result(uuid.uuid4().hex, sid, profile.id, ct, pb, seed,
                                dict(row.metrics, run_id=row.run_id))
        rows.append(row)
    order = {ct: i for i, ct in enumerate(contenders)}
    rows.sort(key=lambda r: (scenario_ids.index(r.scenario_id), order[r.contender], r.seed))
    return rows


# --------------------------------------------------------------------------- CLI


def _csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def main(argv: list[str] | None = None) -> int:
    from eval.report import render

    ap = argparse.ArgumentParser(prog="python -m eval.run_eval", description=__doc__.split("\n")[0])
    ap.add_argument("--scenario", default="reslot_default", help="one id or a comma list")
    ap.add_argument("--profile", default="customer_first")
    ap.add_argument("--contenders", default=",".join(DEFAULT_CONTENDERS))
    ap.add_argument("--seeds", default=None, help="comma list; default: each scenario's own seed")
    ap.add_argument("--days", type=parse_days, default=None, help="trim the scenario range, e.g. 61-70")
    ap.add_argument("--parallel", type=int, default=4, help="max concurrent crew (LLM) runs")
    ap.add_argument("--twin", default=None, help="stub | real | auto (default $GEMBA_TWIN or auto)")
    ap.add_argument("--store", default=None, help="SQLAlchemy URL (default $RUN_STORE_URL or sqlite:///runs.db)")
    ap.add_argument("--out", default=None, help="also write the Markdown report here")
    args = ap.parse_args(argv)

    seeds = [int(x) for x in _csv(args.seeds)] if args.seeds else None
    rows = evaluate(_csv(args.scenario), args.profile, _csv(args.contenders), seeds, parallel=args.parallel,
                    days=args.days, store=RunStore(args.store), twin=args.twin)
    text = render(rows)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    raise SystemExit(main())
