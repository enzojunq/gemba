# Person B progress log

One section per phase: what was built, how it is tested, what is pending. Updated at the end of each phase. Issue #3 is the brief; `CONTRACT.md` is the boundary.

## Phase 0 - scaffold + contract (B0, B1) - done 11:55

**Built**
- `pyproject.toml` (uv, Python >= 3.11, pydantic 2 / pandas / sqlalchemy / openai / fastapi), `Makefile` with the spec section 19 targets, `README.md`, package folders for both owners.
- `contracts.py`: every shared type from `CONTRACT.md` section 3 (levers as a discriminated union, events + `EVENT_ARGS` + announcement lead times, demand, `FloorView`, `Kpis`, `DayResult`, `WhatIf`, `Decision`, `Scenario`, `Profile`, `DoneCheck`), the `FloorTwin` and `DecisionPolicy` protocols (sections 4-5), and the spec 10.6 agent output schemas.
- Opus review of `contracts.py`: 14 findings, 0 blockers, all should-fixes applied (`decision_id` stamped by B at commit, event args validated per kind, DataFrame-safe equality, `hourly` must be native types, `WhatIf.kpis_total` aggregation documented, `Scenario.day_range`).
- `CONTRACT.md` section 11 records the unit decisions (ratios for `pm_peak_zone_load` / `safety_risk_max`, 0-100 elsewhere) and the `EVENT_ARGS` keys **Person A must confirm**.

**Tests** - `tests/test_contracts.py`, 7 tests
- Lever union parses floor levers, rejects `set_profile` and unknown fields; `LEVER_KINDS` matches the union.
- 17 event kinds; `EVENT_ARGS` and `ANNOUNCE_LEAD_DAYS` cover all; args validated against kind.
- `visible()` / `active()` filters; KPI names (18 + per-customer mis-picks); `DoneCheck` bounds and metric whitelist; `Decision` JSON round trip; `DayDemand` pickles; node-id helpers.

**Pending from this phase**
- A's confirmation of `EVENT_ARGS` (contract section 11).

## Phase 1 - parallel lanes (B2 LLM, B3 store, B4 baselines/StubTwin, B6 scoring, B7 org config) - done 12:28

### B6 scoring, feasibility, profiles, verifier template - done 12:05
**Built**
- `profiles/customer_first.yaml`, `profiles/efficiency_first.yaml` (spec 10.4 verbatim).
- `org/scoring.py`: `load_profile`, the single 10.4 formula `score_kpis -> Score(total, terms)`, `score_whatif`, `aggregate_run` + `AGGREGATION` (mean / sum / max / end, same mapping as `WhatIf.kpis_total`), `feasibility -> Feasibility(feasible, breaches)`, `least_bad`.
- `engine/verifier.py`: `template(profile)` builds 7 daily + 5 final `DoneCheck`s with chair-selectable bounds (moves budget default 200, range 150-280). `check_day` / `check_final` runner is being filled in by the engine lane.
- Resolutions worth knowing: `reslot_progress_pct` scores absolute progress (REF = 0); relative scales fall back to 1.0 when REF = 0; `final.score >= 0` means "at least the static-rules baseline" because that baseline is the REF; `lph_lift_pct` is vs the do-nothing baseline mean LPH.

**Tests** - `tests/test_scoring.py` (15), `tests/test_verifier.py` (4+)
- Zero delta gives 0; +5 on-time gives +0.35; clipping at +-1; mis-pick penalty sign; relative lph/labor-cost scales; progress independent of REF; weight validation; both profiles load; aggregation rules; feasibility breach amounts; `least_bad`.
- Template thresholds sit inside their bounds, include derived metrics, every metric is a KPI name or derived.

### B2 LLM client, cache, prompt builder, smoke test - done 12:10
**Built**
- `llm/client.py`: OpenAI-compatible client for the three Nemotron tiers (ids from `.env`), strict `json_schema` output with `json_object` then text fallbacks remembered per model, one validation retry then a caller-supplied safe default logged as `llm_fallback`, Nemotron reasoning toggle (`chat_template_kwargs.enable_thinking`), cost + cached-token accounting, `on_call` sink for the run store.
- `llm/cache.py`: append-only JSONL (`recordings/llm_cache.jsonl`, overridable) keyed by sha256(model, messages, schema, temperature, reasoning). Hits skip the network, cost $0.
- `llm/prompt.py`: `build_messages(role_prompt, playbook_rules, board_snapshot, task)` in the fixed KV-cache order.
- `llm/smoke.py`: the phase-0 gate. **Live Crusoe results:** Super `NVIDIA-Nemotron-3-Super-120B-A12B` 1.7 s, Nano `Nemotron-3-Nano-30B-A3B` 0.26 s, both in strict `json_schema` mode; repeat call from cache at $0. Reasoning on Super: 1.5 s vs 0.5 s off; thinking returns in `reasoning_content`, counts against `max_tokens`.
- Spec 12 corrections found live: Nano id differs from the spec's Omni id (price may differ); Super id is the capitalised form; Super price still a placeholder.

