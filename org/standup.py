"""The morning standup (spec 10.5): `OrgPolicy`, the LLM org as a `contracts.DecisionPolicy`.

    BRIEF (code) -> TRIAGE (code + chair) -> PROPOSE -> EVIDENCE (what-ifs) -> POSITIONS
    -> AMEND (max 1 round) -> SCORE (code) -> DECIDE (chair) -> ESCALATE (director, if needed)
    -> COMMIT (code: authority, stamping)

Code decides quiet days, hiring, authority, scores, feasibility and every rule the chair must
follow; the LLMs propose, argue and narrate. Every LLM call goes through `llm.complete` with
`build_messages(role prompt, playbook rules, board snapshot, task)` and a safe `fallback`, so a
bad answer never crashes a run (spec 10.6).

Engine-facing extras (all optional for the engine to use):
- `decision.predicted`: the chosen option's first what-if day (`Kpis`), attached with
  `object.__setattr__` (not a pydantic field: `model_dump` ignores it, `model_copy` keeps it).
  The engine reads `getattr(decision, "predicted", None)`. The full `WhatIf` is `self.last_whatif`.
- `decision.deviates_from_top_score` (same mechanism) and `self.last_deviates`.
- `self.pending_drift`: the engine may append drift flags (or call `on_drift(day, flag)`); they
  are read at the next BRIEF. `self.pending_inbox`: raw inbox texts not yet parsed.
- `self.last_spec_hashes`: today's evidence spec hashes; `self.llm_usd`, `self.llm_calls`.
- `on_rejections(day, rejections)`: undo the run-state effects of levers the floor rejected and
  brief them tomorrow. `snapshot_state()` / `restore(payload, forced)`: the org's run state for
  snapshots and forks (see `snapshot_state` for the dict shape). `set_day(day)`: dates
  `llm_calls` rows logged before `decide` (the Event Interpreter runs first).

Ids in prompts are display ids (`m{day}`, `o1..o3`, `ev{day}-{n}`) so the LLM cache hits across
runs, forks and eval; the store keeps the run-scoped ids (`{run}-m{day}`, `{run}-m{day}-o{n}`,
`ev-{run}-{day}-{n}`) and the policy maps between them. What-if levers carry provisional ids
`whatif:{run}:{day}:{n}`; the committed decision re-stamps them with its real id.

The client is synchronous, so positions run in a `ThreadPoolExecutor(MAX_WORKERS)`; their
results are processed in sorted role order, so the outcome does not depend on thread timing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from typing import Any

from pydantic import BaseModel, ValidationError

import contracts as c
from contracts import (
    ChairDecision, ChairTriage, Decision, DirectorRuling, DoneCheck, Event, FloorView, Kpis, Lock,
    Position, Profile, Proposal, Protect, PauseReslot, ReslotTonight, Veto, WhatIf,
)
from llm.prompt import build_messages
from org import authority
from org.board import Board, EvidenceRow, StanceMemory, snapshot_text
from org.playbook_select import select_rules
from org.roles import Role, always_present, chair, director, hire_triggers, load_roles, role_prompt
from org.scoring import Feasibility, Score, feasibility, least_bad, load_profile, score_whatif

log = logging.getLogger(__name__)

HORIZON = 3
"""What-if horizon; clamped to the days left in the scenario (`OrgPolicy._h`)."""
MAX_OPTIONS = 3
MAX_AMENDMENTS = 2
"""Amendment options per meeting. Whatifs per meeting <= 1 REF + 3 options + 2 amendments + 3 lock re-runs."""
LOCK_MAX_DAYS = authority.LOCK_MAX_DAYS_ALONE
DEFAULT_STANDING_BUDGET = 200
RELEASE_AFTER_QUIET_DAYS = 2
TIE_FRACTION = 0.02
TIE_FLOOR = 0.01
"""Top-2 scores 'within 2%': |s1 - s2| <= 2% of max(|s1|, |s2|, TIE_FLOOR). Relative to the
larger magnitude; the floor keeps two near-zero scores from always tying on rounding noise
(0 vs 0 still ties)."""
EPS = 1e-9
MAX_WORKERS = max(1, int(os.environ.get("GEMBA_LLM_WORKERS", "2") or 2))
"""Parallel LLM calls per meeting (env GEMBA_LLM_WORKERS, default 2: fewer 429s when runs share the endpoint)."""
HALF_BATCH_MIN = 50
TEMPS_MAX_DEFAULT = 8
CODE_PAUSE_LABEL = "pause reslot 1 day (code default)"
REF_PICKS = {"ref", "do nothing", "do-nothing", "do_nothing", "none", "null", "nothing"}
POOLS = set(c.Pool.__args__)
SHIFTS = set(c.Shift.__args__)
ALT_ORDER = ("ops_manager", "shift_lead", "replenishment", "safety", "quality", "plant_director")
"""Who is asked for an alternative first (the first distinct alternatives fill the option cap)."""

# --------------------------------------------------------------------------- reply schemas


class Thresholds(BaseModel):
    values: dict[str, float]


class Note(BaseModel):
    text: str


NIGHT_KINDS = ("reslot_tonight", "pause_reslot", "staging_clearance")
# Roster caps per pool (spec 7.3 defaults x2, night movers 4 + up to 8 temps): absolute headcount a
# labor_allocate may set; the floor rejects more, and 0 silently stops a pool.
POOL_HEADCOUNT_CAP = {"pm": 24, "forklift": 28, "pallet": 6, "replen": 8, "consolidation": 20,
                      "loading": 12, "night_move": 12, "temp": 8}


def with_standing_plan(levers: list[c.Lever], budget: int, order: str, *, paused: bool) -> list[c.Lever]:
    """Append the standing reslot to a lever set that says nothing about tonight's moves."""
    if paused or budget <= 0 or any(lv.kind in NIGHT_KINDS for lv in levers):
        return list(levers)
    return list(levers) + [ReslotTonight(n_moves=budget, order=order)]


class MaybeProposal(BaseModel):
    proposal: Proposal | None = None


class SafetyReply(BaseModel):
    position: Position
    veto: Veto | None = None


class QualityReply(BaseModel):
    position: Position
    lock: Lock | None = None


class Accept(BaseModel):
    accept: bool
    reason: str


LEVER_HELP: dict[str, str] = {
    "reslot_tonight": '{"kind":"reslot_tonight","n_moves":<n legs>,"order":"value|zone|family","include_zones":[<Pick Module zone 1-6>],"exclude_zones":[<Pick Module zone 1-6>]}  (zones are the 6 Pick Module zones, NOT aisle numbers 1-28; to keep moves out of a damaged aisle, Safety uses close_aisle)',
    "pause_reslot": '{"kind":"pause_reslot","days":<days>}',
    "labor_allocate": '{"kind":"labor_allocate","shift":"day|swing|night","pool":"pm|forklift|pallet|replen|consolidation|loading|night_move|temp","headcount":<headcount, absolute people on that shift/pool (not a delta)>}',
    "overtime": '{"kind":"overtime","shift":"day|swing|night","pool":"pm|forklift|...","hours":<hours>}',
    "hire_temps": '{"kind":"hire_temps","count":<n, max 8>,"start_day":<day>,"days":<days>}',
    "protect": '{"kind":"protect","target":"zone|skus|customer","ids":["<zone 1-6 | sku_id e.g. S0412 | customer_id e.g. C017>"],"days":<days 1-3>}',
    "resize_slots": '{"kind":"resize_slots","skus":["<sku_id e.g. S0412>"],"days_cover":<days 1-10>}',
    "wave_priority": '{"kind":"wave_priority","mode":"rush_first|by_cutoff|by_zone"}',
    "close_aisle": '{"kind":"close_aisle","aisle":<aisle 1-28>,"days":<days>}',
    "reopen_aisle": '{"kind":"reopen_aisle","aisle":<aisle 1-28>}',
    "staging_clearance": '{"kind":"staging_clearance","n_movers":<n>}',
}


def tier_for(tier: str) -> str:
    """Map a role tier to the tier actually called: "ultra" runs on "super" while MODEL_ULTRA
    is empty (decision log 26: Ultra is not in the Crusoe catalog today)."""
    if tier == "ultra" and not (os.environ.get("MODEL_ULTRA") or "").strip():
        return "super"
    return tier


def lever_str(lv: c.Lever) -> str:
    args = {k: v for k, v in lv.model_dump(mode="json", exclude={"kind", "decision_id"}).items()
            if v not in ([], None)}
    return f"{lv.kind}(" + ", ".join(f"{k}={v}" for k, v in args.items()) + ")"


def spec_hash(levers: list[c.Lever], day: int) -> str:
    """sha256 of the sorted lever dumps + day (the drift detector's repeat-whatif key)."""
    dumps = sorted(json.dumps(lv.model_dump(mode="json", exclude={"decision_id"}), sort_keys=True)
                   for lv in levers)
    return hashlib.sha256(json.dumps({"levers": dumps, "day": day}).encode()).hexdigest()


def _event_end(e: Event) -> int:
    return e.day + max(1, int(e.args.get("days", 1) or 1)) - 1


def _is_fallback(p: Position) -> bool:
    return (p.rationale or "").lstrip().startswith("(fallback)")


def _lever_key(lv: c.Lever) -> str:
    return json.dumps(lv.model_dump(mode="json", exclude={"decision_id"}), sort_keys=True)


# --------------------------------------------------------------------------- per-meeting state


@dataclass
class Option:
    option_id: str
    label: str
    levers: list[c.Lever]
    proposed_by: str
    rationale: str = ""
    evidence_id: str | None = None
    whatif: WhatIf | None = None
    score: Score | None = None
    feas: Feasibility | None = None
    vetoed_by: str | None = None
    notes: list[str] = field(default_factory=list)
    replaced: bool = False

    @property
    def total(self) -> float:
        return self.score.total if self.score is not None else -math.inf

    @property
    def feasible(self) -> bool:
        return bool(self.feas and self.feas.feasible)


