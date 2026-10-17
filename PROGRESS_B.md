# Person B progress log

One section per phase: what was built, how it is tested, what is pending. Updated at the end of each phase. Issue #3 is the brief; `CONTRACT.md` is the boundary.

## Phase 0 â€” scaffold + contract (B0, B1) Â· done 11:55

**Built**
- `pyproject.toml` (uv, Python â‰¥3.11, pydantic 2 / pandas / sqlalchemy / openai / fastapi), `Makefile` with the spec Â§19 targets, `README.md`, package folders for both owners.
- `contracts.py`: every shared type from `CONTRACT.md` Â§3 (levers as a discriminated union, events + `EVENT_ARGS` + announcement lead times, demand, `FloorView`, `Kpis`, `DayResult`, `WhatIf`, `Decision`, `Scenario`, `Profile`, `DoneCheck`), the `FloorTwin` and `DecisionPolicy` protocols (Â§4â€“5), and the spec Â§10.6 agent output schemas.
- Opus review of `contracts.py` â†’ 14 findings, 0 blockers, all should-fixes applied (`decision_id` stamped by B at commit, event args validated per kind, DataFrame-safe equality, `hourly` must be native types, `WhatIf.kpis_total` aggregation documented, `Scenario.day_range`).
- `CONTRACT.md` Â§11 records the unit decisions (ratios for `pm_peak_zone_load` / `safety_risk_max`, 0â€“100 elsewhere) and the `EVENT_ARGS` keys **Person A must confirm**.

**Tests** â€” `tests/test_contracts.py`, 7 tests
- Lever union parses floor levers, rejects `set_profile` and unknown fields; `LEVER_KINDS` matches the union.
- 17 event kinds; `EVENT_ARGS` and `ANNOUNCE_LEAD_DAYS` cover all; args validated against kind.
- `visible()` / `active()` filters; KPI names (18 + per-customer mis-picks); `DoneCheck` bounds and metric whitelist; `Decision` JSON round trip; `DayDemand` pickles; node-id helpers.

**Pending from this phase**
- A's confirmation of `EVENT_ARGS` (contract Â§11).

## Phase 1 â€” lanes (B2 LLM Â· B3 store Â· B4 baselines/StubTwin Â· B6 scoring) Â· in progress

### B6 scoring, feasibility, profiles, verifier template Â· done 12:05
**Built**
- `profiles/customer_first.yaml`, `profiles/efficiency_first.yaml` (spec Â§10.4 verbatim).
- `org/scoring.py`: `load_profile`, the single Â§10.4 formula `score_kpis -> Score(total, terms)`, `score_whatif`, `aggregate_run` + `AGGREGATION` (mean / sum / max / end, same mapping as `WhatIf.kpis_total`), `feasibility -> Feasibility(feasible, breaches)`, `least_bad`.
- `engine/verifier.py`: `template(profile)` builds 7 daily + 5 final `DoneCheck`s with chair-selectable bounds (moves budget default 200, range 150â€“280). `check_day` / `check_final` are stubs for the engine lane.
- Resolutions worth knowing: `reslot_progress_pct` scores absolute progress (REF = 0); relative scales fall back to 1.0 when REF = 0; `final.score >= 0` means "â‰¥ static-rules baseline" because that baseline is the REF; `lph_lift_pct` is vs the do-nothing baseline mean LPH.

**Tests** â€” `tests/test_scoring.py` (15), `tests/test_verifier.py` (4)
- Zero delta â†’ 0; +5 on-time â†’ +0.35; clipping at Â±1; mis-pick penalty sign; relative lph/labor-cost scales; progress independent of REF; weight validation; both profiles load; aggregation rules; feasibility breach amounts; `least_bad`.
- Template thresholds sit inside their bounds, include derived metrics, every metric is a KPI name or derived.