**Tests** - `tests/test_llm.py`, 8 tests (fake OpenAI client, no network)
- Cache hit on identical call; key changes with schema; validation retry path; fallback path; message order fixed; `<think>` stripping; `ModelNotConfigured` on an empty tier.

**Pending**
- Confirm Super/Nano prices in the Crusoe console.

### B3 run store + B4 baselines and StubTwin - done 12:18
**Built**
- `engine/store.py`: `RunStore` over SQLAlchemy Core + SQLite (WAL so the console can read while the engine writes). All spec 8.2 tables + `inbox`, `snapshot_path`, and a few additive nullable columns (listed in `CONTRACT.md` section 7). **Cascade edges are automatic**: any insert with `caused_by` writes a `cascade_edges` row with typed node ids. `write_day` is one transaction per simulated day; small writers (`write_meeting`, `write_utterance`, `write_option`, `write_evidence`, `write_hire`, `write_llm_call`, ...) take an optional `conn` to join a transaction or commit alone so meeting rows stream live. `copy_run_prefix` copies rows with day < fork day and rewrites KPI node ids to the child run. `cascade(run_id, node, depth)` walks both directions.
- `engine/stub_twin.py`: `StubTwin` implementing `contracts.FloorTwin`, deterministic per `scenario.seed`, picklable state, honours every lever in `view()`, physical cap 280 legs/night, rejects aisle > 28 / zone > 6. Consequences C1, C2, C3, C6 with correct `caused_by` attribution (C2 to the moving decision; C6 to the KPI node). Calibrated so 280 moves/night trips C2, 200 does not, and `protect KEY1` prevents it.
- `eval/baselines.py`: `DoNothing`, `NaiveBigBang` (1200 moves + 8 temps on the first two days), `StaticRules` (200/night, pause on event days). `eval.POLICIES` registry.

**Tests** - `tests/test_store.py` (7), `tests/test_stub_twin.py` (9), `tests/test_baselines.py` (4)
- Schema creates; `write_day` atomic (exception mid-write persists nothing); caused_by produces a cascade edge with typed ids; `copy_run_prefix` boundary; inbox push/pop; `write_llm_call` accepts the client's dict.
- StubTwin is a `FloorTwin`; determinism; pickle round trip; `close_aisle` visible; rejection for aisle 99; C2 fires with `dec:` cause; `whatif` never touches `actual_demand`.
- StaticRules pauses on event days, 200 otherwise; all three are `DecisionPolicy`; levers carry decision ids.

**Flags for A / the spec**
- **C6 threshold:** spec 9.4 escalates on on-time below the profile target (97) for 2 days, but 9.3a calibrates normal days at 94-97%, so the Director would be escalated almost daily. The stub uses target - 2 (95), the same slack as the daily done-check. Recommend the real twin and spec 9.4 adopt target - 2.
- Store columns beyond spec 8.2 (additive, nullable) are listed in `CONTRACT.md` section 7.

### B7 roles, prompts, authority - done 12:28
**Built**
- `org/roles/*.yaml`: the 9 roles (Dana, Megan, Ryan, Luke, Paige, Sam, Ellen, Marcus, Tom) in the spec 10.2 schema, plus `meta_powers` for the director/chair (set_profile, override_lock, break_tie / escalate, set_agenda, hire_roles, decide) and `runs_simulator` for Paige.
- `org/prompts/*.md`: 9 KV-cache-stable role prompts (129-221 words each: identity, owned KPIs, powers and their exact limits, proposable levers, the "cite evidence ids, never invent numbers" rule, JSON-only, 60-word rationale cap) + `event_interpreter.md` (generated from `EVENT_ARGS`) + `retro_coach.md`.
- `org/roles.py`: validated `load_roles` (exactly one chair, one director, one simulator role; levers in `LEVER_KINDS`; KPIs in `KPI_NAMES`), `role_prompt`, `meta_prompt`, deterministic `hire_triggers` (event kinds or KPI conditions like `mispick_rate > 0.6`; code decides hiring per 10.5).
- `org/authority.py`: `filter_proposal` (drops levers a role may not propose, with reason), `issuer_for` / `commit_plan` per 10.3 (chair / ops / shift lead within budget else director / quality up to 3 days else chair / safety; absent on-call issuer gives `needs_hire`; director absent gives escalate), `veto_allowed`, `lock_allowed`, `capacity_truth`.