@dataclass
class _Meeting:
    day: int
    meeting_id: str
    seq: int = 0
    n_options: int = 0
    options: list[Option] = field(default_factory=list)
    ref: WhatIf | None = None
    ref_id: str | None = None
    lock_levers: list[c.Lever] = field(default_factory=list)
    positions: dict[str, Position] = field(default_factory=dict)

    def active(self) -> list[Option]:
        return [o for o in self.options if not o.replaced]

    def get(self, option_id: str | None) -> Option | None:
        return next((o for o in self.active() if o.option_id == option_id), None)


# --------------------------------------------------------------------------- the policy


class OrgPolicy:
    name = "org"

    def __init__(self, run_id: str, profile: Profile | str | None = None, llm: Any = None, store: Any = None,
                 roles: dict[str, Role] | None = None, playbook_rules: list[dict] | None = None, seed: int = 0,
                 scenario_end: int = 90):
        self.run_id = run_id
        self.scenario_end = int(scenario_end)
        self.profile = (load_profile(profile or "customer_first") if not isinstance(profile, Profile) else profile)
        if store is None:
            from engine.store import RunStore
            store = RunStore()
            store.create_schema()
        self.store = store
        if llm is None:
            from llm.client import LLM
            llm = LLM()
        self.llm = llm
        if getattr(llm, "on_call", "absent") is None:  # a real client without a sink: log to llm_calls
            llm.on_call = self._on_call
        elif callable(getattr(llm, "on_call", None)) and getattr(llm.on_call, "__self__", None) is not self:
            # A client built with on_call=store.write_llm_call (eval/gate/retro CLIs, engine/run.py):
            # stamp the policy's current day on rows that lack one, so `llm_calls.day` is not NULL.
            # The base sink is kept so a client shared by several policies is not wrapped repeatedly.
            base = getattr(llm.on_call, "_gemba_base", llm.on_call)

            def _stamped(row, _base=base):
                if isinstance(row, dict) and row.get("day") is None:
                    row = {**row, "day": self._day}
                return _base(row)
            _stamped._gemba_base = base
            try:
                llm.on_call = _stamped
            except Exception:  # noqa: BLE001 - read-only attribute: leave it
                pass
        self.roles = roles if roles is not None else load_roles()
        self.playbook_rules = list(playbook_rules or [])
        self.seed = seed
        self.chair_id = chair(self.roles).id
        self.director_id = director(self.roles).id
        self.always = always_present(self.roles)
        self.engineer_id = "reslotting_engineer" if "reslotting_engineer" in self.roles else self.chair_id
        self._prompts = {rid: role_prompt(r) for rid, r in self.roles.items()}

        # per-run state
        self.present: set[str] = set(self.always)
        self.quiet_streak: dict[str, int] = {}  # hired role -> consecutive days without its trigger
        self.stances = StanceMemory()
        self.evidence_rows: list[EvidenceRow] = []
        self.known_evidence: set[str] = set()
        self.overtime_used: dict[int, float] = {}  # week index -> hours
        self.meetings_held = 0
        self.standing_budget = DEFAULT_STANDING_BUDGET
        self.standing_order = "value"
        self.paused_until = 0
        self.agreements: list[str] = []
        self.pending_drift: list[Any] = []
        self.pending_inbox: list[str] = []
        self.last_spec_hashes: list[str] = []
        self.last_whatif: WhatIf | None = None
        self.last_deviates = False
        self.last_ref: WhatIf | None = None  # the latest do-nothing REF what-if (reference for KPI bands)
        self.last_ref_day: int | None = None
        self.pending_rejections: list[str] = []  # board lines for the next brief
        self._applied: dict[int, tuple[dict, list[c.Lever]]] = {}  # day -> (run state before, levers applied)
        self._wn = 0
        self._amendments = 0
        self._lock_reruns = 0
        self._twin: Any = None
        self._state: Any = None
        self._events: list[Event] = []
        self._unparsed: list[str] = []
        self._rejected: list[str] = []
        self.llm_calls = 0
        self.llm_usd = 0.0
        self.llm_latency_ms = 0
        self._lock = threading.Lock()
        self._day = 0
        self._tags: set[str] = set()
        self.board: Board | None = None

    # ================================================================== engine hooks
    def on_drift(self, day: int, flag: Any) -> None:
        """Engine hook: a drift flag raised at the end of `day` is briefed next morning."""
        self.pending_drift.append(flag)

    def set_day(self, day: int) -> None:
        """Date the `llm_calls` rows logged before `decide` (e.g. the Event Interpreter's)."""
        self._day = day

    def on_rejections(self, day: int, rejections: list[c.LeverRejection]) -> None:
        """Engine hook: the twin rejected some of `day`'s levers. Undo their run-state effects
        (standing budget/order, pause, overtime used) and brief them at the next standup."""
        if not rejections:
            return
        rec = self._applied.get(day)
        if rec is not None:
            before, levers = rec
            remaining = list(levers)
            for rj in rejections:
                k = _lever_key(rj.lever)
                i = next((i for i, lv in enumerate(remaining) if _lever_key(lv) == k), None)
                if i is not None:
                    del remaining[i]
            self._set_run_state(before)
            self._apply_run_state(day, remaining)
        for rj in rejections:
            self.pending_rejections.append(f"rejected by floor: {rj.lever.kind} — {self._scrub(rj.reason)}"[:300])

    def snapshot_state(self) -> dict:
        """The org's run state, JSON-safe, for the engine's daily snapshot (`payload["org_state"]`).

        Shape (version 1):
            {"version": 1, "standing_budget": int, "standing_order": str, "paused_until": int,
             "overtime_used": {"<week index>": hours}, "present": [role ids],
             "quiet_streak": {role: int}, "stances": {role: [[day, option_id, stance, line], ...]},
             "agreements": [str], "meetings_held": int, "pending_rejections": [str],
             "pending_drift": [str], "last_ref": WhatIf json | None, "last_ref_day": int | None,
             "evidence_rows": [EvidenceRow fields as dicts, last 5]}
        """
        return {
            "version": 1,
            "standing_budget": int(self.standing_budget),
            "standing_order": self.standing_order,
            "paused_until": int(self.paused_until),
            "overtime_used": {str(k): float(v) for k, v in self.overtime_used.items()},
            "present": sorted(self.present),
            "quiet_streak": {k: int(v) for k, v in sorted(self.quiet_streak.items())},
            "stances": {r: [list(x) for x in self.stances.get(r)] for r in sorted(self.stances.by_role)},
            "agreements": list(self.agreements),
            "meetings_held": int(self.meetings_held),
            "pending_rejections": list(self.pending_rejections),
            "pending_drift": [self._flag_text(f) for f in self.pending_drift],
            "last_ref": self.last_ref.model_dump(mode="json") if self.last_ref is not None else None,
            "last_ref_day": self.last_ref_day,
            "evidence_rows": [asdict(r) for r in self.evidence_rows[-5:]],
        }

    def restore(self, payload: dict, forced: Decision | None = None) -> None:
        """Engine hook on a fork, after the snapshot is loaded. `payload["hires"]` (unreleased hire
        rows) rebuilds who is present; `payload["paused_until"]` and `payload["org_state"]` (from
        `snapshot_state`) restore the run state. `forced` (the fork-day decision the engine applies
        instead of a meeting) goes through the same run-state updates as a committed decision."""
        org = payload.get("org_state") or {}
        if org:
            self.standing_budget = int(org.get("standing_budget", self.standing_budget))
            self.standing_order = org.get("standing_order", self.standing_order)
            self.paused_until = int(org.get("paused_until", 0) or 0)
            self.overtime_used = {int(k): float(v) for k, v in (org.get("overtime_used") or {}).items()}
            self.stances = StanceMemory()
            for r, rows in (org.get("stances") or {}).items():
                for d, o, s, line in rows:
                    self.stances.add(r, int(d), o, s, line)
            self.agreements = list(org.get("agreements") or [])
            self.meetings_held = int(org.get("meetings_held", 0) or 0)
            self.pending_rejections = list(org.get("pending_rejections") or [])
            self.pending_drift = list(org.get("pending_drift") or [])
            lr = org.get("last_ref")
            self.last_ref = WhatIf.model_validate(lr) if lr else None
            self.last_ref_day = org.get("last_ref_day")
            self.evidence_rows = [EvidenceRow(**r) for r in (org.get("evidence_rows") or [])]
        self.paused_until = max(self.paused_until, int(payload.get("paused_until", 0) or 0))
        hires = payload.get("hires")
        if hires is not None:
            hired = {h.get("role") for h in hires if isinstance(h, dict) and h.get("released_day") is None}
            self.present = set(self.always) | {r for r in hired if r in self.roles}
        elif org.get("present"):
            self.present = set(self.always) | {r for r in org["present"] if r in self.roles}
        streak = org.get("quiet_streak") or {}
        self.quiet_streak = {r: int(streak.get(r, 0)) for r in sorted(self.present - self.always)}
        if forced is not None:
            self._day = forced.day
            self._apply_run_state(forced.day, list(forced.levers))

    def _on_call(self, row: dict) -> None:
        try:
            day = row.get("day") if isinstance(row, dict) else None
            self.store.write_llm_call(row, day=day if day is not None else self._day)
        except Exception:  # noqa: BLE001 - logging must never break a meeting
            log.exception("could not log llm call")

    # ================================================================== run state
    def _run_state(self) -> dict:
        return {"standing_budget": self.standing_budget, "standing_order": self.standing_order,
                "paused_until": self.paused_until, "overtime_used": dict(self.overtime_used)}

    def _set_run_state(self, s: dict) -> None:
        self.standing_budget, self.standing_order = s["standing_budget"], s["standing_order"]
        self.paused_until, self.overtime_used = s["paused_until"], dict(s["overtime_used"])

    def _apply_run_state(self, day: int, levers: list[c.Lever]) -> None:
        """Standing plan, pause and overtime-used updates for levers issued on `day` (kept so
        `on_rejections` can undo them)."""
        self._applied = {d: v for d, v in self._applied.items() if d >= day - 1}
        self._applied[day] = (self._run_state(), list(levers))
        for lv in levers:
            if lv.kind == "reslot_tonight":
                self.standing_budget, self.standing_order = lv.n_moves, lv.order
            elif lv.kind == "pause_reslot":
                self.paused_until = max(self.paused_until, day + lv.days - 1)
            elif lv.kind == "overtime":
                wk = (day - 1) // 7
                self.overtime_used[wk] = self.overtime_used.get(wk, 0.0) + lv.hours

    # ================================================================== ids and what-ifs
    def _h(self, day: int) -> int:
        """What-if horizon: HORIZON days, never past the scenario's last day."""
        return max(1, min(HORIZON, self.scenario_end - day + 1))

    def _whatif(self, twin: Any, state: Any, levers: list[c.Lever], events: list[Event]) -> WhatIf:
        n = self._wn
        self._wn += 1
        did = f"whatif:{self.run_id}:{self._day}:{n}"
        return twin.whatif(state, [lv.model_copy(update={"decision_id": did}) for lv in levers],
                           self._h(self._day), events)

    def _scrub(self, text: Any) -> str:
        """Drop run-scoped id prefixes from free text that goes into prompts."""
        return str(text).replace(f"{self.run_id}-", "").replace(f"{self.run_id}:", "")

    def _disp_opt(self, oid: str | None) -> str:
        prefix = f"{self._m.meeting_id}-"
        return oid[len(prefix):] if oid and oid.startswith(prefix) else (oid or "")

    def _store_opt(self, s: str | None) -> str | None:
        if s is None:
            return None
        t = s.strip().strip("`")
        return f"{self._m.meeting_id}-{t}" if re.fullmatch(r"o\d+", t) else t

    def _disp_ev(self, eid: str | None) -> str:
        prefix = f"ev-{self.run_id}-"
        return "ev" + eid[len(prefix):] if eid and eid.startswith(prefix) else (eid or "")

    def _store_ev(self, s: str) -> str:
        t = str(s).strip().strip("`")
        mt = re.fullmatch(r"ev(\d+)-(\d+)", t)
        return f"ev-{self.run_id}-{mt.group(1)}-{mt.group(2)}" if mt else t

    def _norm_pos(self, p: Position) -> Position:
        """A position as the model wrote it (display ids) -> store ids."""
        return p.model_copy(update={"option_id": self._store_opt(p.option_id) or "",
                                    "evidence_ids": [self._store_ev(e) for e in p.evidence_ids]})

    # ================================================================== LLM plumbing
    def _ask(self, role_id: str, schema: type[BaseModel], task: str, fallback: BaseModel, *,
             tier: str | None = None, reasoning: bool = False, max_tokens: int = 800,
             board_text: str | None = None) -> BaseModel:
        role = self.roles[role_id]
        memory = self.stances.render(role_id)
        msgs = build_messages(self._prompts[role_id], select_rules(self.playbook_rules, role_id, self._tags),
                              board_text if board_text is not None else snapshot_text(self.board),
                              task + ("\n\n" + memory if memory else ""))
        t = tier_for(tier or role.model_tier)
        try:
            out, meta = self.llm.complete(t, msgs, schema, role=role_id, run_id=self.run_id, reasoning=reasoning,
                                          max_tokens=max_tokens, fallback=fallback)
        except Exception as e:  # noqa: BLE001 - a transport error must never crash a run
            log.warning("%s: llm transport error %s; using fallback", role_id, type(e).__name__)
            self._log_transport(role_id, t, e)
            return fallback
        with self._lock:
            self.llm_calls += 1
            self.llm_usd += float(getattr(meta, "usd", 0.0) or 0.0)
            self.llm_latency_ms += int(getattr(meta, "latency_ms", 0) or 0)
        if not isinstance(out, schema):
            log.warning("%s returned %s, expected %s; using fallback", role_id, type(out).__name__, schema.__name__)
            return fallback
        return out

    def _log_transport(self, role_id: str, tier: str, e: Exception) -> None:
        row = {"call_id": uuid.uuid4().hex, "run_id": self.run_id, "day": self._day, "role": role_id,
               "model": "(transport)", "request": {"error": type(e).__name__, "tier": tier},
               "response": {"llm_fallback": True, "error": f"{type(e).__name__}: {e}"[:500]}, "usd": 0.0,
               "kind": "llm_fallback", "fallback_used": True}
        sink = getattr(self.llm, "on_call", None)
        if not callable(sink):
            sink = self._on_call
        try:
            sink(row)
        except Exception:  # noqa: BLE001
            log.exception("could not log transport fallback")

    def _log_fallback(self, role_id: str, reason: str) -> None:
        """An llm_fallback row for a code-level fallback (e.g. a position that never cited evidence)."""
        try:
            self.store.write_llm_call(call_id=uuid.uuid4().hex, run_id=self.run_id, day=self._day, role=role_id,
                                      model="(code)", request={"reason": "validation"},
                                      response={"llm_fallback": True, "error": reason}, usd=0.0,
                                      kind="llm_fallback", fallback_used=True)
        except Exception:  # noqa: BLE001
            log.exception("could not log fallback")

    def _parallel(self, fn, items: list) -> list:
        if len(items) <= 1:
            return [fn(x) for x in items]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            return list(ex.map(fn, items))

    # ================================================================== thresholds
    def pick_thresholds(self, checks: list[DoneCheck]) -> list[DoneCheck]:
        """One chair call (Ultra, reasoning) picks each threshold within its code-set bounds."""
        if not checks:
            return []
        lines = [f"- {k.check_name}: {k.metric} {k.op} threshold (default {k.threshold:g}, allowed "
                 f"[{k.min_threshold:g}, {k.max_threshold:g}])" for k in checks]
        task = ("Pick the done-check thresholds for this run. Stay inside each allowed range; the company "
                "profile is your anchor.\n" + "\n".join(lines)
                + '\n\nReply JSON {"values": {"<check_name>": <number>, ...}}.')
        p = self.profile
        board = (f"Run start. Profile {p.id}: weights {p.weights}; targets {p.targets}; "
                 f"hard limits {p.hard_limits}; budgets {p.budgets}.")
        fallback = Thresholds(values={k.check_name: k.threshold for k in checks})
        out = self._ask(self.chair_id, Thresholds, task, fallback, tier="ultra", reasoning=True,
                        max_tokens=1500, board_text=board)
        result = []
        for k in checks:
            v = out.values.get(k.check_name, k.threshold)
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = k.threshold
            v = min(k.max_threshold, max(k.min_threshold, v)) if math.isfinite(v) else k.threshold
            result.append(k.model_copy(update={"threshold": v}))
        return result

    # ================================================================== decide
    def decide(self, day: int, view: FloorView, events: list[Event], twin: Any, state: Any) -> Decision:
        self._day = day
        m = self._m = _Meeting(day=day, meeting_id=f"{self.run_id}-m{day}")
        self._evn = 0
        self._wn = 0
        self._amendments = 0
        self._lock_reruns = 0
        self._twin, self._state = twin, state
        self.last_spec_hashes = []
        self.last_deviates = False

        # ---- 1. BRIEF (code)
        yesterday, predicted = self._yesterday(day)
        new_events = sorted([e for e in events if e.day == day or e.announced_day == day],
                            key=lambda e: (e.day, e.kind, e.event_id))
        kinds = sorted({e.kind for e in new_events})
        consequences = [e for e in events if e.source == "consequence" and e.day == day]
        violations = self._violations(yesterday, day)
        drift_flags, self.pending_drift = list(self.pending_drift), []
        drift = [self._scrub(self._flag_text(f)) for f in drift_flags]
        parsed_inbox = [f"{e.kind} {e.args}" for e in new_events if e.source == "inbox"]
        unparsed, self.pending_inbox = [str(t) for t in self.pending_inbox], []
        inbox = parsed_inbox + unparsed
        rejected, self.pending_rejections = list(self.pending_rejections), []
        self._kinds = set(kinds)
        self._tags = set(kinds) | set(violations) | {getattr(f, "kind", str(f)) for f in drift_flags}

        # ---- 2. TRIAGE (code): hiring, then quiet-day check
        kpis_y = yesterday.model_dump(exclude={"mispick_rate_by_customer"}) if yesterday else {}
        active_kinds = sorted(set(kinds) | {e.kind for e in events if e.day <= day <= _event_end(e)})
        fired = hire_triggers(self.roles, active_kinds, kpis_y)
        hires_today = self._code_hiring(day, fired)
        quiet = not (new_events or consequences or drift or inbox or violations or rejected)
        self._view, self._events, self._yday, self._pred = view, events, yesterday, predicted
        self._inbox, self._drift, self._hires_today = parsed_inbox, drift, hires_today
        self._unparsed, self._rejected = unparsed, rejected
        self._refresh_board()
        if quiet:
            return self._quiet_day(day, view, events, twin, state)

        self.meetings_held += 1
        triage = self._ask(self.chair_id, ChairTriage, self._triage_task(new_events, violations, drift, inbox, fired),
                           ChairTriage(meeting=True, agenda=self._code_agenda(new_events, violations, drift, inbox),
                                       hire=[], release=[]),
                           tier="ultra", reasoning=False)
        for h in triage.hire:  # code validates the chair's extra hires
            r = self.roles.get(h.role)
            if r is not None and r.attends == "on_trigger" and h.role != self.director_id and h.role not in self.present:
                self._hire(day, h.role, f"chair: {h.trigger}"[:200])
        for rid in triage.release:
            if rid in self.present and rid not in self.always and rid not in fired and rid != self.director_id:
                self._release(day, rid)
        agenda = [a for a in triage.agenda if a.strip()] or self._code_agenda(new_events, violations, drift, inbox)
        kind = "escalation" if "director_escalation" in kinds else "standup"
        self.store.write_meeting(self.run_id, m.meeting_id, day, kind, sorted(self.present), agenda,
                                 caused_by=[c.node_id(c.EVT, e.event_id) for e in new_events] or None)
        self._refresh_board()

        # ---- 3. PROPOSE
        self._propose(day)

        # ---- 4. EVIDENCE (Paige: code + whatif)
        m.ref = self._whatif(twin, state, [], events)
        self.last_ref, self.last_ref_day = m.ref, day
        m.ref_id = self._write_evidence("do nothing (REF)", [], m.ref, None)
        for o in m.active():
            self._run_evidence(o, twin, state, events)
        self._smaller_batch(twin, state, events)
        self._ensure_pause(twin, state, events)
        self._refresh_board()

        # ---- 5. POSITIONS (parallel)
        voters = sorted(r for r in self.present
                        if r not in (self.chair_id, self.director_id) and not self.roles[r].runs_simulator)
        for role_id, pos, extra in self._parallel(self._position, voters):
            self._record_position(day, role_id, pos)
            if isinstance(extra, Veto):
                self._handle_veto(role_id, extra)
            elif isinstance(extra, Lock):
                self._handle_lock(role_id, extra)

        # ---- 6. AMEND (max 1 round, no second position round)
        self._amend(twin, state, events)
        self._capacity_check(day)

        # ---- 7. SCORE (code)
        for o in m.active():
            if o.score is None and o.whatif is not None:
                o.score = score_whatif(o.whatif, m.ref, self.profile)
                o.feas = feasibility(o.whatif, self.profile, m.ref)
            self._update_option(o, score=o.score.total if o.score else None, feasible=o.feasible,
                                vetoed_by=o.vetoed_by, levers=o.levers, evidence_id=o.evidence_id)

        # ---- 8. DECIDE (chair)
        chosen, rationale, escalate_reasons, deviates, chair_ev = self._chair_decide(day)
        decided_by = self.chair_id
        if "director_escalation" in kinds:
            escalate_reasons.append("director_escalation event on the agenda")

        # ---- 9. ESCALATE
        if escalate_reasons:
            chosen, rationale, decided_by = self._escalate(day, chosen, rationale, escalate_reasons)

        # ---- 10. COMMIT (code)
        return self._commit(day, chosen, rationale, decided_by, deviates, chair_ev, escalate_reasons)

    # ================================================================== brief helpers
    def _yesterday(self, day: int) -> tuple[Kpis | None, Kpis | None]:
        try:
            rows = [r for r in self.store.day_results(self.run_id) if r["day"] == day - 1]
        except Exception:  # noqa: BLE001
            return None, None
        if not rows:
            return None, None
        r = rows[-1]
        k = Kpis.model_validate(r["kpis"]) if r.get("kpis") else None
        p = Kpis.model_validate(r["predicted"]) if r.get("predicted") else None
        return k, p

    def _ref_for(self, day: int) -> Kpis | None:
        """The do-nothing REF forecast for `day` (from the latest REF what-if run on or before it)."""
        r, d0 = self.last_ref, self.last_ref_day
        if r is None or d0 is None or not r.kpis_by_day or day < d0:
            return None
        return r.kpis_by_day[min(day - d0, len(r.kpis_by_day) - 1)]

    def _violations(self, k: Kpis | None, day: int | None = None) -> list[str]:
        """Yesterday's KPIs out of band, relative to the do-nothing REF forecast for yesterday (so
        the twin's own magnitudes do not make every day a meeting); the profile targets when no
        REF exists yet. Safety stays absolute (> 1.0 is risk)."""
        if k is None:
            return []
        t = self.profile.targets
        ref = self._ref_for((day if day is not None else self._day) - 1)
        on_time_t = t.get("on_time_pct", 97.0)
        mispick_t = t.get("mispick_rate", 0.5)
        out = []
        if ref is None:
            on_time_bar, mispick_bar, pm_bar = on_time_t - 2, mispick_t, 1.0
            stockout_bar = 0.01 * k.lines
        else:
            on_time_bar = ref.on_time_pct - 2
            mispick_bar = max(mispick_t, ref.mispick_rate * 1.25)
            pm_bar = max(1.0, ref.pm_peak_zone_load)
            stockout_bar = max(0.01 * k.lines, ref.stockout_lines * 1.5)
        if k.on_time_pct < on_time_bar:
            out.append("on_time_pct")
        if k.mispick_rate > mispick_bar:
            out.append("mispick_rate")
        if k.pm_peak_zone_load > pm_bar:
            out.append("pm_peak_zone_load")
        if k.safety_risk_max > 1.0:
            out.append("safety_risk_max")
        if k.stockout_lines > stockout_bar:
            out.append("stockout_lines")
        return out

    @staticmethod
    def _flag_text(f: Any) -> str:
        if isinstance(f, str):
            return f
        kind, detail = getattr(f, "kind", None), getattr(f, "detail", None)
        if kind is None and isinstance(f, dict):
            kind, detail = f.get("kind"), f.get("detail")
        return f"{kind}: {detail}" if detail else str(kind or f)

    def _refresh_board(self) -> None:
        day, view = self._day, self._view
        evs = sorted([e for e in self._events if _event_end(e) >= day],
                     key=lambda e: (e.day, e.kind, e.event_id))
        constraints = [f"reslot paused until day {self.paused_until}"] if self.paused_until >= day else []
        constraints += [f"{e.kind} {e.args} (days {e.day}-{_event_end(e)})" for e in evs
                        if e.day <= day <= _event_end(e) and e.day < day]
        self.board = Board(
            day=day, view=view, yesterday=self._yday, predicted=self._pred, targets=dict(self.profile.targets),
            hard_limits=dict(self.profile.hard_limits), events=[(e.kind, e.day, dict(e.args)) for e in evs],
            constraints=constraints, agreements=list(self.agreements), evidence=list(self.evidence_rows),
            inbox=list(self._inbox), present={r: self.roles[r].name for r in sorted(self.present)},
            drift=list(self._drift), hires_today=list(self._hires_today), run_id=self.run_id,
            unparsed_inbox=[self._scrub(t) for t in self._unparsed], rejected=list(self._rejected))

    # ================================================================== hiring
    def _hire(self, day: int, role_id: str, trigger: str) -> None:
        if role_id in self.present:
            return
        self.store.write_hire(self.run_id, day, role_id, trigger)
        self.present.add(role_id)
        self.quiet_streak[role_id] = 0
        self._hires_today = list(getattr(self, "_hires_today", [])) + [f"{self.roles[role_id].name} ({trigger})"]

    def _release(self, day: int, role_id: str) -> None:
        self.store.release_hire(self.run_id, role_id, day)
        self.present.discard(role_id)
        self.quiet_streak.pop(role_id, None)

    def _code_hiring(self, day: int, fired: dict[str, str]) -> list[str]:
        self._hires_today = []
        for rid in sorted(self.present - self.always):
            if rid in fired:
                self.quiet_streak[rid] = 0
            else:
                self.quiet_streak[rid] = self.quiet_streak.get(rid, 0) + 1
                if self.quiet_streak[rid] >= RELEASE_AFTER_QUIET_DAYS:
                    self._release(day, rid)
        for rid, trig in sorted(fired.items()):
            self._hire(day, rid, trig)
        return self._hires_today

    # ================================================================== quiet day
    def _quiet_day(self, day: int, view: FloorView, events: list[Event], twin: Any, state: Any) -> Decision:
        m = self._m
        paused = self.paused_until >= day
        levers: list[c.Lever] = [] if paused else [ReslotTonight(n_moves=self.standing_budget,
                                                                 order=self.standing_order)]
        plan = "reslot paused (pause in force)" if paused else \
            f"reslot {self.standing_budget} moves tonight by {self.standing_order}"
        whatif = self._whatif(twin, state, levers, events)
        ev_id = self._write_evidence("standing plan", levers, whatif, None)
        self._refresh_board()
        task = (f"Quiet day: no new events, no consequences, no drift flags, no inbox, yesterday's KPIs in band. "
                f"The standing plan continues: {plan} (evidence `{self._disp_ev(ev_id)}`). Write Megan's one-line standup "
                'note (25 words or fewer). Reply JSON {"text": "..."}.')
        note = self._ask(self.chair_id, Note, task, Note(text=f"Quiet day; standing plan continues: {plan}."),
                         tier="nano")
        text = " ".join(note.text.split())[:300] or f"Quiet day; standing plan continues: {plan}."
        self.store.write_meeting(self.run_id, m.meeting_id, day, "quiet", [self.chair_id], ["standing plan"])
        self._utter(self.chair_id, "decision", text, [ev_id])
        self.store.set_meeting_outcome(self.run_id, m.meeting_id, f"standing plan: {plan}")
        did = f"{self.run_id}-d{day}"
        decision = Decision(decision_id=did, day=day, levers=[lv.model_copy(update={"decision_id": did}) for lv in levers],
                            constraints=[], decided_by=self.chair_id, rationale=text, option_id=None,
                            meeting_id=m.meeting_id)
        self._attach(decision, whatif, False)
        self._apply_run_state(day, list(decision.levers))
        return decision

    # ================================================================== store helpers
    def _utter(self, role: str, kind: str, text: str, evidence_ids=(), option_id: str | None = None) -> None:
        m = self._m
        m.seq += 1
        self.store.write_utterance(self.run_id, m.meeting_id, m.seq, role, kind, text,
                                   [e for e in evidence_ids if e], option_id)

    def _update_option(self, o: Option, **vals: Any) -> None:
        upd = getattr(self.store, "update_option", None)
        if callable(upd):
            upd(self.run_id, o.option_id, **vals)

    def _write_evidence(self, label: str, levers: list[c.Lever], whatif: WhatIf, option_id: str | None) -> str:
        m = self._m
        ev_id = f"ev-{self.run_id}-{m.day}-{self._evn}"
        self._evn += 1
        h = spec_hash(levers, m.day)
        sc = score_whatif(whatif, m.ref, self.profile) if m.ref is not None else None
        fe = feasibility(whatif, self.profile, m.ref)
        hz = self._h(m.day)
        self.store.write_evidence(
            self.run_id, ev_id, h, {"label": label, "option_id": option_id, "levers": levers, "horizon": hz},
            hz, whatif.kpis_total,
            scorecard={"score": sc.total if sc else None, "terms": sc.terms if sc else None,
                       "feasible": fe.feasible, "breaches": fe.breaches,
                       "expected_near_misses": whatif.expected_near_misses,
                       "safety_risk_max": whatif.safety_risk_max, "pm_peak_zone_load": whatif.pm_peak_zone_load},
            created_day=m.day)
        self.last_spec_hashes.append(h)
        self.known_evidence.add(ev_id)
        self.evidence_rows.append(EvidenceRow.from_kpis(self._disp_ev(ev_id), label, whatif.kpis_total,
                                                        whatif.expected_near_misses))
        return ev_id

    def _run_evidence(self, o: Option, twin: Any, state: Any, events: list[Event]) -> None:
        try:
            o.whatif = self._whatif(twin, state, list(o.levers), events)
        except ValueError as e:  # A's whatif raises on levers apply_levers would reject (A/B agreement 14:12)
            o.replaced = True
            self._utter(self.chair_id, "question", f"{self._disp_opt(o.option_id)} withdrawn: the floor rejects its "
                        f"levers: {self._scrub(e)[:200]}", option_id=o.option_id)
            return
        o.evidence_id = self._write_evidence(f"{self._disp_opt(o.option_id)} {o.label}", o.levers, o.whatif,
                                             o.option_id)
        o.score = score_whatif(o.whatif, self._m.ref, self.profile)
        o.feas = feasibility(o.whatif, self.profile, self._m.ref)
        self._update_option(o, evidence_id=o.evidence_id, score=o.score.total, feasible=o.feas.feasible)

    # ================================================================== propose
    def _signals(self) -> str:
        b = self.board
        ev = [f"{k} day {d} {a}" for k, d, a in b.events] if b else []
        text = ("Events: " + ("; ".join(ev) or "none")
                + f"\nStanding plan: reslot {self.standing_budget} moves/night by {self.standing_order}"
                + (f" (paused until day {self.paused_until})" if self.paused_until >= self._day else ""))
        y, v = getattr(self, "_yday", None), getattr(self, "_view", None)
        if (y is not None and y.moves_done == 0 and self.standing_budget > 0 and v is not None
                and v.moves_target_total > v.moves_done_total and self.paused_until < self._day - 1):
            night = (v.labor_available.get("night") or {}).get("night_move", "?")
            text += (f"\nWARNING: last night's planned moves did not execute (0 done; night movers available: "
                     f"{night}; {v.move_queue} legs queued). Restore night labor or expect no progress.")
        return text

    def _lever_help(self, role_id: str) -> str:
        kinds = self.roles[role_id].levers_can_propose
        lines = [f"- {LEVER_HELP[k].replace('<day>', str(self._day))}" for k in kinds]
        v = getattr(self, "_view", None)
        if v is not None and any(k in ("labor_allocate", "overtime") for k in kinds):
            cur = "; ".join(f"{s}: " + ", ".join(f"{p} {n}" for p, n in pools.items() if n)
                            for s, pools in v.labor_available.items())
            lines.append(f"  (current headcount, absolute - {cur})")
        return "\n".join(lines) or "- (none)"

    def _propose(self, day: int) -> None:
        eng = self.engineer_id
        task = (f"Propose exactly ONE option for tonight (day {day}).\n{self._signals()}\n\n"
                f"Use only these levers (JSON shapes):\n{self._lever_help(eng)}\n\n"
                'Reply JSON {"option_label": "...", "levers": [...], "rationale": "<= 60 words"}.')
        fb = Proposal(option_label="standing plan", rationale="(fallback) standing plan continues",
                      levers=[ReslotTonight(n_moves=self.standing_budget, order=self.standing_order)])
        self._add_option(eng, self._ask(eng, Proposal, task, fb))

        skip = {self.engineer_id, self.chair_id, "analytics"}
        cands = [r for r in self.present if r in self.roles and r not in skip
                 and not self.roles[r].runs_simulator and self.roles[r].levers_can_propose]
        alts = sorted(cands, key=lambda r: (ALT_ORDER.index(r) if r in ALT_ORDER else len(ALT_ORDER), r))
        first = self._m.active()[0] if self._m.active() else None
        tabled = (f"The engineer tabled `{self._disp_opt(first.option_id)}` {first.label}: "
                  + ", ".join(lever_str(lv) for lv in first.levers)) if first else "No option tabled yet."

        def ask_alt(rid: str) -> MaybeProposal:
            t = (f"{tabled}\n{self._signals()}\n\nYou may add ONE alternative option, or none. Use only these "
                 f"levers (JSON shapes):\n{self._lever_help(rid)}\n\n"
                 'Reply JSON {"proposal": {"option_label": "...", "levers": [...], "rationale": "..."}} '
                 'or {"proposal": null}.')
            return self._ask(rid, MaybeProposal, t, MaybeProposal(proposal=None))

        for rid, res in zip(alts, self._parallel(ask_alt, alts)):
            if res.proposal is None:
                continue
            if len(self._m.active()) >= MAX_OPTIONS:
                self._utter(rid, "question", f"alternative '{res.proposal.option_label}' not tabled: "
                            f"cap {MAX_OPTIONS} options")
                continue
            self._add_option(rid, res.proposal)
        if len(self._m.active()) < 2:
            self._add_option(self.chair_id, Proposal(option_label=CODE_PAUSE_LABEL,
                                                     levers=[PauseReslot(days=1)],
                                                     rationale="Code-generated alternative so there is a choice."),
                             check_authority=False)

    def _new_option_id(self) -> str:
        m = self._m
        m.n_options += 1
        return f"{m.meeting_id}-o{m.n_options}"

    def _add_option(self, role_id: str, p: Proposal, check_authority: bool = True) -> Option | None:
        levers = self._normalize(list(p.levers))
        if check_authority:
            levers, rejected = authority.filter_proposal(self.roles[role_id], levers)
            for rj in rejected:
                log.info("rejected lever %s from %s: %s", rj.lever.kind, role_id, rj.reason)
                self._utter(role_id, "question", f"rejected lever {rj.lever.kind}: {rj.reason}")
            levers = self._validate_levers(role_id, levers)
        if not levers:
            if p.levers:  # everything it proposed was outside its authority
                self._utter(role_id, "question", f"option '{p.option_label}' dropped: no authorised levers")
            else:  # an empty proposal is "no alternative" (models often agree with the tabled option this way)
                self._utter(role_id, "question", f"no alternative: {p.rationale.strip()[:200]}" if p.rationale
                            else "no alternative tabled")
            return None
        levers = self._with_standing_plan(levers)
        key = spec_hash(levers, self._day)
        dup = next((o for o in self._m.active() if spec_hash(o.levers, self._day) == key), None)
        if dup is not None:
            self._utter(role_id, "question", f"option '{p.option_label}' is the same as "
                        f"{self._disp_opt(dup.option_id)}; not added", option_id=dup.option_id)
            return None
        o = Option(option_id=self._new_option_id(), label=p.option_label[:80], levers=levers,
                   proposed_by=role_id, rationale=p.rationale)
        self._m.options.append(o)
        self.store.write_option(self.run_id, self._m.meeting_id, o.option_id, o.levers, role_id)
        self._utter(role_id, "proposal", f"{o.label}: {p.rationale}".strip()[:600], option_id=o.option_id)
        return o

    # ================================================================== lever validation
    def _lever_error(self, lv: c.Lever) -> str | None:
        """Why the floor would reject this lever's ids/values (None when valid)."""
        k = lv.kind
        if k == "reslot_tonight":
            bad = [z for z in list(lv.include_zones) + list(lv.exclude_zones) if not (1 <= int(z) <= 6)]
            if bad:
                return f"zones {bad} are not zones (expected 1-6)"
        elif k == "protect":
            if lv.target == "zone":
                bad = [i for i in lv.ids if not (str(i).isdigit() and 1 <= int(i) <= 6)]
                what = "zone id {!r} is not a zone (expected 1-6)"
            elif lv.target == "customer":
                bad = [i for i in lv.ids if not re.fullmatch(r"C\d{3}", str(i))]
                what = "customer id {!r} is not a customer (expected C001-C200)"
            else:
                bad = [i for i in lv.ids if not re.fullmatch(r"S\d{4}", str(i))]
                what = "sku id {!r} is not a sku (expected S0001-S9999)"
            if not lv.ids:
                return f"no {lv.target} ids"
            if bad:
                return what.format(bad[0])
        elif k in ("close_aisle", "reopen_aisle"):
            if not (1 <= int(lv.aisle) <= 28):
                return f"aisle {lv.aisle} is not an aisle (expected 1-28)"
        elif k in ("labor_allocate", "overtime"):
            if lv.pool not in POOLS:
                return f"pool {lv.pool!r} is not a pool"
            if lv.shift not in SHIFTS:
                return f"shift {lv.shift!r} is not a shift"
            if k == "labor_allocate":
                cap = POOL_HEADCOUNT_CAP.get(lv.pool, 40)
                if lv.headcount < 1:
                    return (f"headcount 0 would empty the {lv.pool} pool (the hero run zeroed its night "
                            "crew this way); use pause_reslot to stop moves")
                if lv.headcount > cap:
                    return f"headcount {lv.headcount} over the {lv.pool} roster cap ({cap})"
        elif k == "resize_slots":
            bad = [i for i in lv.skus if not re.fullmatch(r"S\d{4}", str(i))]
            if not lv.skus:
                return "no skus"
            if bad:
                return f"sku id {bad[0]!r} is not a sku (expected S0001-S9999)"
            if not (1 <= lv.days_cover <= 10):
                return f"days_cover {lv.days_cover:g} out of range (1-10)"
        elif k == "hire_temps":
            cap = int(self.profile.budgets.get("temps_max", TEMPS_MAX_DEFAULT))
            if lv.count > cap:
                return f"count {lv.count} over the temps budget ({cap})"
        return None

    def _validate_levers(self, role_id: str, levers: list[c.Lever], option_id: str | None = None) -> list[c.Lever]:
        """Drop levers whose ids/values the floor would reject, with an utterance each."""
        kept = []
        for lv in levers:
            err = self._lever_error(lv)
            if err is None:
                kept.append(lv)
            else:
                self._utter(role_id, "question", f"rejected lever {lv.kind}: {err}"[:600], option_id=option_id)
        return kept

    # ================================================================== code options after evidence
    def _make_room(self, keep: tuple = ()) -> bool:
        """At the option cap, replace the lowest-scored non-engineer option. False if nothing can go."""
        m = self._m
        if len(m.active()) < MAX_OPTIONS:
            return True
        victims = [o for o in m.active() if o.proposed_by != self.engineer_id and o not in keep]
        if not victims:
            return False
        victim = min(victims, key=lambda o: (o.total, o.option_id))
        victim.replaced = True
        self._utter(self.chair_id, "question", f"{self._disp_opt(victim.option_id)} replaced by a code option "
                    f"(cap {MAX_OPTIONS} options)", option_id=victim.option_id)
        return True

    def _code_option(self, role_id: str, label: str, levers: list[c.Lever], rationale: str,
                     twin: Any, state: Any, events: list[Event], keep: tuple = ()) -> Option | None:
        if not self._make_room(keep):
            return None
        o = self._add_option(role_id, Proposal(option_label=label, levers=levers, rationale=rationale),
                             check_authority=False)
        if o is not None:
            self._run_evidence(o, twin, state, events)
        return o

    @staticmethod
    def _moves(o: Option) -> int:
        return sum(lv.n_moves for lv in o.levers if lv.kind == "reslot_tonight")

    def _smaller_batch(self, twin: Any, state: Any, events: list[Event]) -> None:
        """Every reslot option breaches safety_risk_max / pm_peak_zone_load and nothing feasible
        moves stock: table the engineer's option with n_moves halved (min 50), once more if needed."""
        m = self._m
        act = m.active()
        reslots = [o for o in act if any(lv.kind == "reslot_tonight" for lv in o.levers)]
        if not reslots or any(o.feasible and self._moves(o) > 0 for o in act):
            return
        load = ("safety_risk_max", "pm_peak_zone_load")
        if not all(o.feas and any(b in o.feas.breaches for b in load) for o in reslots):
            return
        base = next((o for o in reslots if o.proposed_by == self.engineer_id), reslots[0])
        rs = next(lv for lv in base.levers if lv.kind == "reslot_tonight")
        who = "analytics" if "analytics" in self.roles else self.chair_id
        prev, cur, n = base, None, rs.n_moves
        for _ in range(2):
            n2 = max(HALF_BATCH_MIN, n // 2)
            if n2 >= n:
                break
            levers = [lv.model_copy(update={"n_moves": n2}) if lv.kind == "reslot_tonight" else lv
                      for lv in base.levers]
            key = spec_hash(levers, m.day)
            dup = next((o for o in m.active() if spec_hash(o.levers, m.day) == key), None)
            if dup is not None:  # someone already tabled that batch: judge it, do not re-run
                o = dup
            else:
                if cur is not None:
                    cur.replaced = True
                o = self._code_option(who, f"reslot {n2} legs (code: smaller batch after breach)", levers,
                                      f"{n} legs breach a hard limit; same plan with {n2} legs.", twin, state,
                                      events, keep=(base,))
                if o is None:
                    break
                cur = o
            name = next(b for b in load if b in prev.feas.breaches)
            limit = self.profile.hard_limits.get(name, 1.0)
            self._utter(who, "question", f"{n} legs breach {name} {getattr(prev.whatif, name):.2f} > {limit:.2f}; "
                        f"trying {n2} legs: {getattr(o.whatif, name):.2f}, "
                        + ("feasible" if o.feasible else "still infeasible"), [o.evidence_id], o.option_id)
            if o.feasible or not (o.feas and any(b in o.feas.breaches for b in load)):
                return
            prev, n = o, n2

    def _ensure_pause(self, twin: Any, state: Any, events: list[Event]) -> None:
        """No tabled option is feasible: always table the code pause option."""
        if any(o.feasible for o in self._m.active()) or self._pause_option() is not None:
            return
        self._code_option(self.chair_id, CODE_PAUSE_LABEL, [PauseReslot(days=1)],
                          "Code-generated: no tabled option is feasible.", twin, state, events)

    def _pause_option(self) -> Option | None:
        return next((o for o in self._m.active() if o.levers and all(lv.kind == "pause_reslot" for lv in o.levers)),
                    None)

    def _map_ref_pick(self, oid: str | None) -> str | None:
        """A pick of REF / "do nothing" / "none" means the pause option (if tabled)."""
        raw = (oid or "").strip().strip("`").lower()
        if raw in REF_PICKS or (self._m.ref_id and self._store_ev(raw) == self._m.ref_id):
            p = self._pause_option()
            return p.option_id if p else oid
        return oid

    # ================================================================== positions
    def _options_text(self, with_scores: bool = False) -> str:
        m = self._m
        lines = [f"REF `{self._disp_ev(m.ref_id)}`: do nothing (the reference every option is scored against)"]
        for o in m.active():
            k = o.whatif.kpis_total if o.whatif else None
            s = (f"- `{self._disp_opt(o.option_id)}` \"{o.label}\" by {self.roles[o.proposed_by].name}: "
                 + ", ".join(lever_str(lv) for lv in o.levers))
            if k is not None:
                ref_k = m.ref.kpis_total if getattr(m, "ref", None) is not None else None
                dprog = (f" ({k.reslot_progress_pct - ref_k.reslot_progress_pct:+.1f} pp vs REF)"
                         if ref_k is not None else "")
                s += (f" | evidence `{self._disp_ev(o.evidence_id)}`: on_time {k.on_time_pct:.1f}%, mispick {k.mispick_rate:.2f}%, "
                      f"lph {k.lph:.1f}, progress {k.reslot_progress_pct:.1f}%{dprog}, cost ${k.labor_cost_usd:,.0f}, "
                      f"exp near-misses {o.whatif.expected_near_misses:.2f}, safety_risk {o.whatif.safety_risk_max:.2f}, "
                      f"pm_peak_zone_load {o.whatif.pm_peak_zone_load:.2f}")
            if o.feasible:
                s += " | feasible"
            else:  # value vs limit, not the breach amount (Luke once read "{'safety_risk_max': 0.1}" as the limit)
                parts = []
                for name, amount in (o.feas.breaches if o.feas else {}).items():
                    val = getattr(o.whatif, name, None) if o.whatif is not None else None
                    limit = self.profile.hard_limits.get(name)
                    parts.append(f"{name} {val:.2f} > limit {limit:.2f}" if val is not None and limit is not None
                                 else f"{name} over by {amount:.2f}")
                s += " | INFEASIBLE: " + "; ".join(parts)
            if with_scores and o.score is not None:
                s += f" | score {o.score.total:+.4f}"
            if o.vetoed_by:
                s += f" | VETOED by {self.roles[o.vetoed_by].name}"
            if o.notes:
                s += " | " + "; ".join(o.notes)
            lines.append(s)
        return "\n".join(lines)

    def _default_option(self) -> Option | None:
        act = self._m.active()
        return next((o for o in act if o.proposed_by == self.engineer_id), act[0] if act else None)

    def _fallback_position(self) -> Position:
        o = self._default_option()
        return Position(option_id=o.option_id if o else "", stance="support", rationale="(fallback)",
                        evidence_ids=[o.evidence_id] if o and o.evidence_id else [])

    def _position_error(self, p: Position) -> str | None:
        if self._m.get(p.option_id) is None:
            return f"unknown option_id {p.option_id!r}"
        if not p.evidence_ids:
            return "no evidence_ids cited"
        bad = [e for e in p.evidence_ids if e not in self.known_evidence]
        if bad:
            return f"unknown evidence ids {bad}"
        return None

    def _position(self, role_id: str) -> tuple[str, Position, Veto | Lock | None]:
        role = self.roles[role_id]
        if role.power == "veto":
            schema, wrap = SafetyReply, (lambda p: SafetyReply(position=p, veto=None))
            extra_help = ('Reply JSON {"position": {...}, "veto": null} or with "veto": {"option_id": "...", '
                          '"rule": "...", "evidence_ids": ["..."]}. Veto only an option whose what-if breaches '
                          "a safety limit or raises risk vs REF.")
        elif role.power == "lock":
            schema, wrap = QualityReply, (lambda p: QualityReply(position=p, lock=None))
            extra_help = ('Reply JSON {"position": {...}, "lock": null} or with "lock": {"target": '
                          '"zone|skus|customer", "ids": ["..."], "days": 1-3, "reason": "..."}.')
        else:
            schema, wrap = Position, (lambda p: p)
            extra_help = "Reply JSON with the Position fields only."
        known = [self._disp_ev(e) for e in
                 sorted(self.known_evidence & {o.evidence_id for o in self._m.active()} | {self._m.ref_id})]
        task = (f"## Options on the table ({self._h(self._day)}-day what-ifs on forecast demand)\n{self._options_text()}\n\n"
                "Give your position on the ONE option that matters most to your KPIs: "
                '{"option_id": "...", "stance": "support|oppose|amend", "amendment": null or [levers], '
                '"rationale": "<= 60 words", "evidence_ids": ["..."]}. '
                f"Cite at least one evidence id from: {', '.join(known)}. An amendment may use only your levers:\n"
                f"{self._lever_help(role_id)}\n{extra_help}")
        fb_pos = self._fallback_position()
        out = self._ask(role_id, schema, task, wrap(fb_pos))
        first_lock = getattr(out, "lock", None) if schema is QualityReply else None
        pos = self._norm_pos(out if schema is Position else out.position)
        err = self._position_error(pos)
        if err:
            out = self._ask(role_id, schema, task + f"\n\nYour previous answer was rejected ({self._scrub(err)}). "
                            "Cite evidence or support: use only the evidence ids listed above.", wrap(fb_pos))
            pos = self._norm_pos(out if schema is Position else out.position)
            err = self._position_error(pos)
            if err:
                self._log_fallback(role_id, f"position rejected twice: {err}")
                # Quality's lock is her own authority: a valid lock survives a bad position.
                lk = (getattr(out, "lock", None) or first_lock) if schema is QualityReply else None
                return role_id, fb_pos, lk
        extra = getattr(out, "veto", None) if schema is SafetyReply else getattr(out, "lock", None) \
            if schema is QualityReply else None
        return role_id, pos, extra

    def _record_position(self, day: int, role_id: str, p: Position) -> None:
        self._m.positions[role_id] = p
        text = f"{p.stance}: {p.rationale}"
        if p.stance == "amend" and p.amendment:
            text += " | amendment: " + ", ".join(lever_str(lv) for lv in p.amendment)
        self._utter(role_id, "position", text[:800], p.evidence_ids, p.option_id)
        if not _is_fallback(p):  # an LLM fallback is not a stance worth remembering
            self.stances.add(role_id, day, self._disp_opt(p.option_id), p.stance, p.rationale)

    def _handle_veto(self, role_id: str, v: Veto) -> None:
        v = v.model_copy(update={"option_id": self._store_opt(v.option_id) or "",
                                 "evidence_ids": [self._store_ev(e) for e in v.evidence_ids]})
        o, ref = self._m.get(v.option_id), self._m.ref
        evid = [e for e in v.evidence_ids if e in self.known_evidence]
        if o is None or o.whatif is None:
            self._utter(role_id, "veto", f"veto on {v.option_id} overruled: unknown option", evid)
            return
        if not authority.veto_allowed(self.roles[role_id]):
            self._utter(role_id, "veto", f"veto on {o.option_id} overruled: {role_id} has no veto power", evid,
                        o.option_id)
            return
        breach = (not o.feasible
                  or o.whatif.safety_risk_max > ref.safety_risk_max + EPS
                  or o.whatif.expected_near_misses > ref.expected_near_misses + EPS)
        if breach:
            o.vetoed_by = role_id
            self._utter(role_id, "veto", f"veto {o.option_id}: {v.rule}"[:600], evid or [o.evidence_id], o.option_id)
        else:
            self._utter(role_id, "veto", f"veto on {o.option_id} ({v.rule}) overruled: no evidence of breach"[:600],
                        evid or [o.evidence_id], o.option_id)

    def _handle_lock(self, role_id: str, lk: Lock) -> None:
        role = self.roles[role_id]
        if role.power != "lock":
            self._utter(role_id, "lock", f"lock {lk.target} {lk.ids} not applied: {role_id} has no lock power")
            return
        days = lk.days
        if days > LOCK_MAX_DAYS:  # her own authority covers 3 days: clamp, still apply
            days = LOCK_MAX_DAYS
            self._utter(role_id, "lock", f"lock clamped to {LOCK_MAX_DAYS} days (Quality alone)")
        if not authority.lock_allowed(role, days):
            self._utter(role_id, "lock", f"lock {lk.target} {lk.ids} for {days}d not applied ({lk.reason})"[:600])
            return
        try:
            pr = Protect(target=lk.target, ids=[str(i) for i in lk.ids], days=days)
        except ValidationError as e:
            self._utter(role_id, "lock", f"lock not applied: invalid ({str(e)[:200]})")
            return
        err = self._lever_error(pr)
        if err:
            self._utter(role_id, "lock", f"lock not applied: {err}"[:600])
            return
        self._m.lock_levers.append(pr)
        changed = []
        for o in self._m.active():
            if pr not in o.levers:
                o.levers = list(o.levers) + [pr]
                self._update_option(o, levers=o.levers)
                changed.append(o)
        self._utter(role_id, "lock", f"lock {lk.target} {', '.join(pr.ids)} for {days}d: {lk.reason}"[:600])
        # Re-run the evidence so `predicted` and the scores include the Protect lever (<= 3 what-ifs).
        for o in changed:
            if self._lock_reruns >= MAX_OPTIONS or self._twin is None:
                break
            self._lock_reruns += 1
            self._run_evidence(o, self._twin, self._state, self._events)

    # ================================================================== amend
    @staticmethod
    def _merge(base: list[c.Lever], amendment: list[c.Lever]) -> list[c.Lever]:
        """Amendment levers replace the same kind in the base, else append; the result is normalised so
        the last word wins between mutually exclusive levers (an amendment "pause" removes the base
        "reslot", and vice versa) rather than producing "reslot 200 legs; pause reslot"."""
        out = list(base)
        for lv in amendment:
            idx = [i for i, x in enumerate(out) if x.kind == lv.kind]
            if idx:
                out[idx[0]] = lv
                out = [x for i, x in enumerate(out) if i not in idx[1:]]
            else:
                out.append(lv)
        return OrgPolicy._normalize(out)

    def _with_standing_plan(self, levers: list[c.Lever]) -> list[c.Lever]:
        """An option that neither moves nor pauses (resize_slots, labor_allocate, protect alone) is
        additive to tonight's standing plan, which continues (spec 10.5); the first real-twin hero run
        lost 8 nights to such options because they silently replaced the reslot."""
        return with_standing_plan(levers, self.standing_budget, self.standing_order,
                                  paused=self.paused_until >= self._day)

    @staticmethod
    def _normalize(levers: list[c.Lever]) -> list[c.Lever]:
        """Resolve contradictory lever combinations: `pause_reslot` excludes `reslot_tonight` and
        `staging_clearance` (the later one in the list wins); duplicates of a kind keep the last."""
        out: list[c.Lever] = []
        for lv in levers:
            out = [x for x in out if x.kind != lv.kind]
            if lv.kind == "pause_reslot":
                out = [x for x in out if x.kind not in ("reslot_tonight", "staging_clearance")]
            elif lv.kind in ("reslot_tonight", "staging_clearance"):
                out = [x for x in out if x.kind != "pause_reslot"]
            out.append(lv)
        return out

    def _amend(self, twin: Any, state: Any, events: list[Event]) -> None:
        m = self._m
        created: list[Option] = []
        for role_id in sorted(m.positions):
            p = m.positions[role_id]
            if p.stance != "amend" or not p.amendment:
                continue
            base = m.get(p.option_id)
            if base is None:
                continue
            kept, rejected = authority.filter_proposal(self.roles[role_id], list(p.amendment))
            for rj in rejected:
                self._utter(role_id, "question", f"rejected lever {rj.lever.kind}: {rj.reason}", option_id=base.option_id)
            kept = self._validate_levers(role_id, kept, base.option_id)
            if not kept:
                continue
            levers = self._merge(base.levers, kept)
            if any(spec_hash(o.levers, m.day) == spec_hash(levers, m.day) for o in m.active()):
                continue
            if self._amendments >= MAX_AMENDMENTS:
                self._utter(role_id, "question", f"amendment dropped: cap {MAX_AMENDMENTS} amendments per meeting",
                            option_id=base.option_id)
                continue
            self._amendments += 1
            if len(m.active()) >= MAX_OPTIONS:
                victims = [o for o in m.active() if o.proposed_by != self.engineer_id and o not in created]
                if not victims:
                    self._utter(role_id, "question", "amendment dropped: option cap reached", option_id=base.option_id)
                    continue
                victim = min(victims, key=lambda o: (o.total, o.option_id))
                victim.replaced = True
                self._utter(self.chair_id, "question", f"{victim.option_id} replaced by an amendment from "
                            f"{self.roles[role_id].name} (cap {MAX_OPTIONS} options)", option_id=victim.option_id)
            o = Option(option_id=self._new_option_id(), label=f"{base.label} (amended by {self.roles[role_id].name})"[:80],
                       levers=levers, proposed_by=role_id, rationale=p.rationale)
            m.options.append(o)
            created.append(o)
            self.store.write_option(self.run_id, m.meeting_id, o.option_id, o.levers, role_id)
            self._utter(role_id, "proposal", f"amend {base.option_id}: " + ", ".join(lever_str(x) for x in kept),
                        p.evidence_ids, o.option_id)
            self._run_evidence(o, twin, state, events)

    def _overtime_remaining(self, day: int) -> float:
        budget = float(self.profile.budgets.get("overtime_hours_per_week", math.inf))
        return budget - self.overtime_used.get((day - 1) // 7, 0.0)

    def _capacity_check(self, day: int) -> None:
        """Shift Lead's capacity truth: overtime beyond the remaining weekly budget needs the director."""
        rem = self._overtime_remaining(day)
        for o in self._m.active():
            hours = sum(lv.hours for lv in o.levers if lv.kind == "overtime")
            if hours > rem + EPS:
                note = f"needs director: overtime {hours:g}h > {rem:g}h remaining this week"
                if note not in o.notes:
                    o.notes.append(note)

    # ================================================================== decide
    def _positions_text(self) -> str:
        rows = [f"- {self.roles[r].name} ({r}): no position (LLM fallback)" if _is_fallback(p) else
                f"- {self.roles[r].name} ({r}): {p.stance} {self._disp_opt(p.option_id)} - {p.rationale[:160]}"
                for r, p in sorted(self._m.positions.items())]
        return "\n".join(rows) or "- (none)"

    def _chair_decide(self, day: int) -> tuple[Option, str, list[str], bool, list[str]]:
        m = self._m
        act = m.active()
        non_vetoed = [o for o in act if not o.vetoed_by]
        eligible = [o for o in non_vetoed if o.feasible]
        ranked = sorted(eligible, key=lambda o: (-o.total, o.option_id))
        top = ranked[0] if ranked else None
        pause = self._pause_option()
        fb_opt = top or pause or (act[0] if act else None)
        task = (f"## Options (code-computed scores under profile {self.profile.id}; {self._h(day)}-day what-ifs)\n"
                f"{self._options_text(with_scores=True)}\n\n## Positions\n{self._positions_text()}\n\n"
                "Pick one option. Rules: a vetoed or infeasible option cannot be chosen; picking a feasible option "
                "other than the top score needs a rationale (logged as a deviation); escalate to the Director when "
                "every option is vetoed or infeasible, the top two are within 2%, or a lever needs budget approval.\n"
                "Feasibility is already checked: a hard limit no option breaches is not a reason. A paused night "
                "delays the whole reslot; on a tie prefer the option with more progress unless a specialist gave an "
                "evidence-backed reason (breach, veto, lock, no labor to absorb moves).\n"
                'Reply JSON {"option_id": "...", "escalate": false, "rationale": "<= 60 words", "evidence_ids": ["..."]}.')
        fb = ChairDecision(option_id=fb_opt.option_id if fb_opt else None, escalate=False,
                           rationale="(fallback) pause reslot" if pause is not None and fb_opt is pause
                           else "(fallback) top feasible score",
                           evidence_ids=[fb_opt.evidence_id] if fb_opt and fb_opt.evidence_id else [])
        dec = self._ask(self.chair_id, ChairDecision, task, fb, tier="ultra", reasoning=True, max_tokens=1500)
        dec = dec.model_copy(update={"option_id": self._store_opt(self._map_ref_pick(dec.option_id)),
                                     "evidence_ids": [self._store_ev(e) for e in dec.evidence_ids]})
        rationale = (dec.rationale or "").strip()
        reasons: list[str] = []
        deviates = False
        if not non_vetoed:
            reasons.append("every option vetoed")
            chosen = act[least_bad([o.feas for o in act])]
        elif not eligible:
            reasons.append("every option infeasible: least-bad taken")
            chosen = non_vetoed[least_bad([o.feas for o in non_vetoed])]
        else:
            pick = m.get(dec.option_id)
            if pick is None or pick not in eligible:
                if dec.option_id:
                    self._utter(self.chair_id, "question", f"pick {dec.option_id} is not allowed (vetoed, infeasible "
                                "or unknown); taking the top score", option_id=dec.option_id)
                chosen = top
            elif pick is not top and not rationale:
                chosen = top
            else:
                chosen = pick
            deviates = chosen is not top
            if len(ranked) >= 2:
                s1, s2 = ranked[0].total, ranked[1].total
                same_outcome = (ranked[0].whatif is not None and ranked[1].whatif is not None
                                and ranked[0].whatif.kpis_total == ranked[1].whatif.kpis_total)
                if same_outcome:  # two ways of doing the same thing is not a tie worth the Director
                    reasons_note = f"{self._disp_opt(ranked[1].option_id)} has the same outcome as the top option"
                    self._utter(self.chair_id, "question", reasons_note, option_id=ranked[1].option_id)
                elif abs(s1 - s2) <= TIE_FRACTION * max(abs(s1), abs(s2), TIE_FLOOR):
                    reasons.append(f"top-2 scores within 2% ({s1:+.4f} vs {s2:+.4f})")
        plan = authority.commit_plan(chosen.levers, self.roles, self.present, self._overtime_remaining(day))
        if plan.escalate:
            reasons.append("a lever needs the director")
        if dec.escalate:  # honoured only with a code-detected reason
            if reasons or "director_escalation" in getattr(self, "_kinds", set()):
                reasons.append("chair escalates")
            else:
                self._utter(self.chair_id, "question", "chair asked to escalate without a rule reason; deciding",
                            option_id=chosen.option_id)
        if not rationale:
            rationale = f"Top score under {self.profile.id}: {chosen.option_id}."
        chair_ev = [e for e in dec.evidence_ids if e in self.known_evidence]
        return chosen, rationale, reasons, deviates, chair_ev

    def _escalate(self, day: int, chosen: Option, rationale: str, reasons: list[str]) -> tuple[Option, str, str]:
        m = self._m
        dir_id = self.director_id
        self._utter(self.chair_id, "question", f"escalating to {self.roles[dir_id].name}: " + "; ".join(reasons)
                    + f". Provisional choice {chosen.option_id}: {rationale}"[:600], [chosen.evidence_id],
                    chosen.option_id)
        trigger = "veto_deadlock" if "every option vetoed" in reasons else "chair_escalates"
        self._hire(day, dir_id, trigger)
        self._refresh_board()
        locks = ", ".join(lever_str(lv) for lv in m.lock_levers) or "none"
        task = (f"## Escalation\nReasons: {'; '.join(reasons)}\nChair's provisional choice: {self._disp_opt(chosen.option_id)} "
                f"({rationale})\nQuality locks in force: {locks}\n\n## Options\n{self._options_text(with_scores=True)}"
                f"\n\n## Positions\n{self._positions_text()}\n\nRule on it. You may choose any option (you may override a "
                "veto), override a lock, or set the profile (customer_first | efficiency_first). "
                'Reply JSON {"option_id": "..." or null, "set_profile": null, "override_lock": null, "rationale": "..."}.')
        fb = DirectorRuling(option_id=chosen.option_id, set_profile=None, override_lock=None,
                            rationale="(fallback) the chair's choice stands")
        ruling = self._ask(dir_id, DirectorRuling, task, fb, tier="ultra", reasoning=True, max_tokens=1500)
        ruling = ruling.model_copy(update={"option_id": self._store_opt(self._map_ref_pick(ruling.option_id))})
        pick = m.get(ruling.option_id) if ruling.option_id else None
        if ruling.option_id and pick is None:
            self._utter(dir_id, "question", f"ruling names unknown option {ruling.option_id}; chair's choice stands")
        elif pick is not None:
            if not pick.feasible and any(o.feasible for o in m.active()):
                self._utter(dir_id, "question", f"{pick.option_id} breaches a hard limit while a feasible option "
                            "exists; chair's choice stands", [pick.evidence_id], pick.option_id)
            else:
                chosen = pick
        if ruling.override_lock:
            if m.lock_levers:
                chosen = Option(**{**chosen.__dict__, "levers": [lv for lv in chosen.levers if lv not in m.lock_levers]})
                self._utter(dir_id, "lock", f"director overrides the lock: {ruling.override_lock}"[:600],
                            option_id=chosen.option_id)
            else:
                log.info("override_lock %r ignored: no lock in force", ruling.override_lock)
        if ruling.set_profile:
            text = (f"Director set_profile {ruling.set_profile} on day {day} (recorded; profile switching "
                    "mid-run is not applied)")
            self.agreements.append(text)
            try:
                self.store.write_agreement(self.run_id, f"{self.run_id}-a{day}-profile", text,
                                           [dir_id, self.chair_id], day)
            except Exception:  # noqa: BLE001
                log.exception("could not write agreement")
        return chosen, (ruling.rationale or rationale).strip(), dir_id

    # ================================================================== commit
    def _commit(self, day: int, chosen: Option, rationale: str, decided_by: str, deviates: bool,
                chair_ev: list[str], reasons: list[str]) -> Decision:
        m = self._m
        levers = list(chosen.levers)
        rem = self._overtime_remaining(day)
        plan = authority.commit_plan(levers, self.roles, self.present, rem)
        for rid in plan.hires:
            mine = [lv for lv in levers
                    if authority.issuer_for(lv, self.roles, self.present, rem).needs_hire == rid]
            self._hire(day, rid, "commit: issue " + ", ".join(sorted({lv.kind for lv in mine})))
            task = (f"The chair approved {self._disp_opt(chosen.option_id)} \"{chosen.label}\". You are asked to issue: "
                    + ", ".join(lever_str(lv) for lv in mine)
                    + '. Accept or refuse with a reason. Reply JSON {"accept": true|false, "reason": "..."}.')
            acc = self._ask(rid, Accept, task, Accept(accept=True, reason="(fallback) accepted"))
            if acc.accept:
                self._utter(rid, "position", f"accepts to issue {', '.join(lv.kind for lv in mine)}: {acc.reason}"[:600],
                            [chosen.evidence_id], chosen.option_id)
            else:
                levers = [lv for lv in levers if not any(lv is x for x in mine)]
                self._utter(rid, "question", f"refuses to issue {', '.join(lv.kind for lv in mine)}: {acc.reason}"[:600],
                            [chosen.evidence_id], chosen.option_id)
        plan = authority.commit_plan(levers, self.roles, self.present, rem)
        issued = {id(lv) for lv, _ in plan.issuable}
        for lv, why in zip(levers, plan.reasons):
            if id(lv) not in issued:
                self._utter(self.chair_id, "question", f"dropped {lever_str(lv)}: {why}"[:600], option_id=chosen.option_id)
        final = [lv for lv, _ in plan.issuable]

        did = f"{self.run_id}-d{day}"
        stamped = [lv.model_copy(update={"decision_id": did}) for lv in final]
        decision = Decision(decision_id=did, day=day, levers=stamped, constraints=[], decided_by=decided_by,
                            rationale=rationale[:1000], option_id=chosen.option_id, meeting_id=m.meeting_id)
        self._attach(decision, chosen.whatif, deviates)
        ev = [chosen.evidence_id] + [e for e in chair_ev if e != chosen.evidence_id]
        self._utter(decided_by, "decision", f"{chosen.option_id}: {rationale}"[:800], ev, chosen.option_id)
        outcome = f"{chosen.option_id} {chosen.label} (decided by {self.roles[decided_by].name}"
        outcome += "; escalated: " + "; ".join(reasons) + ")" if reasons else ")"
        if deviates:
            outcome += " [deviates from top score]"
        self.store.set_meeting_outcome(self.run_id, m.meeting_id, outcome[:1000])

        self._apply_run_state(day, stamped)
        return decision

    def _attach(self, decision: Decision, whatif: WhatIf | None, deviates: bool) -> None:
        """`decision.predicted` (first what-if day) and `decision.deviates_from_top_score` as plain
        attributes outside the pydantic fields (see module docstring)."""
        self.last_whatif = whatif
        self.last_deviates = deviates
        pred = (whatif.kpis_by_day[0] if whatif.kpis_by_day else whatif.kpis_total) if whatif else None
        object.__setattr__(decision, "predicted", pred)
        object.__setattr__(decision, "deviates_from_top_score", deviates)

    # ================================================================== triage helpers
    def _code_agenda(self, new_events, violations, drift, inbox) -> list[str]:
        items = [f"{e.kind} (day {e.day})" for e in new_events]
        items += [f"KPI out of band: {v}" for v in violations] + [f"drift: {d}" for d in drift]
        items += [f"inbox: {t[:80]}" for t in inbox]
        items += [t[:100] for t in self._rejected]
        return items[:6] or ["review standing plan"]

    def _triage_task(self, new_events, violations, drift, inbox, fired) -> str:
        callable_roles = sorted(r for r, role in self.roles.items()
                                if role.attends == "on_trigger" and r not in self.present and r != self.director_id)
        return ("Not a quiet day. Signals:\n"
                f"- new or announced events: {', '.join(f'{e.kind} day {e.day} {e.args}' for e in new_events) or 'none'}\n"
                f"- yesterday KPIs out of band: {', '.join(violations) or 'none'}\n"
                f"- drift flags: {'; '.join(drift) or 'none'}\n- inbox: {'; '.join(self._scrub(t) for t in inbox) or 'none'}\n"
                f"- floor rejections: {'; '.join(self._rejected) or 'none'}\n"

                f"- code hired on triggers: {', '.join(f'{r} ({t})' for r, t in sorted(fired.items())) or 'none'}\n"
                f"- on-call roles you may also call in: {', '.join(callable_roles) or 'none'}\n\n"
                "Set the agenda (at most 4 short items). "
                'Reply JSON {"meeting": true, "agenda": ["..."], "hire": [{"role": "...", "trigger": "..."}], '
                '"release": []}.')
