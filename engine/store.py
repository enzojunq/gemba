"""Run store (`runs.db`): spec 8.2 + CONTRACT section 7. SQLAlchemy Core over SQLite.

JSON columns are TEXT (json.dumps; pydantic models via `model_dump(mode="json")`).
Every insert carrying `caused_by` also writes a `cascade_edges` row (spec 9.4).

Additions beyond spec 8.2 (all nullable, needed by the engine): `day_results.snapshot_path`
(CONTRACT 7), `day_results.violations`, `decisions.rejections` (twin LeverRejections),
`cascade_edges.day` and `llm_calls.day` (so forks can copy rows "before fork_day"),
`llm_calls.kind/fallback_used/mode` (emitted by llm/client.py).
"""

from __future__ import annotations

import json
import os
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Iterable

from pydantic import BaseModel
from sqlalchemy import (
    Boolean, Column, Connection, Float, Integer, MetaData, String, Table, Text, create_engine,
    event, insert, or_, select, update,
)

import contracts as c

MODE_PREFIX = {"backtest": "bt", "fork": "fk", "eval": "ev", "retro": "rt"}


def dumps(x: Any) -> str | None:
    """JSON-encode anything, serializing pydantic models with model_dump(mode='json')."""
    if x is None:
        return None

    def default(o: Any) -> Any:
        if isinstance(o, BaseModel):
            return o.model_dump(mode="json")
        if isinstance(o, (set, tuple)):
            return list(o)
        raise TypeError(f"not JSON serializable: {type(o).__name__}")

    return json.dumps(x, default=default)


def loads(s: str | None) -> Any:
    return None if s is None else json.loads(s)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id(mode: str) -> str:
    return f"{MODE_PREFIX.get(mode, mode[:2])}-{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- schema

md = MetaData()
J = Text  # JSON stored as TEXT


def _t(name: str, *cols: Column) -> Table:
    return Table(name, md, *cols)


runs = _t("runs",
    Column("run_id", String, primary_key=True), Column("parent_run_id", String), Column("fork_day", Integer),
    Column("fork_decision", J), Column("scenario_id", String), Column("profile_id", String),
    Column("playbook_version", String), Column("org_spec_hash", String), Column("seed", Integer),
    Column("mode", String), Column("status", String), Column("started_at", String),
    Column("finished_at", String), Column("llm_usd", Float))
day_results = _t("day_results",
    Column("run_id", String, primary_key=True), Column("day", Integer, primary_key=True),
    Column("kpis", J), Column("hourly", J), Column("predicted", J), Column("violations", J),
    Column("snapshot_path", String))
events_log = _t("events_log",
    Column("run_id", String, primary_key=True), Column("event_id", String, primary_key=True),
    Column("day", Integer), Column("hour", Integer), Column("kind", String), Column("args", J),
    Column("source", String), Column("caused_by", String))
constraints = _t("constraints",
    Column("run_id", String, primary_key=True), Column("constraint_id", String, primary_key=True),
    Column("kind", String), Column("args", J), Column("owner_role", String), Column("day_start", Integer),
    Column("day_end", Integer), Column("active", Boolean), Column("caused_by", String))
agreements = _t("agreements",
    Column("run_id", String, primary_key=True), Column("agreement_id", String, primary_key=True),
    Column("text", Text), Column("parties", J), Column("day", Integer), Column("active", Boolean),
    Column("retired_reason", Text))
evidence = _t("evidence",
    Column("run_id", String, primary_key=True), Column("evidence_id", String, primary_key=True),
    Column("spec_hash", String), Column("spec", J), Column("horizon_days", Integer), Column("kpis", J),
    Column("scorecard", J), Column("created_day", Integer))
meetings = _t("meetings",
    Column("run_id", String, primary_key=True), Column("meeting_id", String, primary_key=True),
    Column("day", Integer), Column("kind", String), Column("attendees", J), Column("agenda", J),
    Column("outcome", Text))
