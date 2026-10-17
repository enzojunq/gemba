"""Eval report (spec 15) and the retro GATE rule (spec 13.2).

Every metric is reported, including the ones where a baseline wins; the best value per
column is bold. `meetings_held` and `hires` have no better direction, so they are never bold.
"""

from __future__ import annotations

from statistics import mean, stdev

from eval.run_eval import BASELINES, METRIC_KEYS, EvalRow

HIGHER_BETTER = {"score", "score_vs_static", "lph_lift_pct", "on_time_pct", "reslot_progress_pct"}
LOWER_BETTER = {"mispick_rate", "violations", "near_misses", "overtime_hours", "llm_usd"}
DECIMALS = {"score": 3, "score_vs_static": 3, "lph_lift_pct": 2, "on_time_pct": 2, "mispick_rate": 3,
            "violations": 1, "near_misses": 2, "overtime_hours": 1, "reslot_progress_pct": 1, "llm_usd": 3,
            "meetings_held": 1, "hires": 1}


def _contender_order(rows: list[EvalRow]) -> list[str]:
    seen = list(dict.fromkeys(r.contender for r in rows))
    known = [ct for ct in (*BASELINES, "crew") if ct in seen]
    return known + sorted(ct for ct in seen if ct not in known)


def _stats(rows: list[EvalRow], contender: str, key: str) -> tuple[float | None, float | None, int]:
    vals = [r.metrics.get(key) for r in rows if r.contender == contender]
    vals = [float(v) for v in vals if v is not None]
    if not vals:
        return None, None, 0
    return mean(vals), (stdev(vals) if len(vals) > 1 else None), len(vals)


def table(rows: list[EvalRow]) -> str:
    """Markdown: one row per contender (mean over its rows, ± sample std when > 1 seed),
    columns in spec 15 order, best value per column in bold (ties all bold)."""
    if not rows:
        return "(no eval rows)"
    contenders = _contender_order(rows)
    stats = {ct: {k: _stats(rows, ct, k) for k in METRIC_KEYS} for ct in contenders}
    best: dict[str, float] = {}
    for k in METRIC_KEYS:
        vals = [round(stats[ct][k][0], DECIMALS[k]) for ct in contenders if stats[ct][k][0] is not None]
        if vals and k in HIGHER_BETTER:
            best[k] = max(vals)
        elif vals and k in LOWER_BETTER:
            best[k] = min(vals)

    lines = ["| contender | seeds | " + " | ".join(METRIC_KEYS) + " |",
             "|---|---:|" + "---:|" * len(METRIC_KEYS)]
    for ct in contenders:
        n_seeds = len({r.seed for r in rows if r.contender == ct})
        cells = []
        for k in METRIC_KEYS:
            m, sd, _ = stats[ct][k]
            if m is None:
                cells.append("n/a")
                continue
            d = DECIMALS[k]
            txt = f"{m:.{d}f}" + (f" ± {sd:.{d}f}" if sd is not None and n_seeds > 1 else "")
            cells.append(f"**{txt}**" if k in best and round(m, d) == best[k] else txt)
        lines.append(f"| {ct} | {n_seeds} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def by_scenario(rows: list[EvalRow]) -> dict[str, list[EvalRow]]:
    """Rows grouped by scenario id, in first-seen order."""
    out: dict[str, list[EvalRow]] = {}
    for r in rows:
        out.setdefault(r.scenario_id, []).append(r)
    return out


def render(rows: list[EvalRow]) -> str:
    """The full report: one table per scenario, plus an overall table when there are several."""
    groups = by_scenario(rows)
    if not groups:
        return "(no eval rows)"
    parts = []
    for sid, rs in groups.items():
        parts.append(f"### {sid} (profile {rs[0].profile_id})\n\n{table(rs)}")
    if len(groups) > 1:
        parts.append(f"### all scenarios (mean over {len(groups)} scenarios x seeds)\n\n{table(rows)}")
    return "\n\n".join(parts)


def gate_summary(rows_v0: list[EvalRow], rows_v1: list[EvalRow]) -> dict:
    """Spec 13.2 GATE on the test set: promote the candidate only if the mean test score
    improves AND violations (mean per run) do not increase."""
    def avg(rows: list[EvalRow], key: str) -> float:
        vals = [float(r.metrics[key]) for r in rows if r.metrics.get(key) is not None]
        return mean(vals) if vals else 0.0

    s0, s1 = avg(rows_v0, "score"), avg(rows_v1, "score")
    v0, v1 = avg(rows_v0, "violations"), avg(rows_v1, "violations")
    return dict(mean_score_v0=s0, mean_score_v1=s1, violations_v0=v0, violations_v1=v1,
                improved=bool(rows_v0 and rows_v1 and s1 > s0 and v1 <= v0))