**Tests** - `tests/test_roles.py` (27), `tests/test_authority.py` (13)
- All 9 load; one chair/director; prompts exist and are < 250 words; interpreter prompt names every exogenous kind; triggers fire for rack_damage (safety), mispick 0.7 (quality), labor_shortage (shift lead), nothing on a quiet day.
- Engineer proposing `close_aisle` is filtered; `protect` 2 d goes to quality, 5 d to chair; overtime within/over budget goes to shift lead/director; absent quality gives `needs_hire`; `hire_temps` without director escalates; mixed option gets the correct issuers.

**Resolutions:** roster "owns" items that aren't KPIs (schedule, revenue, headcount) mapped to the nearest KPIs; `chair_escalates`, `veto_deadlock`, `overtime_requested` kept as signal strings the standup passes in; Megan may propose the five levers she issues; characters for 8 roles written from their roster lines (only Ryan's was given).

**Model decision 12:25:** Nemotron Ultra is not in the Crusoe catalog today, so `MODEL_ULTRA` = the Super id. Tier mapping in code unchanged (spec Part D #26).

## Phase 2 - engine day loop, forks, drift, verifier runner, engine API (B5, B9) - done 12:30

**Built**
- `engine/run.py`: the CONTRACT section 6 day loop over any `FloorTwin` + `DecisionPolicy`. `make_twin("auto")` imports `floor.twin.make_twin()` / `Twin()` and falls back to `StubTwin` with a warning (this is the whole integration switch). `make_policy` builds baselines or `OrgPolicy`. CLI: `python -m engine.run --scenario --profile --policy --seed --days 61-70`.
- `engine/fork.py`: restores the parent's day-1 snapshot, copies rows before the fork day, re-logs pending consequences, forces the fork-day decision, then the policy decides normally. CLI `python -m engine.fork --run --day --levers`.
- `engine/drift.py`: all spec 11.4 detectors (repeat what-if, plan churn, stall without a reason, prediction error > 15% for 2 days which triggers `twin.recalibrate`, LLM overrun); a repeating flag becomes `shrink_or_stop`.
- `engine/verifier.py`: `check_day` / `check_final` evaluate every `DoneCheck`; derived metrics missing are skipped, not failed.
- `engine/snapshot.py`: pickled per-day payload (state, pending consequences, constraints, hires, checks, drift counters, scenario, inbox events).
- `engine/events.py`: `load_scenario`, `build_calendar`, `parse_inbox` hook (replaced by the Event Interpreter in Phase 3).
- `engine/api.py`: `start_run`, `fork_run`, `run_status` (background threads; return once the `runs` row exists) for A's console.
- `scenarios/reslot_default.yaml`: the spec 9.5 calendar, valid against `contracts.Scenario`.
- Store additions: tolerant JSON decoding (the `drift_flags.response` text column), `relog_events`, `write_checks`, `llm_usd_total`, `drift_flag_rows`.

**Tests** - `tests/test_engine.py` (9), `tests/test_fork.py` (5), `tests/test_drift.py` (8), `tests/test_verifier_runner.py` (3) - suite total 119
- 30-day StaticRules run on StubTwin finishes in 1.5 s with 30 `day_results` + 30 `decisions`; two runs with the same seed give identical KPI JSON per day.
- NaiveBigBang triggers a `customer_escalation` whose cascade walks back to a `dec:` node.
- Fork at the escalation day: child equals parent before the fork day, differs after; `parent_run_id` set; pending consequences re-logged exactly once.
- `--days 61-70` trims to 10 days; drift detectors fire on their streaks and escalate on repeat; failing on-time day writes a `checks` row with `passed=False`; `api.start_run` reaches "done".

**Measured (StubTwin, StaticRules, 30 days):** on-time 94.4, mis-pick 0.48%, LPH 99.0, progress 100%, labor cost $538,560. Stub numbers, not the real twin.

**Known gap:** `final.score >= 0` is weaker than "at least static rules" because `reslot_progress_pct` is scored with REF = 0 (a run scored against itself is not 0). Resolve in the eval harness (B10) by reporting score deltas between contenders rather than through this check.

**Integration notes for A (CONTRACT section 4):** expose `floor/twin.py` with `make_twin()` or `Twin()`; `engine.run` picks it up automatically.
## Phase 3 - standup / OrgPolicy, board, Event Interpreter (B8) - in progress (lane 6)
## Integration with A at 13:00 - not started
## Phase 4 - hero run, recordings, eval table (B10) - not started
## Phase 5 - retro coach, playbook, gate (B11) - not started