utterances = _t("utterances",
    Column("run_id", String, primary_key=True), Column("meeting_id", String, primary_key=True),
    Column("seq", Integer, primary_key=True), Column("role", String), Column("kind", String),
    Column("text", Text), Column("evidence_ids", J), Column("option_id", String))
options = _t("options",
    Column("run_id", String, primary_key=True), Column("meeting_id", String),
    Column("option_id", String, primary_key=True), Column("levers", J), Column("proposed_by", String),
    Column("evidence_id", String), Column("score", Float), Column("feasible", Boolean),
    Column("vetoed_by", String))
decisions = _t("decisions",
    Column("run_id", String, primary_key=True), Column("decision_id", String, primary_key=True),
    Column("day", Integer), Column("option_id", String), Column("levers", J), Column("decided_by", String),
    Column("rationale", Text), Column("deviates_from_top_score", Boolean), Column("caused_by", String),
    Column("rejections", J))
hires = _t("hires",
    Column("run_id", String), Column("day", Integer), Column("role", String), Column("trigger", Text),
    Column("released_day", Integer))
checks = _t("checks",
    Column("run_id", String), Column("day", Integer), Column("check_name", String), Column("scope", String),
    Column("passed", Boolean), Column("value", Float), Column("threshold", Float))
drift_flags = _t("drift_flags",
    Column("run_id", String), Column("day", Integer), Column("kind", String), Column("detail", Text),
    Column("response", String))
cascade_edges = _t("cascade_edges",
    Column("run_id", String), Column("from_id", String), Column("to_id", String), Column("kind", String),
    Column("day", Integer))
llm_calls = _t("llm_calls",
    Column("call_id", String), Column("run_id", String), Column("day", Integer), Column("role", String),
    Column("model", String), Column("prompt_hash", String), Column("request", J), Column("response", J),
    Column("tokens_in", Integer), Column("tokens_out", Integer), Column("cached_tokens", Integer),
    Column("latency_ms", Float), Column("usd", Float), Column("from_cache", Boolean),
    Column("kind", String), Column("fallback_used", Boolean), Column("mode", String))
playbook_rules = _t("playbook_rules",
    Column("version", String), Column("rule_id", String), Column("role", String), Column("tags", J),
    Column("trigger", Text), Column("advice", Text), Column("evidence", J), Column("score_delta", Float),
    Column("status", String))
eval_results = _t("eval_results",
    Column("eval_id", String), Column("scenario_id", String), Column("profile_id", String),
    Column("contender", String), Column("playbook_version", String), Column("seed", Integer),
    Column("metrics", J))
inbox = _t("inbox",
    Column("run_id", String, primary_key=True), Column("msg_id", String, primary_key=True),
    Column("day_hint", Integer), Column("text", Text), Column("received_at", String),
    Column("consumed_day", Integer))

JSON_COLS = {
    "fork_decision", "kpis", "hourly", "predicted", "violations", "args", "parties", "spec", "scorecard",
    "attendees", "agenda", "evidence_ids", "levers", "rejections", "request", "response", "tags",
    "evidence", "metrics",
}

# (table, day column) copied by copy_run_prefix; utterances/options follow their meeting.
_DAY_TABLES = [
    (day_results, "day"), (events_log, "day"), (constraints, "day_start"), (agreements, "day"),
    (evidence, "created_day"), (meetings, "day"), (decisions, "day"), (hires, "day"), (checks, "day"),
    (drift_flags, "day"), (cascade_edges, "day"), (llm_calls, "day"), (inbox, "consumed_day"),
]


def _decode(k: str, v: Any) -> Any:
    """JSON-decode known JSON columns. `drift_flags.response` is plain text but shares its
    name with the JSON `llm_calls.response`, so a value that isn't JSON is returned as-is."""
    if k not in JSON_COLS or not isinstance(v, str):
        return v
    try:
        return loads(v)
    except ValueError:
        return v


