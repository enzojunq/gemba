"""Lane A1: data generator tests. Small dataset (12 days, full orders/day) so every trap is measurable in < 20 s."""
import sqlite3

import pandas as pd
import pytest

from data.gen import generate, write_db, main as gen_main
from data.verify import verify

TABLES = ["site", "aisles", "pm_zones", "locations", "skus", "customers", "slotting_current", "slotting_target",
          "orders", "order_lines", "labor_roster", "labor_calendar"]
DAYS = 12


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("gen") / "inputs.db")
    assert gen_main(["--seed", "1", "--days", str(DAYS), "--out", path]) == 0
    return path


def test_schema_tables_and_columns(db):
    con = sqlite3.connect(db)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(TABLES) <= names
    cols = lambda t: [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
    assert cols("orders")[:6] == ["order_id", "day", "release_hour", "customer_id", "priority", "cutoff_hour"]
    assert cols("order_lines")[:4] == ["order_id", "line_no", "sku_id", "qty"]
    assert cols("slotting_target") == ["sku_id", "active_loc", "reserve_loc", "active_capacity_units"]
    assert cols("labor_calendar") == ["day", "shift", "role", "scheduled"]
    assert con.execute("SELECT count(*) FROM skus").fetchone()[0] == 2000
    assert con.execute("SELECT count(*) FROM customers WHERE tier='key'").fetchone()[0] == 3
    assert con.execute("SELECT count(*) FROM locations WHERE kind='pm'").fetchone()[0] == 720
    assert con.execute("SELECT count(*) FROM locations WHERE kind='staging'").fetchone()[0] == 150
    assert con.execute("SELECT count(*) FROM locations WHERE kind='aisle'").fetchone()[0] == 28 * 60 * 4
    assert con.execute("SELECT count(*) FROM labor_roster").fetchone()[0] == 110
    assert con.execute("SELECT count(DISTINCT day) FROM orders").fetchone()[0] == DAYS
    assert con.execute("SELECT min(line_no) FROM order_lines").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM orders WHERE cutoff_hour NOT IN (13,17,21)").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM orders WHERE release_hour < 5 OR release_hour > 19").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM skus WHERE demand_group='seasonal'").fetchone()[0] == 120
    assert con.execute("SELECT count(*) FROM skus WHERE demand_group='fading'").fetchone()[0] == 120
    assert con.execute("SELECT count(*) FROM skus WHERE demand_group='normal'").fetchone()[0] == 1760
    roles = {r[0] for r in con.execute("SELECT DISTINCT role FROM labor_roster")}
    assert roles == {"pm", "forklift", "pallet", "replen", "consolidation", "loading", "night_move", "temp"}
    con.close()


def test_eight_traps_hold(db):
    checks = verify(db)
    failed = [c for c in checks if not c.ok]
    assert not failed, [(c.name, c.measured, c.target) for c in failed]
    assert len([c for c in checks if not c.name.startswith("info")]) >= 8


def test_cutoff_rule(db):
    con = sqlite3.connect(db)
    o = pd.read_sql("SELECT release_hour, priority, cutoff_hour FROM orders", con)
    con.close()
    due = o["release_hour"] + o["priority"].map({"normal": 3, "rush": 2})
    expected = due.map(lambda d: next((c for c in (13, 17, 21) if d <= c), 13))
    assert (o["cutoff_hour"] == expected).all()


def test_target_uses_history_only_and_is_deterministic():
    a = generate(seed=1, days=DAYS, orders_per_day=2000, heldout_seed=0)
    b = generate(seed=1, days=DAYS, orders_per_day=2000, heldout_seed=5)
    hist = a["_meta"]["history_days"]
    pd.testing.assert_frame_equal(a["slotting_target"], b["slotting_target"])
    pd.testing.assert_frame_equal(a["slotting_current"], b["slotting_current"])
    pd.testing.assert_frame_equal(a["orders"][a["orders"].day <= hist], b["orders"][b["orders"].day <= hist])
    assert not a["orders"][a["orders"].day > hist].equals(b["orders"][b["orders"].day > hist])
    c = generate(seed=1, days=DAYS, orders_per_day=2000, heldout_seed=0)
    pd.testing.assert_frame_equal(a["order_lines"], c["order_lines"])
