"""Per-day engine snapshots (spec 11.2): `snapshots/<run_id>/<day>.pkl`.

Payload keys written by the engine: state (FloorState, picklable per CONTRACT 3.6),
pending_consequences, active_constraints, hires, done_checks, drift_counters, rng_state
(optional), plus scenario, known_inbox_events and predictions so a fork can resume without
the parent's in-memory context. The root defaults to ./snapshots and can be moved with
GEMBA_SNAPSHOT_DIR (read at call time, so tests can redirect it).
"""

from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Any


def root() -> Path:
    return Path(os.environ.get("GEMBA_SNAPSHOT_DIR", "snapshots"))


def path_for(run_id: str, day: int, base: str | Path | None = None) -> Path:
    return Path(base) if base is not None and Path(base).suffix == ".pkl" else \
        (Path(base) if base is not None else root()) / run_id / f"{day}.pkl"


def save(run_id: str, day: int, payload: dict[str, Any], base: str | Path | None = None) -> str:
    p = path_for(run_id, day, base)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".pkl.tmp")
    with open(tmp, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, p)
    return str(p)


def load(path: str | Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return pickle.load(f)
