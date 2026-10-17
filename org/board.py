"""The Shared Brain (spec 10.7): the per-day board every org LLM call sees, plus stance memory.

`Board` is plain data built by the standup from the floor view, yesterday's KPIs, visible
events, constraints, evidence, inbox, attendees, drift flags and hires. `snapshot_text`
renders it as compact Markdown under ~1,500 tokens (approx. 4 chars per token): if the full
render is too long, the event and evidence lists are cut first, then inbox texts, then the
text is hard-truncated.

Stance memory (each role's last 3 positions) is rendered into that role's task section by
the standup, never into the board, so the board stays identical for every role on a day.
"""
from __future__ import annotations

import re

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from contracts import FloorView, Kpis

MAX_TOKENS = 1500
CHARS_PER_TOKEN = 4
MAX_CHARS = MAX_TOKENS * CHARS_PER_TOKEN
BOARD_EVIDENCE = 5
STANCE_MEMORY = 3


@dataclass(frozen=True)
class EvidenceRow:
    """One what-if summary (3-day kpis_total) as the board shows it."""
    evidence_id: str
    option_label: str
    on_time: float
    mispick: float
    lph: float
    progress: float
    cost: float
    near_misses: float

    @classmethod
    def from_kpis(cls, evidence_id: str, label: str, k: Kpis, expected_near_misses: float | None = None) -> "EvidenceRow":
        nm = k.near_misses if expected_near_misses is None else expected_near_misses
        return cls(evidence_id, label, k.on_time_pct, k.mispick_rate, k.lph, k.reslot_progress_pct,
                   k.labor_cost_usd, nm)

    def line(self) -> str:
        return (f"- `{self.evidence_id}` {self.option_label}: on_time {self.on_time:.1f}%, "
                f"mispick {self.mispick:.2f}%, lph {self.lph:.1f}, progress {self.progress:.1f}%, "
                f"cost ${self.cost:,.0f}, exp near-misses {self.near_misses:.2f}")


@dataclass
class Board:
    day: int
    view: FloorView
    yesterday: Kpis | None = None
    predicted: Kpis | None = None
    targets: dict[str, float] = field(default_factory=dict)
    hard_limits: dict[str, float] = field(default_factory=dict)
    events: list[tuple[str, int, dict[str, Any]]] = field(default_factory=list)  # (kind, day, args)
    constraints: list[str] = field(default_factory=list)
    agreements: list[str] = field(default_factory=list)
    evidence: list[EvidenceRow] = field(default_factory=list)  # newest last; the board shows the last 5
    inbox: list[str] = field(default_factory=list)
    present: dict[str, str] = field(default_factory=dict)  # role id -> name
    drift: list[str] = field(default_factory=list)
    hires_today: list[str] = field(default_factory=list)
    run_id: str = ""
    """Only used to strip the run-scoped prefix from ids shown on the board (the board text must
    not contain the run id, so the LLM cache hits across runs, forks and eval)."""
    unparsed_inbox: list[str] = field(default_factory=list)  # inbox texts the interpreter could not parse
    rejected: list[str] = field(default_factory=list)  # "rejected by floor: <kind> — <reason>"

    def display_id(self, id_: str | None) -> str:
        if not id_:
            return "n/a"
        if self.run_id:
            for sep in ("-", ":"):
                if id_.startswith(self.run_id + sep):
                    return id_[len(self.run_id) + 1:]
        return id_

    @property
    def locks(self) -> list[str]:
        return [f"protect {p.target} {','.join(p.ids)} until day {p.until_day} ({self.display_id(p.decision_id)})"
                for p in self.view.protected]


def _kpi_line(name: str, k: Kpis) -> str:
    return (f"{name}: on_time {k.on_time_pct:.1f}%, mispick {k.mispick_rate:.2f}%, lph {k.lph:.1f}, "
            f"pm_peak_load {k.pm_peak_zone_load:.2f}, safety_risk {k.safety_risk_max:.2f}, "
            f"near_misses {k.near_misses:.2f}, stockouts {k.stockout_lines}, moves {k.moves_done}, "
            f"progress {k.reslot_progress_pct:.1f}%, cost ${k.labor_cost_usd:,.0f}")


