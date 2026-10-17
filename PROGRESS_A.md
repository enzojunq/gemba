# Person A progress log

One section per lane: what was built, how it is tested, what is pending. Issue #2 is the brief; `CONTRACT.md` is the boundary.

## Contract review (A) · 12:25

- **EVENT_ARGS confirmed** as drafted in `contracts.py`, including the four B chose without a spec signature (`staging_congestion.occupancy`, `director_escalation.metric`, `replen_alert.stockout_pct`, `absenteeism.pool/pct/days`).
- **C6 threshold agreed:** the real twin escalates the Director when on-time is below `target − 2` for 2 consecutive days (same slack as the daily done-check), as the StubTwin does. Spec 9.4 should read the same.
- **Units as resolved in CONTRACT §11:** percentages 0–100; `pm_peak_zone_load` and `safety_risk_max` are ratios.
- **`DayResult.hourly` format** is defined in `floor/HOURLY.md` (A-only; B stores it opaquely).

## Lane A1 — data generator (`data/`) · done 12:40

**Built.** `data/schema.sql` (spec 8.1, SQLite), `data/gen.py` (seeded, vectorized numpy/pandas; the 8 traps are named functions `zipf_velocity`, `family_cooccurrence`, `month3_shift`, `stale_layout`, `tight_pm`, `rush_orders`, `pallet_lines`, `key_customer_concentration`; the target slotter is `build_target`, fed days 1–60 only), `data/verify.py` (measures every trap on the written db; exit 1 on failure), `tests/test_data_gen.py` (4 tests, ~2 s).

**Run.** `make data` or `uv run python -m data.gen --seed 1 --verify [--out inputs.db] [--days 90] [--orders-per-day 2000]`. Writes to `INPUTS_DB_URL` (default `sqlite:///inputs.db`, repo root). Full 90 days: 180,000 orders, 2,136,415 lines, 1.2 s generate + 2.8 s write, 134 MB. `uv run python -m data.verify --db inputs.db` re-checks an existing file.

**Measured (seed 1, 90 days).** zipf top-20% share 0.790 (≥0.75); P(other family member | member) 0.625 (≥0.4); seasonal ratio 2.90 (≥2), fading 0.297 (≤0.5); top-200 in far aisles 0.445 (target 0.40); undersized top-300 slots 25; peak PM zone load 1.223 (target 1.2); rush 4.98 %; pallet lines 3.0 %; C001 share of lines 8.1 %, of which 89.9 % in top-300 value-of-move + seasonal (≥0.6); current→target changed active slots **1,111**; 11.87 lines/order.

**Decisions other lanes should know.**
- Ids: `S0001..S2000`, `F001..F300`, `C001..C200` (key: C001–C003), aisles 1..28, PM zones 1..6, `P001..P110`, `O<day:02>-<n:05>`, `line_no` from 1.
- Location ids per `floor/HOURLY.md`. Aisle bay runs **1–60** (bays 1–30 = left side, 31–60 = right), so `aisles.bays = 60`; levels 1–2 active, 3–4 reserve. PM `PM<z>-<001..120>`, staging `ST-001..150` (kind `staging`, slot_type `reserve`). `locations.capacity_units` is large (20k) and never binds; the effective slot size is `slotting_*.active_capacity_units`.
- `aisles.distance_m = 8 + 4·|aisle − 14.5|` → aisles 14/15 = 10 m, aisles 1/28 = 62 m. PM distance 0.
- `orders.cutoff_hour ∈ {13, 17, 21}` (21 stands for 21:30; `site.cutoffs` JSON is `[13, 17, 21.5]`). Rule: first cutoff ≥ release + 3 h (rush + 2 h); none left → 13, i.e. cutoff < release means ships next day.
- Extra columns beyond 8.1: `site.history_days` (=60), `site.gen_meta` (JSON ground truth of the traps: seasonal/fading/far/undersized/key SKU lists; for verify and tests only — the twin must not read it), `order_lines.line_no`, `order_lines.day` (indexed; load a day with `WHERE day = ?`).
- Target layout: top 720 by day 1–60 lines/day fill the PM with every zone ≤ 1.0 expected load (zone capacity 4,608 lines/day); the next fastest go to aisle tiers of 60 from 14/15 outward (level 1 first); a current slot within 8 m of the assigned aisle is kept; `active_capacity_units` = **3 days** of cover rounded up to 10, floor 20 (2 days would imply ~660 replen trips/day, above the 4-driver pool of ~576–640; 3 days gives ~510). `reserve_loc` is unchanged current→target.
- Current `active_capacity_units`: 3–6 days of cover (floor 20) → ~390 replen trips/day, plus the 25 undersized trap SKUs at 0.4 days of cover. Verify asserts implied current trips ≤ 560. Changed 12:50 at lane A2's request (the earlier 2–4 days implied 614 trips/day and saturated replenishment).
- `labor_calendar` has rows for every day: day/swing × {pm 12, forklift 14, replen 4, pallet 3, consolidation 10, loading 6}, night `night_move 4`, and `temp` scheduled **0** (on request only; 8 temps are in the roster with `is_temp=1`, productivity 0.7).
- `skus.demand_group` ∈ {normal, seasonal, fading} (exactly 120 seasonal, 120 fading): the scenario alias `family: seasonal` resolves through this column. Groups are whole families trimmed to 120 by rank.
- Velocity is a shifted power law (`(rank+20)^-1.3`); families are contiguous in rank so co-ordering keeps the curve. Far-aisle, seasonal and fading groups are whole families.
- Held-out days 61–90 use a separate RNG stream (`--heldout-seed`), and a test asserts the target and history do not change when it is perturbed.

**Open.** Trap thresholds in `verify.py` are the lead's bands; the 1,100-slot count is only printed (not asserted) so smaller `--days` runs still verify. `units_per_pallet` 20–120 and qty for non-pallet lines ≤ 12, so `qty == units_per_pallet` identifies a pallet line unambiguously.
## Lane A2 — floor twin (`floor/`, `scenarios/`) · running
## Lane A3 — console (`console/`) · running
## Integration with B at 13:00 · not started
