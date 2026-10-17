"""Playbook rules for one role (spec 10.7): rules whose role matches and whose tags intersect
today's tags, at most 5, in a deterministic order (most tag overlap first, then rule_id).

No embeddings, no retrieval model: plain tag overlap, so the choice is auditable.
"""
from __future__ import annotations

INACTIVE_STATUSES = frozenset({"retired", "rejected"})


def select_rules(rules: list[dict], role_id: str, tags: set[str], max_n: int = 5) -> list[str]:
    """Advice strings for `role_id`. A rule is a dict with `role`, `tags`, `advice` and
    optionally `rule_id` and `status` (retired/rejected rules are skipped)."""
    tags = set(tags)
    picked: list[tuple[int, str, str]] = []
    for r in rules:
        if r.get("role") != role_id or r.get("status") in INACTIVE_STATUSES:
            continue
        overlap = len(set(r.get("tags") or []) & tags)
        if overlap and r.get("advice"):
            picked.append((-overlap, str(r.get("rule_id", "")), str(r["advice"])))
    picked.sort()
    return [advice for _, _, advice in picked[:max_n]]
