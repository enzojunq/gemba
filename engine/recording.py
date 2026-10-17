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


def export_runs(src_url: str, run_ids: list[str], out_path: str, *, with_forks: bool = False) -> dict[str, int]:
    """Copy every row of the given runs into `out_path` (created with the current schema).
    Returns row counts per table. Existing rows for the same run ids in `out_path` are replaced."""
    src = _sqlite_path(src_url)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    RunStore(f"sqlite:///{out_path}").create_schema()
    con = sqlite3.connect(out_path)
    con.execute("ATTACH DATABASE ? AS src", (src,))
    ids = list(run_ids)
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
        for t in ("playbook_rules",):
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
    a = ap.parse_args()
    counts = export_runs(a.store, a.run, a.out, with_forks=a.with_forks)
    print(f"wrote {a.out}: " + ", ".join(f"{t} {n}" for t, n in sorted(counts.items()) if n))


if __name__ == "__main__":
    main()
