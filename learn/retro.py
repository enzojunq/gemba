"""Retro Coach (spec 13.2): find regret days in finished train runs, trace them back to the
originating decision, ask the coach for an alternative + a general Lesson, and VERIFY it with
a fork. Accepted lessons become "candidate" `playbook_rules` rows under version + 1.

    retro(train_run_ids, store=, llm=, twin=, profile=, version="v0", max_regret_days=3, horizon=None)

Regret days. Each day is scored with `score_kpis(day_kpis, do_nothing_day_kpis, profile)`
against the do-nothing REF run for the same scenario + seed (found in `runs`, or run now with
`make_policy("do_nothing")` in mode "retro"). A day's drop = previous day's score - today's
(the first day is compared with 0, the REF level). Candidates are days with a drop > 0 or at
least one failed daily check; they rank by `drop + 0.25 * failed_daily_checks` and the top
`max_regret_days` are kept.

Tracing. The worst KPI is the scorecard term that fell the most. Starting nodes, in order:
`kpi:<run>:<day>:<kpi>` for every KPI (worst first), then the consequence events logged for
days day+1 .. day+2 (the twin writes C1-C7 events the day after the KPIs that caused them).
The originating decision is a `dec:` node in their cascade with day <= the regret day (direct
causes first, then the latest). If the cascade has none (common: the engine writes no edges
into KPI nodes), the latest decision with levers before the regret day is used, and the trace
is marked `via="fallback"`.

Verification. `fork_run(parent, day=<originating decision's day>, levers=alternative,
policy_name=<the parent's policy>, horizon=horizon)`. Accept iff
`score(fork window) - score(parent window) > 0` (both aggregated over the same days with
`aggregate_run`, against the REF over those days) AND the fork has no more failed hard-limit
checks (`daily.safety_risk_max`, `daily.pm_peak_zone_load`, `daily.expected_near_misses`) in
the window than the parent. One fork per originating decision: regret days that trace to an
already-handled decision are skipped (saves coach calls and forks).

CLI:
    python -m learn.retro --train train_1,train_2,train_3 --version v0 [--horizon 5] [--max-regret-days 2]
`--train` takes scenario ids (an org run with playbook --version is made for each) or existing
run ids. `--playbook` is an alias of `--version` (Makefile `make retro`).
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

import contracts as c
from engine import events as ev
from engine import snapshot
from engine.fork import fork_run
from engine.run import make_policy, make_twin, run
from engine.store import RunStore
from learn import playbook as pb
from llm.prompt import build_messages
from org.roles import meta_prompt
from org.scoring import Score, aggregate_run, load_profile, score_kpis

log = logging.getLogger(__name__)

COACH_ROLE = "retro_coach"
COACH_MAX_TOKENS = 2000
HARD_LIMIT_CHECKS: tuple[str, ...] = ("daily.safety_risk_max", "daily.pm_peak_zone_load",
                                      "daily.expected_near_misses")
FAILED_CHECK_WEIGHT = 0.25
SHOWN_KPIS: tuple[str, ...] = ("on_time_pct", "mispick_rate", "lph", "labor_cost_usd", "reslot_progress_pct",
                               "moves_done", "overtime_hours", "staging_occupancy_max", "safety_risk_max",
                               "pm_peak_zone_load", "near_misses")


# --------------------------------------------------------------------------- schemas / results


class CoachProposal(BaseModel):
    alternative_levers: list[c.Lever]
    lesson: c.Lesson
    why: str


@dataclass
class RegretDay:
    run_id: str
    day: int
    score: float
    prev_score: float
    drop: float
    failed_checks: list[str]
    kpi_order: list[str]  # scorecard KPIs, the one that fell most first

    @property
    def worst_kpi(self) -> str:
        return self.kpi_order[0] if self.kpi_order else "on_time_pct"


@dataclass
class Trace:
    decision: dict  # the `decisions` row
    decision_ids: list[str]  # every dec: node found (originating first)
    chain: str  # "dec:x -> evt:y -> ..."
    via: str  # "cascade" | "fallback"


@dataclass
class AcceptedLesson:
    """A coach lesson and the fork that tested it (`accepted` says whether it passed)."""
    lesson: c.Lesson
    alternative_levers: list[c.Lever]
    why: str
    run_id: str
    regret_day: int
    decision_id: str
    day: int  # fork day = the originating decision's day
    fork_run_id: str
    score_delta: float
    parent_score: float
    fork_score: float
    breaches_parent: int
    breaches_fork: int
    accepted: bool
    fallback_used: bool = False
    chain: str = ""

    @property
    def evidence(self) -> dict:
        return {"run_id": self.run_id, "day": self.day, "fork_run_id": self.fork_run_id,
                "score_delta": round(self.score_delta, 6), "decision_id": self.decision_id,
                "regret_day": self.regret_day}

    def rule(self) -> dict:
        return pb.lesson_rule(self.lesson, self.evidence)


@dataclass
class RetroResult:
    accepted: list[AcceptedLesson] = field(default_factory=list)
    rejected: list[AcceptedLesson] = field(default_factory=list)
    candidate_version: str = ""
    candidate_rules: list[dict] = field(default_factory=list)
    coach_calls: int = 0
    forks: int = 0


# --------------------------------------------------------------------------- helpers


def _fresh(twin: Any) -> Any:
    """A per-run twin (twins hold per-run state). Factories/classes are called."""
    if twin is None or isinstance(twin, str):
        return make_twin(twin)
    if isinstance(twin, type) or (callable(twin) and not isinstance(twin, c.FloorTwin)):
        return twin()
    return copy.deepcopy(twin)


def _day_kpis(store: RunStore, run_id: str) -> dict[int, c.Kpis]:
    return {r["day"]: c.Kpis.model_validate(r["kpis"]) for r in store.day_results(run_id)}


def run_scenario(store: RunStore, run_id: str) -> c.Scenario:
    """The scenario a run actually used (its first snapshot holds it, trimmed days included)."""
    rows = store.day_results(run_id)
    for r in rows[:1]:
        try:
            p = r.get("snapshot_path") or str(snapshot.path_for(run_id, r["day"]))
            return snapshot.load(p)["scenario"]
        except Exception:  # noqa: BLE001 - fall back to the YAML
            pass
    meta = store.get_run(run_id) or {}
    sc = ev.load_scenario(meta["scenario_id"])
    upd: dict[str, Any] = {}
    if meta.get("seed") is not None:
        upd["seed"] = meta["seed"]
    if rows:
        upd["days"] = (rows[0]["day"], rows[-1]["day"])
    return c.Scenario.model_validate({**sc.model_dump(), **upd}) if upd else sc


def run_policy_name(store: RunStore, run_id: str) -> str:
    rows = store.day_results(run_id)
    for r in rows[:1]:
        try:
            return snapshot.load(r.get("snapshot_path") or str(snapshot.path_for(run_id, r["day"])))["policy_name"]
        except Exception:  # noqa: BLE001
            pass
    decs = store.decisions(run_id)
    return decs[0]["decided_by"] if decs else "org"


def find_ref_run(store: RunStore, run_id: str, *, twin: Any = None, profile: c.Profile | None = None) -> str:
    """The do-nothing REF run for the same scenario + seed covering every day of `run_id`; run
    it now (mode "retro") if none exists."""
    meta = store.get_run(run_id)
    if meta is None:
        raise KeyError(f"unknown run {run_id}")
    need = {r["day"] for r in store.day_results(run_id)}
    for r in store.list_runs():
        if (r["run_id"] == run_id or r.get("parent_run_id") or r.get("status") != "done"
                or r.get("scenario_id") != meta["scenario_id"] or r.get("seed") != meta["seed"]):
            continue
        decs = store.decisions(r["run_id"])
        if decs and all(d["decided_by"] == "do_nothing" for d in decs) \
                and need <= {d["day"] for d in decs}:
            return r["run_id"]
    sc = run_scenario(store, run_id)
    prof = profile or load_profile(meta["profile_id"])
    return run(sc, prof, make_policy("do_nothing", ""), _fresh(twin), mode="retro", store=store,
               playbook_version="v0")


def window_score(kpis: dict[int, c.Kpis], ref: dict[int, c.Kpis], days: list[int], profile: c.Profile) -> float:
    ds = [d for d in days if d in kpis and d in ref]
    if not ds:
        return 0.0
    return score_kpis(aggregate_run([kpis[d] for d in ds]), aggregate_run([ref[d] for d in ds]), profile).total


def hard_breaches(store: RunStore, run_id: str, days: list[int]) -> int:
    ds = set(days)
    return sum(1 for r in store.table("checks", run_id)
               if r.get("day") in ds and r.get("check_name") in HARD_LIMIT_CHECKS
               and r.get("passed") is not None and not r.get("passed"))


# --------------------------------------------------------------------------- 1. regret days


def regret_days(store: RunStore, run_id: str, ref_run_id: str, profile: c.Profile,
                max_regret_days: int = 3) -> list[RegretDay]:
    kpis, ref = _day_kpis(store, run_id), _day_kpis(store, ref_run_id)
    failed: dict[int, list[str]] = {}
    for r in store.table("checks", run_id):
        if r.get("scope") == "daily" and r.get("passed") is not None and not r.get("passed"):
            failed.setdefault(r["day"], []).append(r["check_name"])
    out: list[RegretDay] = []
    prev: Score | None = None
    for d in sorted(kpis):
        if d not in ref:
            continue
        s = score_kpis(kpis[d], ref[d], profile)
        p_total = prev.total if prev else 0.0
        p_terms = prev.terms if prev else {k: 0.0 for k in s.terms}
        order = sorted(s.terms, key=lambda k: (s.terms[k] - p_terms.get(k, 0.0), k))
        out.append(RegretDay(run_id, d, s.total, p_total, p_total - s.total, sorted(failed.get(d, [])), order))
        prev = s
    cands = [r for r in out if r.drop > 1e-9 or r.failed_checks]
    cands.sort(key=lambda r: (-(max(r.drop, 0.0) + FAILED_CHECK_WEIGHT * len(r.failed_checks)), r.day))
    return cands[:max_regret_days]


# --------------------------------------------------------------------------- 2. trace


def _path(edges: list[dict], src: str, dst: str) -> list[str] | None:
    nxt: dict[str, list[str]] = {}
    for e in edges:
        nxt.setdefault(e["from_id"], []).append(e["to_id"])
    q, prev = deque([src]), {src: None}
    while q:
        n = q.popleft()
        if n == dst:
            path = [n]
            while prev[path[-1]] is not None:
                path.append(prev[path[-1]])
            return path[::-1]
        for m in nxt.get(n, []):
            if m not in prev:
                prev[m] = n
                q.append(m)
    return None


def trace(store: RunStore, regret: RegretDay) -> Trace | None:
    rid, day = regret.run_id, regret.day
    decs = {d["decision_id"]: d for d in store.decisions(rid)}
    starts = [c.kpi_node_id(rid, day, k) for k in [*regret.kpi_order, "mispick_rate_by_customer"]]
    starts += [c.node_id(c.EVT, e["event_id"]) for e in store.events(rid)
               if e.get("source") == "consequence" and day + 1 <= (e.get("day") or 0) <= day + 2]
    for start in starts:
        edges = store.cascade(rid, start)
        if not edges:
            continue
        nodes = {e["from_id"] for e in edges} | {e["to_id"] for e in edges}
        direct = {e["from_id"] for e in edges if e["to_id"] == start}
        found = [n for n in nodes if n.startswith(c.DEC) and n[len(c.DEC):] in decs
                 and decs[n[len(c.DEC):]]["day"] <= day]
        if not found:
            continue
        found.sort(key=lambda n: (n not in direct, -decs[n[len(c.DEC):]]["day"], n))
        path = _path(edges, found[0], start) or [found[0], start]
        return Trace(decs[found[0][len(c.DEC):]], [n[len(c.DEC):] for n in found], " -> ".join(path), "cascade")
    with_levers = [d for d in decs.values() if d["day"] < day and d.get("levers")]
    pick = (max(with_levers, key=lambda d: d["day"]) if with_levers
            else next((d for d in decs.values() if d["day"] == day), None))
    if pick is None:
        return None
    return Trace(pick, [pick["decision_id"]],
                 f"{c.DEC}{pick['decision_id']} -> {c.kpi_node_id(rid, day, regret.worst_kpi)} (no cascade edge)",
                 "fallback")


# --------------------------------------------------------------------------- 3. coach


def _lever_text(levers: list) -> str:
    out = []
    for lv in levers or []:
        d = dict(lv) if isinstance(lv, dict) else lv.model_dump(mode="json")
        d.pop("decision_id", None)
        out.append(json.dumps(d, sort_keys=True))
    return "[" + ", ".join(out) + "]" if out else "[] (no levers)"


def run_log(store: RunStore, regret: RegretDay, tr: Trace) -> str:
    """The compact log the coach reads (board slot of build_messages)."""
    rid, day = regret.run_id, regret.day
    k = _day_kpis(store, rid)
    meta = store.get_run(rid) or {}
    lines = [f"Run {rid} (scenario {meta.get('scenario_id')}, profile {meta.get('profile_id')}, "
             f"playbook {meta.get('playbook_version')}).",
             f"Regret day {day}: day score {regret.score:+.3f} vs {regret.prev_score:+.3f} the day before "
             f"(drop {regret.drop:+.3f}); worst KPI {regret.worst_kpi}; failed daily checks: "
             f"{', '.join(regret.failed_checks) or 'none'}.", "", f"KPIs day {day} (day {day - 1}):"]
    for name in SHOWN_KPIS:
        today, yday = getattr(k.get(day), name, None), getattr(k.get(day - 1), name, None)
        if today is not None:
            lines.append(f"- {name}: {today:g}" + (f" ({yday:g})" if yday is not None else ""))
    d = tr.decision
    lines += ["", f"Originating decision {c.DEC}{d['decision_id']} on day {d['day']} by {d['decided_by']} "
                  f"(traced via {tr.via}):",
              f"- levers: {_lever_text(d.get('levers'))}", f"- rationale: {d.get('rationale') or ''}"]
    if len(tr.decision_ids) > 1:
        lines.append(f"- other decisions in the cascade: {', '.join(tr.decision_ids[1:6])}")
    mtg = (d.get("caused_by") or "")
    if mtg.startswith(c.MTG):
        mid = mtg[len(c.MTG):]
        m = next((x for x in store.meetings(rid) if x["meeting_id"] == mid), None)
        if m:
            lines.append(f"Meeting {mtg} agenda: {m.get('agenda')}")
        utts = [u for u in store.table("utterances", rid) if u["meeting_id"] == mid
                and u.get("kind") in ("position", "veto", "lock", "proposal")]
        if utts:
            lines.append("Positions:")
            lines += [f"- {u['role']} ({u['kind']}): {(u.get('text') or '')[:240]}"
                      for u in sorted(utts, key=lambda u: u["seq"])[:10]]
    evs = [e for e in store.events(rid) if d["day"] - 1 <= (e.get("day") or 0) <= day + 1]
    if evs:
        lines.append("Events around the decision and the regret day:")
        lines += [f"- {c.EVT}{e['event_id']}: {e['kind']} day {e['day']} {e.get('args') or {}} ({e['source']})"
                  for e in evs[:12]]
    lines += ["", f"Cascade: {tr.chain}"]
    return "\n".join(lines)


def coach_task(regret: RegretDay, tr: Trace) -> str:
    from org.standup import LEVER_HELP
    fmt = "\n".join(f"  {v}" for v in LEVER_HELP.values())
    vocab = ", ".join(sorted(pb.TAG_VOCABULARY))
    return (f"Propose the alternative decision for day {tr.decision['day']} (it replaces decision "
            f"{tr.decision['decision_id']}; a fork from that day will verify it) and one general Lesson that "
            f"would have prevented the regret on day {regret.day}.\n"
            f"Lever formats (omit decision_id):\n{fmt}\n"
            f"Lesson tags must come from: {vocab}.\n"
            'Reply JSON {"alternative_levers": [<lever>, ...], "lesson": {"role": "<role id>", "tags": [...], '
            '"trigger": "...", "advice": "<= 40 words"}, "why": "<= 60 words"}.')


def fallback_proposal(regret: RegretDay, tr: Trace) -> CoachProposal:
    try:
        from org.roles import chair, load_roles
        role = chair(load_roles()).id
    except Exception:  # noqa: BLE001
        role = "ops_manager"
    tag = pb.normalize_tags([regret.worst_kpi])
    return CoachProposal(
        alternative_levers=[c.PauseReslot(days=1)],
        lesson=c.Lesson(role=role, tags=tag, trigger=f"{regret.worst_kpi} falls after a night of moves",
                        advice="When a KPI falls right after a night of moves, pause reslotting for a night "
                               "and let the floor recover before moving more."),
        why="coach fallback: pause one night")


def coach(llm: Any, store: RunStore, regret: RegretDay, tr: Trace) -> tuple[CoachProposal, bool]:
    from org.standup import tier_for
    msgs = build_messages(meta_prompt(COACH_ROLE), [], run_log(store, regret, tr), coach_task(regret, tr))
    fb = fallback_proposal(regret, tr)
    out, meta = llm.complete(tier_for("ultra"), msgs, CoachProposal, role=COACH_ROLE, run_id=regret.run_id,
                             reasoning=True, max_tokens=COACH_MAX_TOKENS, fallback=fb)
    used = bool(getattr(meta, "fallback_used", False)) or out is fb or not isinstance(out, CoachProposal)
    if used:
        log.warning("retro coach fell back for %s day %s", regret.run_id, regret.day)
        try:
            store.write_llm_call(call_id=uuid.uuid4().hex, run_id=regret.run_id, day=regret.day, role=COACH_ROLE,
                                 model="(code)", request={"reason": "coach fallback"},
                                 response={"llm_fallback": True}, usd=0.0, kind="llm_fallback",
                                 fallback_used=True)
        except Exception:  # noqa: BLE001
            log.exception("could not log coach fallback")
        return fb, True
    return out, False


# --------------------------------------------------------------------------- 4. verify


def verify(store: RunStore, regret: RegretDay, tr: Trace, proposal: CoachProposal, *, ref_run_id: str,
           twin: Any, profile: c.Profile, policy_name: str, horizon: int | None,
           policy_kwargs: dict | None) -> AcceptedLesson:
    rid, fday = regret.run_id, tr.decision["day"]
    levers = [lv.model_copy(update={"decision_id": ""}) for lv in proposal.alternative_levers]
    child = fork_run(rid, fday, levers, twin=_fresh(twin), profile=profile, policy_name=policy_name, store=store,
                     horizon=horizon, policy_kwargs=policy_kwargs)
    pk, fk, ref = _day_kpis(store, rid), _day_kpis(store, child), _day_kpis(store, ref_run_id)
    days = sorted(d for d in fk if d >= fday and d in pk)
    ps, fs = window_score(pk, ref, days, profile), window_score(fk, ref, days, profile)
    bp, bf = hard_breaches(store, rid, days), hard_breaches(store, child, days)
    delta = fs - ps
    return AcceptedLesson(lesson=proposal.lesson, alternative_levers=levers, why=proposal.why, run_id=rid,
                          regret_day=regret.day, decision_id=tr.decision["decision_id"], day=fday,
                          fork_run_id=child, score_delta=delta, parent_score=ps, fork_score=fs,
                          breaches_parent=bp, breaches_fork=bf, accepted=bool(delta > 0 and bf <= bp),
                          chain=tr.chain)


# --------------------------------------------------------------------------- 5. the loop


def retro_full(train_run_ids: list[str], *, store: RunStore, llm: Any, twin: Any, profile: c.Profile | str,
               version: str = "v0", max_regret_days: int = 3, horizon: int | None = None,
               policy_name: str | None = None, policy_kwargs: dict | None = None,
               write: bool = True) -> RetroResult:
    """`retro` with the rejected verdicts and the candidate rules too.

    `policy_name`: the verification forks' policy (default: the parent's, i.e. "org" for org
    runs). For "org", `policy_kwargs` defaults to the coach's `llm`, this `store`, `profile`
    and the rules of `version`."""
    prof = load_profile(profile) if isinstance(profile, str) else profile
    if getattr(llm, "on_call", "absent") is None:  # a real client without a sink: log to llm_calls
        llm.on_call = lambda row: store.write_llm_call(row)
    res = RetroResult(candidate_version=pb.next_version(version))
    rules_v = pb.load_rules(store, version)
    for rid in train_run_ids:
        ref_id = find_ref_run(store, rid, twin=twin, profile=prof)
        pname = policy_name or run_policy_name(store, rid)
        pkw = policy_kwargs
        if pkw is None and pname == "org":
            pkw = {"llm": llm, "store": store, "profile": prof, "playbook_rules": rules_v}
        handled: set[str] = set()
        for rd in regret_days(store, rid, ref_id, prof, max_regret_days):
            tr = trace(store, rd)
            if tr is None or tr.decision["decision_id"] in handled:
                continue
            handled.add(tr.decision["decision_id"])
            proposal, used = coach(llm, store, rd, tr)
            res.coach_calls += 1
            verdict = verify(store, rd, tr, proposal, ref_run_id=ref_id, twin=twin, profile=prof,
                             policy_name=pname, horizon=horizon, policy_kwargs=pkw)
            verdict.fallback_used = used
            res.forks += 1
            (res.accepted if verdict.accepted else res.rejected).append(verdict)
            log.info("retro %s day %s: fork %s delta %+.4f breaches %s/%s -> %s", rid, rd.day,
                     verdict.fork_run_id, verdict.score_delta, verdict.breaches_fork, verdict.breaches_parent,
                     "accepted" if verdict.accepted else "rejected")
    fired = pb.fired_counts(store, train_run_ids, rules_v) if rules_v else None
    res.candidate_rules = pb.merge(rules_v, res.accepted, fired)
    if write:
        pb.save_rules(store, res.candidate_version, res.candidate_rules)
    return res


def retro(train_run_ids: list[str], *, store: RunStore, llm: Any, twin: Any, profile: c.Profile | str,
          version: str = "v0", max_regret_days: int = 3, horizon: int | None = None,
          policy_name: str | None = None, policy_kwargs: dict | None = None) -> list[AcceptedLesson]:
    """Spec 13.2 loop body; returns the accepted lessons (with evidence) and writes the
    candidate playbook (status "candidate") under version + 1."""
    return retro_full(train_run_ids, store=store, llm=llm, twin=twin, profile=profile, version=version,
                      max_regret_days=max_regret_days, horizon=horizon, policy_name=policy_name,
                      policy_kwargs=policy_kwargs).accepted


# --------------------------------------------------------------------------- CLI


def _csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m learn.retro", description="Retro Coach (spec 13.2)")
    ap.add_argument("--train", default="train_1,train_2,train_3", help="scenario ids or run ids")
    ap.add_argument("--version", "--playbook", dest="version", default="v0")
    ap.add_argument("--profile", default="customer_first")
    ap.add_argument("--horizon", type=int, default=None)
    ap.add_argument("--max-regret-days", type=int, default=3)
    ap.add_argument("--policy", default="org", help="policy for the train runs and forks")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--twin", default=None)
    ap.add_argument("--store", default=None)
    a = ap.parse_args(argv)

    store = RunStore(a.store)
    store.create_schema()
    llm = None
    if a.policy == "org":
        from llm.client import LLM
        llm = LLM(on_call=lambda row: store.write_llm_call(row))
    run_ids = []
    for item in _csv(a.train):
        if store.get_run(item) is not None:
            run_ids.append(item)
            continue
        from eval.run_eval import run_contender
        contender = a.policy if a.policy != "org" else ("crew" if a.version == "v0" else f"crew_{a.version}")
        rid = run_contender(contender, item, a.profile, a.seed, twin=a.twin, store=store, playbook=a.version,
                            llm=llm)
        print(f"train run {item}: {rid}")
        run_ids.append(rid)
    if llm is None:
        from llm.client import LLM
        llm = LLM(on_call=lambda row: store.write_llm_call(row))
    res = retro_full(run_ids, store=store, llm=llm, twin=a.twin, profile=a.profile, version=a.version,
                     max_regret_days=a.max_regret_days, horizon=a.horizon)
    for v in res.accepted + res.rejected:
        print(f"{'ACCEPT' if v.accepted else 'reject'} {v.run_id} day {v.day} fork {v.fork_run_id} "
              f"delta {v.score_delta:+.4f}  [{v.lesson.role}] {v.lesson.advice}")
    print(f"{len(res.accepted)} accepted / {len(res.rejected)} rejected; {len(res.candidate_rules)} candidate "
          f"rules written under {res.candidate_version}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    raise SystemExit(main())
