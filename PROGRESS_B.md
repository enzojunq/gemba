# Person B progress log

One section per phase: what was built, how it is tested, what is pending. Updated at the end of each phase. Issue #3 is the brief; `CONTRACT.md` is the boundary.

## Phase 0 — scaffold + contract (B0, B1) · done 12:15

**Built**
- `pyproject.toml` (uv, Python ≥3.11, pydantic 2 / pandas / sqlalchemy / openai / fastapi), `Makefile` with the spec §19 targets, `README.md`, package folders for both owners.
- `contracts.py`: every shared type from `CONTRACT.md` §3 (levers as a discriminated union, events + `EVENT_ARGS` + announcement lead times, demand, `FloorView`, `Kpis`, `DayResult`, `WhatIf`, `Decision`, `Scenario`, `Profile`, `DoneCheck`), the `FloorTwin` and `DecisionPolicy` protocols (§4–5), and the spec §10.6 agent output schemas.
- Opus review of `contracts.py` → 14 findings, 0 blockers, all should-fixes applied (`decision_id` stamped by B at commit, event args validated per kind, DataFrame-safe equality, `hourly` must be native types, `WhatIf.kpis_total` aggregation documented, `Scenario.day_range`).
- `CONTRACT.md` §11 records the unit decisions (ratios for `pm_peak_zone_load` / `safety_risk_max`, 0–100 elsewhere) and the `EVENT_ARGS` keys **Person A must confirm**.

**Tests** — `tests/test_contracts.py`, 7 tests
- Lever union parses floor levers, rejects `set_profile` and unknown fields; `LEVER_KINDS` matches the union.
- 17 event kinds; `EVENT_ARGS` and `ANNOUNCE_LEAD_DAYS` cover all; args validated against kind.
- `visible()` / `active()` filters; KPI names (18 + per-customer mis-picks); `DoneCheck` bounds and metric whitelist; `Decision` JSON round trip; `DayDemand` pickles; node-id helpers.

**Pending from this phase**
- A's confirmation of `EVENT_ARGS` (contract §11).

## Phase 1 — lanes (B2 LLM · B3 store · B4 baselines/StubTwin · B6 scoring) · in progress

### B6 scoring, feasibility, profiles, verifier template · done 12:30
**Built**
- `profiles/customer_first.yaml`, `profiles/efficiency_first.yaml` (spec §10.4 verbatim).
- `org/scoring.py`: `load_profile`, the single §10.4 formula `score_kpis -> Score(total, terms)`, `score_whatif`, `aggregate_run` + `AGGREGATION` (mean / sum / max / end, same mapping as `WhatIf.kpis_total`), `feasibility -> Feasibility(feasible, breaches)`, `least_bad`.
- `engine/verifier.py`: `template(profile)` builds 7 daily + 5 final `DoneCheck`s with chair-selectable bounds (moves budget default 200, range 150–280). `check_day` / `check_final` are stubs for the engine lane.
- Resolutions worth knowing: `reslot_progress_pct` scores absolute progress (REF = 0); relative scales fall back to 1.0 when REF = 0; `final.score >= 0` means "≥ static-rules baseline" because that baseline is the REF; `lph_lift_pct` is vs the do-nothing baseline mean LPH.

**Tests** — `tests/test_scoring.py` (15), `tests/test_verifier.py` (4)
- Zero delta → 0; +5 on-time → +0.35; clipping at ±1; mis-pick penalty sign; relative lph/labor-cost scales; progress independent of REF; weight validation; both profiles load; aggregation rules; feasibility breach amounts; `least_bad`.
- Template thresholds sit inside their bounds, include derived metrics, every metric is a KPI name or derived.

### B2 LLM client + cache (lane 2) · running
### B3 run store + B4 baselines/StubTwin (lane 1) · running

## Phase 2 — engine day loop, forks, engine API, verifier runner, drift (B5, B9) · not started
## Phase 3 — org: roles, prompts, authority, standup, hiring, Event Interpreter (B7, B8) · not started
## Integration with A at 13:00 · not started
## Phase 4 — hero run, recordings, eval table (B10) · not started
## Phase 5 — retro coach, playbook, gate (B11) · not started