### B2 LLM client, cache, prompt builder, smoke test Â· done 12:10
**Built**
- `llm/client.py`: OpenAI-compatible client for the three Nemotron tiers (ids from `.env`), strict `json_schema` output with `json_object` â†’ text fallbacks remembered per model, one validation retry then a caller-supplied safe default logged as `llm_fallback`, Nemotron reasoning toggle (`chat_template_kwargs.enable_thinking`), cost + cached-token accounting, `on_call` sink for the run store.
- `llm/cache.py`: append-only JSONL (`recordings/llm_cache.jsonl`, overridable) keyed by sha256(model, messages, schema, temperature, reasoning). Hits skip the network, cost $0.
- `llm/prompt.py`: `build_messages(role_prompt, playbook_rules, board_snapshot, task)` in the fixed KV-cache order.
- `llm/smoke.py`: the phase-0 gate. **Live Crusoe results:** Super `NVIDIA-Nemotron-3-Super-120B-A12B` 1.7 s, Nano `Nemotron-3-Nano-30B-A3B` 0.26 s, both in strict `json_schema` mode; repeat call from cache at $0. Reasoning on Super: 1.5 s vs 0.5 s off; thinking returns in `reasoning_content`, counts against `max_tokens`.
- Spec Â§12 corrections found live: Nano id differs from the spec's Omni id (price may differ); Super id is the capitalised form; Super price still a placeholder.

**Tests** â€” `tests/test_llm.py`, 8 tests (fake OpenAI client, no network)
- Cache hit on identical call; key changes with schema; validation retry path; fallback path; message order fixed; `<think>` stripping; `ModelNotConfigured` on an empty tier.

**Pending**
- `MODEL_ULTRA` in `.env`, then re-run `uv run python -m llm.smoke` for the Ultra latency gate (20 s) and the reasoning call.
- Confirm Super/Nano prices in the Crusoe console.

### B3 run store + B4 baselines and StubTwin Â· done 12:18
**Built**
- `engine/store.py`: `RunStore` over SQLAlchemy Core + SQLite (WAL so the console can read while the engine writes). All spec Â§8.2 tables + `inbox`, `snapshot_path`, and a few additive nullable columns (listed in `CONTRACT.md` Â§7). **Cascade edges are automatic**: any insert with `caused_by` writes a `cascade_edges` row with typed node ids. `write_day` is one transaction per simulated day; small writers (`write_meeting`, `write_utterance`, `write_option`, `write_evidence`, `write_hire`, `write_llm_call`, â€¦) take an optional `conn` to join a transaction or commit alone so meeting rows stream live. `copy_run_prefix` copies rows with day < fork day and rewrites KPI node ids to the child run. `cascade(run_id, node, depth)` walks both directions.
- `engine/stub_twin.py`: `StubTwin` implementing `contracts.FloorTwin`, deterministic per `scenario.seed`, picklable state, honours every lever in `view()`, physical cap 280 legs/night, rejects aisle > 28 / zone > 6. Consequences C1, C2, C3, C6 with correct `caused_by` attribution (C2 â†’ the moving decision; C6 â†’ the KPI node). Calibrated so 280 moves/night trips C2, 200 does not, and `protect KEY1` prevents it.
- `eval/baselines.py`: `DoNothing`, `NaiveBigBang` (1200 moves + 8 temps on the first two days), `StaticRules` (200/night, pause on event days). `eval.POLICIES` registry.

**Tests** â€” `tests/test_store.py` (7), `tests/test_stub_twin.py` (9), `tests/test_baselines.py` (4)
- Schema creates; `write_day` atomic (exception mid-write persists nothing); caused_by â†’ cascade edge with typed ids; `copy_run_prefix` boundary; inbox push/pop; `write_llm_call` accepts the client's dict.
- StubTwin is a `FloorTwin`; determinism; pickle round trip; `close_aisle` visible; rejection for aisle 99; C2 fires with `dec:` cause; `whatif` never touches `actual_demand`.
- StaticRules pauses on event days, 200 otherwise; all three are `DecisionPolicy`; levers carry decision ids.

**Flags for A / the spec**
- **C6 threshold:** spec 9.4 escalates on on-time `< profile target` (97) for 2 days, but 9.3a calibrates normal days at 94â€“97%, so the Director would be escalated almost daily. The stub uses target âˆ’ 2 (95), same slack as the daily done-check. Recommend the real twin and spec 9.4 adopt `target âˆ’ 2`.
- Store columns beyond spec 8.2 (additive, nullable) are listed in `CONTRACT.md` Â§7.

### B7 roles, prompts, authority (lane 4) Â· running
### B5 engine day loop, forks, drift, verifier runner, engine API (lane 5) Â· running

## Phase 2 â€” engine day loop, forks, engine API, verifier runner, drift (B5, B9) Â· not started
## Phase 3 â€” org: roles, prompts, authority, standup, hiring, Event Interpreter (B7, B8) Â· not started
## Integration with A at 13:00 Â· not started
## Phase 4 â€” hero run, recordings, eval table (B10) Â· not started
## Phase 5 â€” retro coach, playbook, gate (B11) Â· not started
