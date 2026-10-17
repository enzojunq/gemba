"""Playbook versions (spec 13.2, 10.7): load/save `playbook_rules`, merge accepted lessons into
a candidate version, and the tag vocabulary rules are selected by.

A rule is a plain dict (what `org.playbook_select.select_rules` and `OrgPolicy(playbook_rules=)`
consume):

    {"rule_id", "role", "tags": [..], "trigger", "advice",
     "evidence": {"run_id", "day", "fork_run_id", "score_delta"}, "score_delta", "status"}

Status lifecycle: `candidate` (written by learn.retro under version+1) -> `active` (promoted by
learn.gate) or `rejected` (gate failed). "v0" is the empty playbook.

Tag vocabulary (must match what `org/standup.py` puts in `OrgPolicy._tags` each morning):
- event kinds of today's and newly announced events (`contracts.EVENT_KINDS`);
- KPI violation names exactly as `OrgPolicy._violations` emits them: the bare KPI names
  `on_time_pct`, `mispick_rate`, `pm_peak_zone_load`, `safety_risk_max`, `stockout_lines`
  (NOT "mispick_rate_high" / "on_time_low"; coach tags like those are mapped by `normalize_tags`);
- drift flag kinds (`repeat_whatif`, `plan_churn`, `stall`, `prediction_error`, `overrun`).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

import contracts as c

MAX_RULES = 12
TAG_OVERLAP_MIN = 0.5
ADVICE_JACCARD_MIN = 0.6

VIOLATION_TAGS: tuple[str, ...] = ("on_time_pct", "mispick_rate", "pm_peak_zone_load", "safety_risk_max",
                                   "stockout_lines")
DRIFT_TAGS: tuple[str, ...] = ("repeat_whatif", "plan_churn", "stall", "prediction_error", "overrun")
TAG_VOCABULARY: frozenset[str] = frozenset(c.EVENT_KINDS) | frozenset(VIOLATION_TAGS) | frozenset(DRIFT_TAGS)

_ALIASES = {
    "on_time": "on_time_pct", "on_time_low": "on_time_pct", "late_orders": "on_time_pct",
    "mispick": "mispick_rate", "mispicks": "mispick_rate", "mispick_rate_high": "mispick_rate",
    "mispick_rate_by_customer": "mispick_rate", "key_customer_mispick": "mispick_rate",
    "stockout": "stockout_lines", "stockouts": "stockout_lines",
    "safety": "safety_risk_max", "safety_risk": "safety_risk_max",
    "pm_zone_load": "pm_peak_zone_load", "zone_overload": "pm_peak_zone_load",
}
_SUFFIXES = ("_high", "_low", "_breach", "_violation", "_drop", "_spike")


def next_version(version: str) -> str:
    """"v0" -> "v1"."""
    m = re.fullmatch(r"v(\d+)", version or "")
    if not m:
        raise ValueError(f"playbook version must look like v<N>, got {version!r}")
    return f"v{int(m.group(1)) + 1}"


# --------------------------------------------------------------------------- tags


def tags_for_day(events_today_kinds: Iterable[str], kpi_violations: Iterable[str],
                 drift_kinds: Iterable[str] = ()) -> set[str]:
    """The tags a morning carries: event kinds + KPI violation names (+ drift flag kinds),
    the same set `OrgPolicy.decide` builds for `select_rules`."""
    return set(events_today_kinds) | set(kpi_violations) | set(drift_kinds)


def normalize_tags(tags: Iterable[str]) -> list[str]:
    """Map coach tags onto the vocabulary ("mispick_rate_high" -> "mispick_rate"). Unknown tags
    are dropped; if none survive, the raw (lower-cased) tags are kept so nothing is lost."""
    raw = [str(t).strip().lower().replace("-", "_").replace(" ", "_") for t in tags if str(t).strip()]
    out: list[str] = []
    for t in raw:
        cand = _ALIASES.get(t, t)
        if cand not in TAG_VOCABULARY:
            for suf in _SUFFIXES:
                if cand.endswith(suf):
                    base = cand[: -len(suf)]
                    cand = _ALIASES.get(base, base)
                    break
        if cand in TAG_VOCABULARY and cand not in out:
            out.append(cand)
    return out or list(dict.fromkeys(raw))


# --------------------------------------------------------------------------- store


def _rule_from_row(r: dict) -> dict:
    out = {k: r.get(k) for k in ("rule_id", "role", "trigger", "advice", "score_delta", "status")}
    out["tags"] = list(r.get("tags") or [])
    out["evidence"] = r.get("evidence") or {}
    return out


def load_rules(store, version: str | None, statuses: Iterable[str] | None = None) -> list[dict]:
    """Rules of `version` with status "active" ("v0" -> []).

    A version still under the gate has no active rows yet; it is then loaded with its
    "candidate" rows (so `crew_v1` can be evaluated before promotion). A rejected version
    loads []. Pass `statuses` to choose explicitly."""
    if version in ("v0", "", None):
        return []
    rows = [_rule_from_row(r) for r in store.playbook_rows(version)]
    if statuses is not None:
        keep = set(statuses)
        return [r for r in rows if r["status"] in keep]
    active = [r for r in rows if r["status"] == "active"]
    return active or [r for r in rows if r["status"] == "candidate"]


def save_rules(store, version: str, rules: list[dict]) -> None:
    """Replace the rows of `version` with `rules` (each keeps its own status, default candidate)."""
    store.replace_playbook_rules(version, [
        {"rule_id": r["rule_id"], "role": r["role"], "tags": list(r.get("tags") or []),
         "trigger": r.get("trigger", ""), "advice": r["advice"], "evidence": r.get("evidence") or {},
         "score_delta": float(r.get("score_delta") or 0.0), "status": r.get("status") or "candidate"}
        for r in rules])


# --------------------------------------------------------------------------- lessons -> rules


def rule_id_for(role: str, advice: str) -> str:
    return "r-" + hashlib.sha1(f"{role}|{' '.join(_tokens(advice))}".encode()).hexdigest()[:8]


def lesson_rule(lesson: c.Lesson | dict, evidence: dict | None = None, status: str = "candidate") -> dict:
    """A `contracts.Lesson` (+ fork evidence) as a rule dict."""
    lz = lesson.model_dump() if hasattr(lesson, "model_dump") else dict(lesson)
    ev = dict(evidence or {})
    return {"rule_id": rule_id_for(lz["role"], lz["advice"]), "role": lz["role"],
            "tags": normalize_tags(lz.get("tags") or []), "trigger": lz.get("trigger", ""),
            "advice": lz["advice"], "evidence": ev, "score_delta": float(ev.get("score_delta") or 0.0),
            "status": status}


def _as_rule(x: Any) -> dict:
    if isinstance(x, dict) and "advice" in x and "rule_id" in x:
        return dict(x)
    if hasattr(x, "rule") and callable(x.rule):  # learn.retro.AcceptedLesson
        return x.rule()
    if isinstance(x, (c.Lesson, dict)):
        return lesson_rule(x)
    raise TypeError(f"cannot turn {type(x).__name__} into a playbook rule")


# --------------------------------------------------------------------------- merge


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", (text or "").lower())


def jaccard(a: str, b: str) -> float:
    """Normalized-token Jaccard similarity of two advice strings."""
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta and not tb:
        return 1.0
    return len(ta & tb) / len(ta | tb)


def tag_overlap(a: Iterable[str] | None, b: Iterable[str] | None) -> float:
    """|A & B| / min(|A|, |B|): 1.0 when one tag set contains the other."""
    sa, sb = set(a or []), set(b or [])
    if not sa or not sb:
        return 1.0 if sa == sb else 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def is_duplicate(r1: dict, r2: dict) -> bool:
    """Same role, >= 50% tag overlap and advice token Jaccard >= 0.6."""
    return (r1.get("role") == r2.get("role")
            and tag_overlap(r1.get("tags"), r2.get("tags")) >= TAG_OVERLAP_MIN
            and jaccard(r1.get("advice", ""), r2.get("advice", "")) >= ADVICE_JACCARD_MIN)


def merge(existing: list[dict], accepted_lessons: list, fired_count: dict[str, int] | None = None,
          cap: int = MAX_RULES) -> list[dict]:
    """candidate = existing rules + accepted lessons (spec 13.2).

    - `fired_count` (rule_id -> times injected during this train cycle, see `fired_counts`):
      when given, existing rules that never fired (0 or missing) are dropped. Rules new in this
      cycle are never dropped. Without it nothing is dropped.
    - Duplicates (`is_duplicate`) merge, keeping the larger score_delta (ties: the older rule).
    - Cap at `cap` rules, keeping the largest score_delta (ties by rule_id).
    Every returned rule has status "candidate"."""
    old = [dict(r) for r in existing]
    if fired_count is not None:
        old = [r for r in old if fired_count.get(r["rule_id"], 0) > 0]
    new = [_as_rule(x) for x in accepted_lessons]
    kept: list[dict] = []
    for r in old + new:
        dup = next((i for i, k in enumerate(kept) if is_duplicate(k, r)), None)
        if dup is None:
            kept.append(r)
        elif float(r.get("score_delta") or 0) > float(kept[dup].get("score_delta") or 0):
            kept[dup] = r
    kept.sort(key=lambda r: (-float(r.get("score_delta") or 0), str(r["rule_id"])))
    return [dict(r, status="candidate") for r in kept[:cap]]


# --------------------------------------------------------------------------- fired counts


def fired_counts(store, run_ids: Iterable[str], rules: list[dict]) -> dict[str, int]:
    """How many logged LLM calls of `run_ids` carried each rule's advice in their
    "Playbook rules for your role" block (i.e. `select_rules` injected it)."""
    counts = {r["rule_id"]: 0 for r in rules}
    for rid in run_ids:
        for call in store.table("llm_calls", rid):
            req = call.get("request") or {}
            msgs = req.get("messages") if isinstance(req, dict) else None
            text = " ".join(str(m.get("content", "")) for m in (msgs or []) if isinstance(m, dict))
            if "## Playbook rules" not in text:
                continue
            block = text.split("## Playbook rules", 1)[1].split("## Board", 1)[0]
            for r in rules:
                if f"- {r['advice']}" in block:
                    counts[r["rule_id"]] += 1
    return counts
