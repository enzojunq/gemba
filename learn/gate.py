"""The GATE (spec 13.2): run the test scenarios with playbook `version_from` and the candidate
`version_to`; promote the candidate only if the mean test score improves AND violations (mean
per run) do not increase.

    gate(test_scenario_ids, profile_id, version_from, version_to, store=, twin=, seeds=, parallel=4)

Contenders: `crew` for v0 (the empty playbook), `crew_<version>` otherwise (eval.run_eval
loads each version's non-rejected rules, so the candidate rows are used before promotion).
On promotion the candidate rows of `version_to` become "active"; otherwise they become
"rejected" (so `load_rules(version_to)` is then []). Either way one `eval_results` row with
contender "gate" records the summary (evaluate() already writes one row per run).

CLI:
    python -m learn.gate --test test_1,test_2,test_3 --from v0 --to v1 [--profile customer_first] [--seeds 1]
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

from engine.store import RunStore

log = logging.getLogger(__name__)


@dataclass
class GateResult:
    promoted: bool
    mean_score_from: float
    mean_score_to: float
    violations_from: float
    violations_to: float
    rows: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def contender_for(version: str) -> str:
    return "crew" if version in ("v0", "", None) else f"crew_{version}"


def gate(test_scenario_ids: list[str], profile_id: str, version_from: str, version_to: str, *,
         store: RunStore, twin: Any = None, seeds: list[int] | None = None, parallel: int = 4,
         llm: Any = None, days: tuple[int, int] | None = None) -> GateResult:
    from eval.report import gate_summary  # lazy: owned by the eval lane
    from eval.run_eval import evaluate

    c_from, c_to = contender_for(version_from), contender_for(version_to)
    if c_from == c_to:
        raise ValueError(f"gate needs two different versions, got {version_from} and {version_to}")
    kwargs: dict[str, Any] = dict(parallel=parallel, store=store, twin=twin)
    if llm is not None:
        kwargs["llm"] = llm
    if days is not None:
        kwargs["days"] = days
    rows = evaluate(list(test_scenario_ids), profile_id, [c_from, c_to], seeds, **kwargs)
    rows_from = [r for r in rows if r.contender == c_from]
    rows_to = [r for r in rows if r.contender == c_to]
    s = gate_summary(rows_from, rows_to)
    sf, st = float(s["mean_score_v0"]), float(s["mean_score_v1"])
    vf, vt = float(s["violations_v0"]), float(s["violations_v1"])
    promoted = bool(rows_from and rows_to and st > sf and vt <= vf)

    if promoted:
        n = store.set_playbook_status(version_to, "active", from_status="candidate")
        log.info("gate: promoted %s (%d rules active)", version_to, n)
    else:
        store.set_playbook_status(version_to, "rejected", from_status="candidate")
        log.info("gate: %s not promoted", version_to)
    store.write_eval_result(f"gate-{uuid.uuid4().hex[:8]}", ",".join(test_scenario_ids), profile_id, "gate",
                            version_to, None, {
                                "version_from": version_from, "version_to": version_to, "promoted": promoted,
                                "mean_score_from": sf, "mean_score_to": st,
                                "violations_from": vf, "violations_to": vt,
                                "run_ids": [r.run_id for r in rows]})
    return GateResult(promoted, sf, st, vf, vt, rows, dict(s))


def _csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m learn.gate", description="Playbook gate (spec 13.2)")
    ap.add_argument("--test", default="test_1,test_2,test_3")
    ap.add_argument("--from", dest="version_from", default="v0")
    ap.add_argument("--to", dest="version_to", default="v1")
    ap.add_argument("--profile", default="customer_first")
    ap.add_argument("--seeds", default="", help="comma-separated; default: each scenario's own seed")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--twin", default=None)
    ap.add_argument("--store", default=None)
    a = ap.parse_args(argv)
    store = RunStore(a.store)
    store.create_schema()
    seeds = [int(x) for x in _csv(a.seeds)] or None
    from llm.client import LLM
    llm = LLM(on_call=lambda row: store.write_llm_call(row))
    res = gate(_csv(a.test), a.profile, a.version_from, a.version_to, store=store, twin=a.twin, seeds=seeds,
               parallel=a.parallel, llm=llm)
    print(f"{a.version_from}: mean score {res.mean_score_from:+.4f}, violations {res.violations_from:.2f}")
    print(f"{a.version_to}: mean score {res.mean_score_to:+.4f}, violations {res.violations_to:.2f}")
    print(f"{'PROMOTED' if res.promoted else 'NOT promoted'}: {a.version_to}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    raise SystemExit(main())