def _row(r: Any) -> dict:
    return {k: _decode(k, v) for k, v in r._mapping.items()}


def _causes(caused_by: str | Iterable[str] | None) -> list[str]:
    if caused_by is None:
        return []
    return [caused_by] if isinstance(caused_by, str) else [x for x in caused_by if x]


class RunStore:
    def __init__(self, url: str | None = None):
        self.url = url or os.environ.get("RUN_STORE_URL", "sqlite:///runs.db")
        self.engine = create_engine(self.url)
        if self.url.startswith("sqlite"):
            event.listen(self.engine, "connect", _sqlite_pragmas)

    def create_schema(self) -> None:
        md.create_all(self.engine)

    # ------------------------------------------------------------------ internals
    def _tx(self, conn: Connection | None):
        """Use the caller's connection (inside its transaction) or open a new one."""
        return _Reuse(conn) if conn is not None else self.engine.begin()

    @staticmethod
    def _edges(conn: Connection, run_id: str, caused_by, to_id: str, kind: str, day: int | None) -> None:
        rows = [dict(run_id=run_id, from_id=f, to_id=to_id, kind=kind, day=day) for f in _causes(caused_by)]
        if rows:
            conn.execute(insert(cascade_edges), rows)

    def _event(self, conn: Connection, run_id: str, e: c.Event, write_day: int) -> None:
        exists = conn.execute(select(events_log.c.event_id).where(
            events_log.c.run_id == run_id, events_log.c.event_id == e.event_id)).first()
        if exists:
            return
        conn.execute(insert(events_log).values(
            run_id=run_id, event_id=e.event_id, day=e.day, hour=e.hour, kind=e.kind, args=dumps(e.args),
            source=e.source, caused_by=e.caused_by))
        self._edges(conn, run_id, e.caused_by, c.node_id(c.EVT, e.event_id), e.source, write_day)

    # ------------------------------------------------------------------ runs
    def create_run(self, mode: str = "backtest", scenario_id: str | None = None, profile_id: str | None = None,
                   playbook_version: str | None = None, seed: int | None = None, *, run_id: str | None = None,
                   parent_run_id: str | None = None, fork_day: int | None = None, fork_decision: Any = None,
                   org_spec_hash: str | None = None, status: str = "running") -> str:
        run_id = run_id or new_run_id(mode)
        with self.engine.begin() as conn:
            conn.execute(insert(runs).values(
                run_id=run_id, parent_run_id=parent_run_id, fork_day=fork_day, fork_decision=dumps(fork_decision),
                scenario_id=scenario_id, profile_id=profile_id, playbook_version=playbook_version,
                org_spec_hash=org_spec_hash, seed=seed, mode=mode, status=status, started_at=_now(), llm_usd=0.0))
        return run_id

    def set_status(self, run_id: str, status: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(runs).where(runs.c.run_id == run_id).values(status=status))

    def finish_run(self, run_id: str, status: str = "done", llm_usd: float | None = None) -> None:
        vals: dict = dict(status=status, finished_at=_now())
        if llm_usd is not None:
            vals["llm_usd"] = llm_usd
        with self.engine.begin() as conn:
            conn.execute(update(runs).where(runs.c.run_id == run_id).values(**vals))

    # ------------------------------------------------------------------ the day transaction
    def write_day(self, run_id: str, day: int, decision: c.Decision, rejections: list[c.LeverRejection],
                  result: c.DayResult, predicted: c.Kpis | None = None, events: list[c.Event] = (),
                  new_consequences: list[c.Event] = (), checks: list[dict] = (), drift_flags: list[dict] = (),
                  snapshot_path: str | None = None, *, deviates_from_top_score: bool = False) -> None:
        """All of one simulated day's rows in a single transaction (CONTRACT 7)."""
        with self.engine.begin() as conn:
            for e in events:
                self._event(conn, run_id, e, day)
            dec_caused = c.node_id(c.MTG, decision.meeting_id) if decision.meeting_id else None
            conn.execute(insert(decisions).values(
                run_id=run_id, decision_id=decision.decision_id, day=day, option_id=decision.option_id,
                levers=dumps(decision.levers), decided_by=decision.decided_by, rationale=decision.rationale,
                deviates_from_top_score=deviates_from_top_score, caused_by=dec_caused, rejections=dumps(rejections)))
            self._edges(conn, run_id, dec_caused, c.node_id(c.DEC, decision.decision_id), "decision", day)
            for con in decision.constraints:
                self.write_constraint(run_id, con, owner_role=decision.decided_by,
                                      caused_by=c.node_id(c.DEC, decision.decision_id), conn=conn, day=day)
            conn.execute(insert(day_results).values(
                run_id=run_id, day=day, kpis=dumps(result.kpis), hourly=dumps(result.hourly),
                predicted=dumps(predicted), violations=dumps(result.violations), snapshot_path=snapshot_path))
            for e in new_consequences:
                self._event(conn, run_id, e, day)
            if checks:
                conn.execute(insert(md.tables["checks"]), [dict(ch, run_id=run_id, day=day) for ch in checks])
            if drift_flags:
                conn.execute(insert(md.tables["drift_flags"]), [dict(f, run_id=run_id, day=day) for f in drift_flags])

    # ------------------------------------------------------------------ smaller writers (org lane)
    def write_meeting(self, run_id: str, meeting_id: str, day: int, kind: str, attendees: list[str],
                      agenda: Any, outcome: str | None = None, caused_by: str | list[str] | None = None,
                      conn: Connection | None = None) -> None:
        """`caused_by`: the event node ids on the agenda (one edge each)."""
        with self._tx(conn) as cx:
            cx.execute(insert(meetings).values(run_id=run_id, meeting_id=meeting_id, day=day, kind=kind,
                                               attendees=dumps(attendees), agenda=dumps(agenda), outcome=outcome))
            self._edges(cx, run_id, caused_by, c.node_id(c.MTG, meeting_id), "meeting", day)

    def set_meeting_outcome(self, run_id: str, meeting_id: str, outcome: str, conn: Connection | None = None) -> None:
        with self._tx(conn) as cx:
            cx.execute(update(meetings).where(meetings.c.run_id == run_id, meetings.c.meeting_id == meeting_id)
                       .values(outcome=outcome))

    def write_utterance(self, run_id: str, meeting_id: str, seq: int, role: str, kind: str, text: str,
                        evidence_ids: list[str] = (), option_id: str | None = None,
                        conn: Connection | None = None) -> None:
        with self._tx(conn) as cx:
            cx.execute(insert(utterances).values(run_id=run_id, meeting_id=meeting_id, seq=seq, role=role, kind=kind,
                                                 text=text, evidence_ids=dumps(list(evidence_ids)), option_id=option_id))

    def write_option(self, run_id: str, meeting_id: str, option_id: str, levers: list, proposed_by: str,
                     evidence_id: str | None = None, score: float | None = None, feasible: bool | None = None,
                     vetoed_by: str | None = None, conn: Connection | None = None) -> None:
        with self._tx(conn) as cx:
            cx.execute(insert(options).values(run_id=run_id, meeting_id=meeting_id, option_id=option_id,
                                              levers=dumps(levers), proposed_by=proposed_by, evidence_id=evidence_id,
                                              score=score, feasible=feasible, vetoed_by=vetoed_by))

    def write_evidence(self, run_id: str, evidence_id: str, spec_hash: str, spec: Any, horizon_days: int,
                       kpis: Any, scorecard: Any = None, created_day: int | None = None,
                       conn: Connection | None = None) -> None:
        with self._tx(conn) as cx:
            cx.execute(insert(evidence).values(run_id=run_id, evidence_id=evidence_id, spec_hash=spec_hash,
                                               spec=dumps(spec), horizon_days=horizon_days, kpis=dumps(kpis),
                                               scorecard=dumps(scorecard), created_day=created_day))

    def write_hire(self, run_id: str, day: int, role: str, trigger: str, released_day: int | None = None,
                   conn: Connection | None = None) -> None:
        with self._tx(conn) as cx:
            cx.execute(insert(hires).values(run_id=run_id, day=day, role=role, trigger=trigger,
                                            released_day=released_day))

    def release_hire(self, run_id: str, role: str, released_day: int, conn: Connection | None = None) -> None:
        with self._tx(conn) as cx:
            cx.execute(update(hires).where(hires.c.run_id == run_id, hires.c.role == role,
                                           hires.c.released_day.is_(None)).values(released_day=released_day))

    def write_llm_call(self, row: dict | None = None, *, conn: Connection | None = None, **fields: Any) -> None:
        """Accepts the dict `llm.client` emits via `on_call` as-is (or the same keys as kwargs).
        Unknown keys are ignored; `mode` falls back to `request["mode"]`; `day` is optional."""
        r = {**(row or {}), **fields}
        if r.get("mode") is None and isinstance(r.get("request"), dict):
            r["mode"] = r["request"].get("mode")
        vals = {k: (dumps(v) if k in JSON_COLS else v) for k, v in r.items() if k in llm_calls.c}
        with self._tx(conn) as cx:
            cx.execute(insert(llm_calls).values(**vals))

    def write_constraint(self, run_id: str, constraint: c.Event, owner_role: str | None = None,
                         caused_by: str | None = None, active: bool = True, conn: Connection | None = None,
                         day: int | None = None) -> None:
        """A constraint is an Event (Decision.constraints); id = event_id, day_end from args.days.
        `caused_by` defaults to the event's own caused_by."""
        e = constraint
        cause = caused_by if caused_by is not None else e.caused_by
        day_end = e.day + int(e.args["days"]) - 1 if "days" in e.args else e.day
        with self._tx(conn) as cx:
            cx.execute(insert(constraints).values(
                run_id=run_id, constraint_id=e.event_id, kind=e.kind, args=dumps(e.args), owner_role=owner_role,
                day_start=e.day, day_end=day_end, active=active, caused_by=cause))
            self._edges(cx, run_id, cause, c.node_id(c.CON, e.event_id), "constraint",
                        day if day is not None else e.day)

    def write_agreement(self, run_id: str, agreement_id: str, text: str, parties: list[str], day: int,
                        active: bool = True, retired_reason: str | None = None, conn: Connection | None = None) -> None:
        with self._tx(conn) as cx:
            cx.execute(insert(agreements).values(run_id=run_id, agreement_id=agreement_id, text=text,
                                                 parties=dumps(parties), day=day, active=active,
                                                 retired_reason=retired_reason))

    def write_event(self, run_id: str, e: c.Event, day: int | None = None, conn: Connection | None = None) -> None:
        """Log one event outside write_day (idempotent on event_id)."""
        with self._tx(conn) as cx:
            self._event(cx, run_id, e, day if day is not None else e.day)

    def write_eval_result(self, eval_id: str, scenario_id: str, profile_id: str, contender: str,
                          playbook_version: str | None, seed: int | None, metrics: dict) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(eval_results).values(eval_id=eval_id, scenario_id=scenario_id, profile_id=profile_id,
                                                     contender=contender, playbook_version=playbook_version,
                                                     seed=seed, metrics=dumps(metrics)))

    # ------------------------------------------------------------------ inbox
    def inbox_push(self, run_id: str, text: str, day_hint: int | None = None) -> str:
        msg_id = uuid.uuid4().hex[:8]
        with self.engine.begin() as conn:
            conn.execute(insert(inbox).values(run_id=run_id, msg_id=msg_id, day_hint=day_hint, text=text,
                                              received_at=_now(), consumed_day=None))
        return msg_id

    def inbox_pop(self, run_id: str, day: int) -> list[str]:
        """Unconsumed messages for `run_id` with no day_hint or day_hint <= day; marks them consumed."""
        with self.engine.begin() as conn:
            rows = conn.execute(select(inbox.c.msg_id, inbox.c.text).where(
                inbox.c.run_id == run_id, inbox.c.consumed_day.is_(None),
                or_(inbox.c.day_hint.is_(None), inbox.c.day_hint <= day)).order_by(inbox.c.received_at)).all()
            if rows:
                conn.execute(update(inbox).where(inbox.c.run_id == run_id,
                                                 inbox.c.msg_id.in_([r.msg_id for r in rows])).values(consumed_day=day))
        return [r.text for r in rows]

    # ------------------------------------------------------------------ forks
    def copy_run_prefix(self, parent_run_id: str, new_run_id: str, before_day: int) -> None:
        """Copy every per-run row with day < before_day from parent to new run (spec 11.2).
        KPI node ids embedding the parent run_id are rewritten to the new run_id."""
        old_kpi, new_kpi = f"{c.KPI}{parent_run_id}:", f"{c.KPI}{new_run_id}:"

        def fix(v: Any) -> Any:
            return new_kpi + v[len(old_kpi):] if isinstance(v, str) and v.startswith(old_kpi) else v

        with self.engine.begin() as conn:
            for table, day_col in _DAY_TABLES:
                rows = conn.execute(select(table).where(table.c.run_id == parent_run_id,
                                                        table.c[day_col] < before_day)).all()
                self._copy(conn, table, rows, new_run_id, fix)
            mtg_ids = select(meetings.c.meeting_id).where(meetings.c.run_id == parent_run_id,
                                                          meetings.c.day < before_day)
            for table in (utterances, options):
                rows = conn.execute(select(table).where(table.c.run_id == parent_run_id,
                                                        table.c.meeting_id.in_(mtg_ids))).all()
                self._copy(conn, table, rows, new_run_id, fix)

    @staticmethod
    def _copy(conn: Connection, table: Table, rows, new_run_id: str, fix) -> None:
        if rows:
            conn.execute(insert(table), [{k: fix(v) for k, v in r._mapping.items()} | {"run_id": new_run_id}
                                         for r in rows])

    # ------------------------------------------------------------------ reads
    def _all(self, stmt) -> list[dict]:
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(stmt).all()]

    def get_run(self, run_id: str) -> dict | None:
        rows = self._all(select(runs).where(runs.c.run_id == run_id))
        return rows[0] if rows else None

    def list_runs(self, mode: str | None = None) -> list[dict]:
        stmt = select(runs).order_by(runs.c.started_at.desc())
        return self._all(stmt.where(runs.c.mode == mode) if mode else stmt)

    def day_results(self, run_id: str) -> list[dict]:
        return self._all(select(day_results).where(day_results.c.run_id == run_id).order_by(day_results.c.day))

    def decisions(self, run_id: str) -> list[dict]:
        return self._all(select(decisions).where(decisions.c.run_id == run_id).order_by(decisions.c.day))

    def events(self, run_id: str) -> list[dict]:
        return self._all(select(events_log).where(events_log.c.run_id == run_id)
                         .order_by(events_log.c.day, events_log.c.hour, events_log.c.event_id))

    def meetings(self, run_id: str) -> list[dict]:
        return self._all(select(meetings).where(meetings.c.run_id == run_id).order_by(meetings.c.day))

    def table(self, name: str, run_id: str | None = None) -> list[dict]:
        """Generic read of any table, optionally filtered by run_id."""
        t = md.tables[name]
        return self._all(select(t).where(t.c.run_id == run_id) if run_id is not None and "run_id" in t.c else select(t))

    def cascade(self, run_id: str, node_id: str, depth: int = 6) -> list[dict]:
        """Edges reachable from `node_id` within `depth` hops, walking both directions (BFS)."""
        edges = self._all(select(cascade_edges.c.from_id, cascade_edges.c.to_id, cascade_edges.c.kind)
                          .where(cascade_edges.c.run_id == run_id))
        adj: dict[str, list[dict]] = {}
        for e in edges:
            adj.setdefault(e["from_id"], []).append(e)
            adj.setdefault(e["to_id"], []).append(e)
        seen_nodes, out, seen_edges = {node_id}, [], set()
        frontier = deque([(node_id, 0)])
        while frontier:
            n, d = frontier.popleft()
            if d >= depth:
                continue
            for e in adj.get(n, []):
                key = (e["from_id"], e["to_id"], e["kind"])
                if key not in seen_edges:
                    seen_edges.add(key)
                    out.append(e)
                for m in (e["from_id"], e["to_id"]):
                    if m not in seen_nodes:
                        seen_nodes.add(m)
                        frontier.append((m, d + 1))
        return out


    # ------------------------------------------------------------------ engine-lane additions (B5)
    def relog_events(self, run_id: str, events: Iterable[c.Event]) -> None:
        """Insert events_log rows only (no cascade edges), skipping ids already logged. Used by
        forks for pending consequences: copy_run_prefix copied their edges (written on a day
        before fork_day) but not their rows (their own day is >= fork_day)."""
        with self.engine.begin() as conn:
            for e in events:
                if conn.execute(select(events_log.c.event_id).where(
                        events_log.c.run_id == run_id, events_log.c.event_id == e.event_id)).first():
                    continue
                conn.execute(insert(events_log).values(
                    run_id=run_id, event_id=e.event_id, day=e.day, hour=e.hour, kind=e.kind,
                    args=dumps(e.args), source=e.source, caused_by=e.caused_by))

    def write_checks(self, run_id: str, day: int, rows: list[dict]) -> None:
        """`checks` rows outside write_day (final-scope results, written on the last day)."""
        if rows:
            with self.engine.begin() as conn:
                conn.execute(insert(checks), [dict(r, run_id=run_id, day=day) for r in rows])

    def drift_flag_rows(self, run_id: str) -> list[dict]:
        """drift_flags rows as plain dicts. `table("drift_flags")` cannot be used: its
        `response` column is plain text but shares a name with the JSON `llm_calls.response`."""
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(
                select(drift_flags).where(drift_flags.c.run_id == run_id).order_by(drift_flags.c.day))]

    def llm_usd_total(self, run_id: str) -> float:
        from sqlalchemy import func
        with self.engine.connect() as conn:
            v = conn.execute(select(func.coalesce(func.sum(llm_calls.c.usd), 0.0))
                             .where(llm_calls.c.run_id == run_id)).scalar()
        return float(v or 0.0)

class _Reuse:
    """Context manager that hands back an existing connection without committing."""
    def __init__(self, conn: Connection):
        self.conn = conn

    def __enter__(self) -> Connection:
        return self.conn

    def __exit__(self, *exc) -> None:
        return None


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")  # the console tails runs.db while the engine writes
    cur.close()


# --- added by standup lane (org/standup.py) ------------------------------------------------
_OPTION_UPDATABLE = ("levers", "evidence_id", "score", "feasible", "vetoed_by")


def _update_option(self: RunStore, run_id: str, option_id: str, conn: Connection | None = None,
                   **vals: Any) -> None:
    """Update an `options` row in place (the standup writes it at PROPOSE, then fills evidence,
    score, feasibility, veto and lock levers). Only the columns in `_OPTION_UPDATABLE`."""
    bad = set(vals) - set(_OPTION_UPDATABLE)
    if bad:
        raise ValueError(f"update_option: not updatable {sorted(bad)}")
    if not vals:
        return
    row = {k: (dumps(v) if k in JSON_COLS else v) for k, v in vals.items()}
    with self._tx(conn) as cx:
        cx.execute(update(options).where(options.c.run_id == run_id, options.c.option_id == option_id).values(**row))


RunStore.update_option = _update_option
