"""Export one run (and optionally its forks) from a run store into a standalone SQLite file,
e.g. `recordings/hero.db`, so `make demo` replays it offline (spec section 22; decision Q12).

    uv run python -m engine.recording --run bt-abe8133f --out recordings/hero.db [--with-forks]
    uv run python -m engine.recording --run bt-abe8133f --run fk-2bd9d337 --out recordings/hero.db

Both files use the same `RunStore` schema, so rows copy column-for-column with SQLite ATTACH.
"""
from __future__ import annotations

import argparse
import os
import sqlite3

from engine.store import RunStore


def _sqlite_path(url: str) -> str:
    assert url.startswith("sqlite:///"), f"only sqlite stores can be exported: {url}"
    return url[len("sqlite:///"):]


GLOBAL_TABLES = ("playbook_rules", "eval_results")
"""Tables without a run_id that the console's Learning view reads; copied whole."""


def learning_run_ids(con: sqlite3.Connection) -> list[str]:
    """Runs the Learning view links to: each playbook rule's parent, verification fork and REF run
    (from `playbook_rules.evidence`) and every run an `eval_results` row points at (the gate's v0/v1
    test runs and the static reference)."""
    import json
    ids: list[str] = []
    for (ev,) in con.execute("SELECT evidence FROM src.playbook_rules"):
        try:
            e = json.loads(ev) if ev else {}
        except ValueError:
            continue
        ids += [e.get(k) for k in ("run_id", "fork_run_id", "ref_run_id") if e.get(k)]
    for (m,) in con.execute("SELECT metrics FROM src.eval_results"):
        try:
            d = json.loads(m) if m else {}
        except ValueError:
            continue
        if d.get("run_id"):
            ids.append(d["run_id"])
        for k in ("run_ids", "run_ids_from", "run_ids_to"):
            v = d.get(k)
            if isinstance(v, list):
                ids += [x for x in v if isinstance(x, str)]
    return [i for i in dict.fromkeys(ids) if i]


def export_runs(src_url: str, run_ids: list[str], out_path: str, *, with_forks: bool = False,
                with_learning: bool = False) -> dict[str, int]:
    """Copy every row of the given runs into `out_path` (created with the current schema).
    Returns row counts per table. Existing rows for the same run ids in `out_path` are replaced.
    `with_learning` also copies the runs behind the playbook rules and the gate (console Learning view)."""
    src = _sqlite_path(src_url)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    RunStore(f"sqlite:///{out_path}").create_schema()
    con = sqlite3.connect(out_path)
    con.execute("ATTACH DATABASE ? AS src", (src,))
    ids = list(run_ids)
    if with_learning:
        ids += learning_run_ids(con)
    if with_forks:
        for rid in list(ids):
            ids += [r[0] for r in con.execute("SELECT run_id FROM src.runs WHERE parent_run_id=?", (rid,))]
    ids = list(dict.fromkeys(ids))
    tables = [r[0] for r in con.execute(
        "SELECT name FROM src.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    counts: dict[str, int] = {}
    marks = ",".join("?" * len(ids))
    with con:
        for t in tables:
            cols = [r[1] for r in con.execute(f"PRAGMA src.table_info({t})")]
            if "run_id" not in cols:
                continue  # playbook_rules and the like are global; copy them whole below
            con.execute(f"DELETE FROM main.{t} WHERE run_id IN ({marks})", ids)
            cur = con.execute(f"INSERT INTO main.{t} SELECT * FROM src.{t} WHERE run_id IN ({marks})", ids)
            counts[t] = cur.rowcount
        # global tables the console may read alongside a run
        for t in GLOBAL_TABLES:
            if t in tables:
                con.execute(f"DELETE FROM main.{t}")
                counts[t] = con.execute(f"INSERT INTO main.{t} SELECT * FROM src.{t}").rowcount
    con.execute("DETACH DATABASE src")
    con.close()
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", action="append", required=True, help="run id (repeatable)")
    ap.add_argument("--out", default="recordings/hero.db")
    ap.add_argument("--store", default=os.environ.get("RUN_STORE_URL", "sqlite:///runs.db"))
    ap.add_argument("--with-forks", action="store_true", help="also copy forks of the given runs")
    ap.add_argument("--with-learning", action="store_true",
                    help="also copy the runs behind playbook_rules and eval_results (console Learning view)")
    a = ap.parse_args()
    counts = export_runs(a.store, a.run, a.out, with_forks=a.with_forks, with_learning=a.with_learning)
    print(f"wrote {a.out}: " + ", ".join(f"{t} {n}" for t, n in sorted(counts.items()) if n))


if __name__ == "__main__":
    main()