def _render(b: Board, n_events: int, n_evidence: int, inbox_chars: int) -> str:
    v = b.view
    out: list[str] = [f"### Day {b.day}"]
    out.append("**In the room:** " + (", ".join(f"{n} ({r})" for r, n in b.present.items()) or "nobody"))
    if b.hires_today:
        out.append("**Hired today:** " + ", ".join(b.hires_today))
    out.append(f"**Floor:** reslot progress {v.reslot_progress_pct:.1f}% ({v.moves_done_total}/"
               f"{v.moves_target_total} moves, queue {v.move_queue}), staging {v.staging_occupancy:.0f}%, "
               f"temps {v.temps_active}, learning-dip SKUs {v.learning_dip_skus}")
    ot = {p: n for p, n in v.overtime_days_last4.items() if n}
    if ot:
        out.append("**Overtime days (last 4):** " + ", ".join(f"{p} {n}" for p, n in sorted(ot.items())))
    if b.targets:
        out.append("**Targets:** " + ", ".join(f"{k} {x:g}" for k, x in b.targets.items())
                   + (" | **Hard limits:** " + ", ".join(f"{k} {x:g}" for k, x in b.hard_limits.items())
                      if b.hard_limits else ""))
    if b.yesterday is not None:
        out.append("**" + _kpi_line("Yesterday actual", b.yesterday).replace(":", ":**", 1))
    if b.predicted is not None:
        out.append("**" + _kpi_line("Yesterday predicted", b.predicted).replace(":", ":**", 1))
    cons = list(b.constraints)
    cons += [f"aisle {a} closed" for a in v.closed_aisles] + [f"aisle {a} frozen" for a in v.frozen_aisles]
    out.append("**Constraints:** " + ("; ".join(cons) if cons else "none"))
    out.append("**Locks:** " + ("; ".join(b.locks) if b.locks else "none"))
    if b.agreements:
        out.append("**Agreements:** " + "; ".join(b.agreements))
    if b.drift:
        out.append("**Drift flags:** " + "; ".join(b.drift))
    out += [f"- {t}" for t in b.rejected[:5]]
    ev = b.events[:n_events]
    out.append(f"**Events ({len(b.events)}):**" + ("" if ev else " none"))
    for kind, day, args in ev:
        a = ", ".join(f"{k}={x}" for k, x in args.items())
        out.append(f"- day {day} {kind}" + (f" ({a})" if a else ""))
    if len(b.events) > n_events:
        out.append(f"- ... {len(b.events) - n_events} more")
    rows = b.evidence[-n_evidence:] if n_evidence else []
    out.append(f"**Evidence (last {len(rows)}):**" + ("" if rows else " none"))
    out += [r.line() for r in rows]
    if b.inbox:
        out.append("**Open inbox:**")
        out += [f"- {t[:inbox_chars]}" + ("..." if len(t) > inbox_chars else "") for t in b.inbox[:5]]
    if b.unparsed_inbox:
        out.append("**Inbox (unparsed):**")
        out += [f"- {t[:inbox_chars]}" + ("..." if len(t) > inbox_chars else "") for t in b.unparsed_inbox[:5]]
    return "\n".join(out)


def snapshot_text(board: Board, max_chars: int = MAX_CHARS) -> str:
    """Compact Markdown of the board, at most `max_chars` (~1,500 tokens)."""
    for n_events, n_evidence, inbox_chars in ((12, BOARD_EVIDENCE, 300), (6, BOARD_EVIDENCE, 200),
                                              (3, 3, 120), (2, 2, 80), (0, 1, 60)):
        text = _render(board, n_events, n_evidence, inbox_chars)
        if len(text) <= max_chars:
            return text
    return text[: max_chars - 4] + "\n..."


def approx_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


# --------------------------------------------------------------------------- stance memory


class StanceMemory:
    """`role_id -> deque(maxlen=3)` of (day, option_id, stance, one-line rationale)."""

    def __init__(self, maxlen: int = STANCE_MEMORY):
        self.maxlen = maxlen
        self.by_role: dict[str, deque[tuple[int, str, str, str]]] = {}

    def add(self, role_id: str, day: int, option_id: str, stance: str, rationale: str) -> None:
        # numbers stripped: stale figures from earlier days must not be recycled as today's facts
        line = re.sub(r"\d+(\.\d+)?%?", "#", " ".join((rationale or "").split()))[:120]
        self.by_role.setdefault(role_id, deque(maxlen=self.maxlen)).append((day, option_id, stance, line))

    def get(self, role_id: str) -> list[tuple[int, str, str, str]]:
        return list(self.by_role.get(role_id, ()))

    def render(self, role_id: str) -> str:
        rows = self.get(role_id)
        if not rows:
            return ""
        return "## Your last positions\n" + "\n".join(
            f"- day {d}: {s} {o} ({r})" for d, o, s, r in rows)
