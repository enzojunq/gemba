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
## Phase 3 - standup / OrgPolicy, board, Event Interpreter (B8) - done 12:40

**Built**
- `org/standup.py`: `OrgPolicy` (a `DecisionPolicy`) running the spec 10.5 ritual in order: BRIEF (code builds the board) -> TRIAGE (code decides quiet days and hires; chair sets the agenda) -> PROPOSE (engineer + up to 2 alternatives, max 3 options, unauthorized levers filtered and logged) -> EVIDENCE (`twin.whatif` per option + do-nothing REF, evidence ids, spec hashes for the drift detector) -> POSITIONS (parallel, 4 threads; evidence ids validated, re-ask once, then safe default; Safety veto honoured only if the what-if shows a breach; Quality lock becomes a `protect` lever on every option; Shift Lead capacity truth) -> AMEND (1 round) -> SCORE (code, 10.4 formula + feasibility) -> DECIDE (chair, reasoning on; vetoed/infeasible options blocked; deviation from top score needs a rationale; tie within 2% / all vetoed / director lever -> ESCALATE to Dana) -> COMMIT (authority matrix: issuer per lever, absent owner hired and asked to accept, decision_id stamped). Quiet day = one Nano call for Megan's note + the standing plan. `pick_thresholds` = one chair call clamped to the template bounds. `decision.predicted` (first-day `Kpis` of the chosen what-if) is attached for the drift detector. `tier_for` maps ultra -> super while `MODEL_ULTRA` is empty.
- `org/board.py`: the Shared Brain snapshot (floor view, yesterday actual vs predicted, visible events, constraints/locks, last 5 evidence rows, inbox, who is in the room, drift flags) rendered under ~1,500 tokens; `StanceMemory` keeps each role's last 3 positions.
- `org/interpreter.py`: Event Interpreter (Nano) turning inbox text into a validated `Event` (args checked against `EVENT_ARGS`, confidence >= 0.5). Wired into `engine/events.parse_inbox` when the policy has an LLM.
- `org/playbook_select.py`: `select_rules` by role and tag overlap (max 5), used for playbook v1+.
- Store: `update_option` for scores/feasibility/vetoes.

**Tests** - `tests/test_standup.py` (15, one live), `tests/test_board.py` (4), `tests/test_interpreter.py` (5) - suite total 142 + 1 live
- Fake-LLM tests (a scripted test double for the client, not a product mock): quiet day = exactly 1 LLM call and the standing plan; rack damage day hires Safety, produces >= 2 options with evidence, positions cite evidence ids, decision is feasible; unauthorized lever rejected and logged; bogus evidence id re-asked then fallback; veto on a breaching option blocks it; all-infeasible -> least_bad + escalation; quality lock adds `protect`; `predicted` readable; determinism with identical answers.
- Board snapshot stays under 1,500 tokens with 50 evidence rows and 30 events. Interpreter: valid parse, low confidence -> None.
- **Live test (GEMBA_LIVE=1, Crusoe):** one standup on the stub at day 66 with rack damage: 8 LLM calls, 0 fallbacks, 16.9 s wall (23.1 s summed, positions parallel), **$0.0066**. Luke proposed reslotting despite the damage, Sam proposed `close_aisle(5, 2d)`, positions 2 oppose / 1 support all citing evidence, Megan chose the close and Sam issued it.

**Resolutions:** tie = within 2% of the larger score (floor 0.01); a `director_escalation` event always routes to Dana; Dana may pick a vetoed option (override) but never an infeasible one while a feasible exists; a lock's `protect` is added after evidence without re-running what-ifs; inbox events are effective the same day.

**Known:** on the stub, a 200-move night pushes mis-picks to ~0.53% (> 0.5 target), so the following day is never quiet. Stub calibration, not standup logic; the real twin decides this.

### First live 10-day org run on the stub (run `bt-7c20b8aa`, days 61-70, customer_first) - 12:50
- 6 standups, 1 escalation meeting, 3 quiet days. 64 LLM calls, 2 safe-default fallbacks, 0 cache hits (fresh run), **$0.0745** (about $0.22 per 30 days, well under the $1.50 cap), 251 s summed latency (positions run in parallel, so wall time is lower).
- 55 utterances, 24 evidence rows, 18 cascade edges. Hires with triggers: Marcus (labor_shortage, day 63, released 65), Dana (director_escalation, 64, released 66), Sam (rack_damage, 66, released 68), Tom (demand_surge, 67), Ellen (key_customer_week, 69).
- Decisions: reslot 200 on quiet days; pause on the labor shortage (Megan, then Dana on the escalation); `labor_allocate` 12 forklifts to swing on day 65; `close_aisle(7, 3d)` on the rack damage; `protect KEY1` for the key-customer week. Every rationale cites the option score and KPIs.
- Daily on-time check failed every day: the stub's baseline on-time is 95.5 against a 97 target, so this is stub calibration. The real twin's calibration target (94-97 on normal days) means the daily check threshold (target - 2 = 95) is where the org will actually be judged.
## Integration with A at 13:00 - not started
## Phase 4 - eval harness, report, train/test scenarios (B10) - done 12:50 (hero run waits for the real twin)

**Built**
- `eval/run_eval.py`: `run_contender` / `evaluate` over (scenario, contender, seed). Do-nothing always runs first per scenario+seed because it is the scoring REF; static always runs so the "beats static" column exists; crew runs (`crew` = org with playbook v0, `crew_vN` = org with that playbook's rules) go through a thread pool capped at `--parallel`. Writes `eval_results`. CLI: `python -m eval.run_eval --scenario --profile --contenders --seeds [--days] [--parallel] [--out]`; `make baselines` is LLM-free.
- `eval/report.py`: Markdown table per scenario and overall with the spec 15 columns (score, score_vs_static, lph_lift_pct, on_time, mispick, violations, near_misses, overtime, progress, llm_usd, meetings_held, hires), best value bold, every metric reported even where a baseline wins; `gate_summary` implements the 13.2 gate rule.
- `scenarios/train_1..3.yaml` (seeds 11-13) and `test_1..3.yaml` (seeds 21-23): 10-day windows, different event mixes and timings; `test_1` has `wms_sync_delay` and `test_3` has `forklift_breakdown`, neither seen in training.

**Tests** - `tests/test_eval.py` (6): all six scenarios validate; unseen kinds in test; three baselines on the stub produce rows with every metric, do-nothing scores ~0, static's `score_vs_static` is 0; rows land in `eval_results`; table bolds; gate rule; CLI prints.

**Measured (stub, 30 days, seed 1):** do_nothing 0.000 / big_bang -0.002 (13 violations) / static 0.033 (progress 100, 39 violations). Stub numbers.

**Pending:** the hero run and `recordings/hero.db` need the real twin; the eval table with `crew` is a live run (about $0.22 per 30 days).

## Phase 5 - retro coach, playbook, gate (B11) - done 12:55

**Built**
- `learn/playbook.py`: `load_rules` / `save_rules` on the `playbook_rules` table (v0 = no rules; a candidate version loads its candidate rows until promoted), `merge` (cap 12, duplicates by role + tag overlap + advice similarity keep the larger score_delta, rules that never fired are dropped), `tags_for_day` with the same tag vocabulary the standup uses (event kinds, bare KPI names, drift kinds), `fired_counts` from the logged prompts.
- `learn/retro.py`: spec 13.2 verbatim. `regret_days` (largest scorecard drop vs the do-nothing REF day, plus failed daily checks) -> `trace` through `cascade_edges` back to the originating `dec:` node (falls back to the next consequence events, then the latest decision with levers) -> `coach` (reasoning on; `CoachProposal(alternative_levers, lesson, why)`; fallback pause + generic lesson) -> `verify` by `fork_run` at the decision's day with the alternative forced, trimmed to `--horizon`, accepted iff score over the remaining days improves and hard-limit breaches do not increase -> candidate rules saved under the next version. One fork per originating decision. CLI `python -m learn.retro --train train_1,train_2,train_3 --version v0 --horizon 5 --max-regret-days 2` (`--playbook` alias for the Makefile).
- `learn/gate.py`: runs `crew` (v0) and `crew_vN` on the test scenarios via `evaluate`, applies `gate_summary`, promotes candidates to active or marks them rejected, records an `eval_results` row with contender "gate". CLI `python -m learn.gate --test test_1,test_2,test_3 --from v0 --to v1`.
- Engine: `fork_run(..., horizon=, policy_kwargs=)` so org forks reuse the retro's LLM, store, profile and rules. Store: `playbook_rows`, `replace_playbook_rules`, `set_playbook_status`.

**Tests** - `tests/test_playbook.py` (9), `tests/test_retro.py` (9) - suite total 170 + 1 live
- Merge cap and duplicate handling; v0 empty; save/load; tag vocabulary.
- NaiveBigBang parent on the stub (days 61-66) -> regret on day 62 -> trace to `dec:...-d61` -> fake coach proposes `pause_reslot(2)` -> fork at day 61 (3-day horizon, `parent_run_id` set) scores higher -> lesson accepted, candidate row under v1 with normalised tags. A "push harder" proposal is rejected and writes nothing. Coach fallback path. Gate promoted / not promoted / rejected statuses (with `evaluate` stubbed).

**Cost estimate for the real retro (3 train runs, horizon 5, 2 regret days each):** about 125-200 LLM calls plus about 150 for the three 10-day train runs; well under $1.

**Known:** the engine writes `caused_by` edges out of `kpi:` nodes (C6) but nothing points into them, so the trace from a regret day's KPI node relies on the fallbacks. Fine for the demo; a `day_results -> kpi` edge would make it exact.

## Integration with A - started 13:15

**13:15 merged.** A's twin, data and console rebased with B's review fixes: **265 tests + 1 live** green on `main` (`24f502a`). `make_twin("auto")` picked up `floor.twin.Twin` with no code change; `runs.twin` records it.

**13:18 first real-twin run** (`bt-6b997749`, StaticRules, 30 days, seed 1): **10.4 s wall**, status done. Mean on-time 87.1, mis-pick 0.38, LPH 82.8, progress 92.4, labor $536,589. Consequences fired: `director_escalation` (65), `replen_alert` (65, 66). 88 failed daily checks (mostly on-time < 95 on surge days).

**Findings for A (floor/):**
1. Progress plateaus at 92.4% from day 81 although static requests 200 moves every night from day 83 (`moves_done` = 0 on days 83-90). Either the remaining ~170 moves are blocked (frozen/protected slots, staging) or `phaser.select_tonight` returns nothing. A's own loop reported 96.6% by day 90, so something differs when driven through `engine.run` (events delivered once on their start day; `pause_reslot` on event days incl. consequence events).
2. Days 89 and 90 have identical KPIs (on-time 85.5, mis-pick 0.36, LPH 84.6, 8 jam hours, pm 1.30). Different demand should not give identical results.
3. The day-69 demand surge (x3, 5 days) drives on-time to 69-76% with 2-3k stockout lines/day and 9-10 jam hours. It is the trap biting, but the scorecard sees the 87% mean; worth a look at whether x3 for 5 days is the intended severity.
4. Static pauses on `replen_alert` (a consequence event) as well as on scripted events. If baselines should only pause on exogenous events, say so and B changes `StaticRules`.

**13:30 integration gate (spec section 20, phase 3) - all four pass on the real twin**
1. `make run POLICY=static` finishes 30 days (10.4 s).
2. A cascade chain is in `cascade_edges`: big bang (`ev-98548726`) -> `staging_congestion` (caused by `dec:...-d61`) -> `customer_escalation` (caused by `dec:...-d61`) -> `director_escalation` (caused by `kpi:...:64:on_time_pct`).
3. Fork at day 69 (`fk-148294a2`, forced `pause_reslot(5)`): identical to the parent on all 8 days before 69, different on 21 of 22 days after; progress held at 37.0 on day 70 while the parent moved on.
4. Hero cascade fires under big bang (A's `test_floor_twin.py`, seeds 1-3; seen here on seed 1).

**First real eval table** (`eval.run_eval`, reslot_default, customer_first, seed 1, 26 s for three 30-day runs):

| contender | score | score_vs_static | lph_lift_pct | on_time_pct | mispick_rate | violations | progress |
|---|---:|---:|---:|---:|---:|---:|---:|
| do_nothing | 0.000 | -0.404 | 0.00 | 85.58 | 0.389 | 85 | 0.0 |
| big_bang | 0.169 | -0.235 | 19.05 | 84.49 | 0.406 | 93 | 57.5 |
| static | **0.404** | 0.000 | **20.13** | **87.08** | **0.376** | 87 | **92.4** |

Static beats do-nothing by +20% LPH, as spec 9.3a target 4 expects. Near-misses are 0 for every contender (safety never bites on seed 1); violations are dominated by on-time < 95 on the surge days. Cosmetic: the CLI prints the `static` row twice; to fix in the polish pass.

**13:38 first live org run on the real twin** (`bt-c71e8d99`, 30 days, seed 1, customer_first; stale 12:36 `inputs.db`): **19.2 min wall, 219 LLM calls, $0.242**, 26 standups + 1 escalation + 3 quiet days, 20 hires (shift lead, quality, replenishment, safety, director; triggers logged), 19 reslot / 8 pause / 3 staging_clearance decisions. Mean on-time 88.7, mis-pick 0.37, LPH 82.2, progress 93.1 (old progress accounting). 6 fallbacks, all `APITimeoutError` on chair/director reasoning calls at the 20 s client timeout -> reasoning calls now get 90 s (`LLM_REASONING_TIMEOUT_S`). A observed the chair pausing on unbreached limits (6 of 10 days in A's own run): the chair/director prompts and the decide task now say feasibility is code-checked, a paused night delays the reslot, and ties break toward progress; options show the progress delta vs REF. Hero candidate relaunched 13:39 on the regenerated `inputs.db` with these fixes; retro train runs (crew v0 on train_1..3) started in parallel.

**13:50 flag for A, corrected 14:05 after the QA cross-review:** the `safety_risk_max = 1.10` on reslot what-ifs is **not a constant**. It is a real night-1 peak for the second value-ordered batch (moves #201-400), quantised (people / safe limit 3) and concentrated in one aisle; predicted equals actual on every executed night (c71 d80 1.067/1.067, d82 1.0/1.0, hero d75 0.667/0.667). From the hero d61 snapshot, `reslot(200)` gives [1.10, 0.43, 0.36] by day while `reslot(100)` gives 0.42 (feasible), and it is non-monotonic in n_moves (c71 d75: 90 legs 1.233, 179 legs 1.067). So the twin is honest; the org's failure was that every engineer proposal was exactly 200 legs and nobody tabled a smaller batch (fixed on B's side: a code-generated half-batch option when the reslot breaches). A-side items from the review, in A's order of value: (1) consider spreading a night's moves across aisles so one aisle does not carry the whole peak; (2) `staging_clearance` has no effect in the what-if (identical KPIs in 20+ cases); (3) `labor_allocate.headcount` is absolute and undocumented (12 = current swing PM = no-op; 4 pushed PM load to 2.2); (4) rack_damage barely moves REF risk (0.493 both ways) so `close_aisle` shows no benefit; (5) `whatif` silently accepts levers `apply_levers` later rejects (bad zones/ids give 0 moves) - returning rejections would let the org see them; (6) PM peak zone load on the stale layout is 1.17-1.71 against the 85-100% calibration target and the 1.10 limit, which puts KPIs out of band almost daily; (7) leadership_visit lists aisles but `reslot_tonight` only excludes zones; (8) REF `whatif([])` differs from `pause` when moves are partially queued (ev-784 d63) - which is intended?

**13:55 hero candidate on fresh data** (`bt-36957ece`, 30 days, seed 1, customer_first, Super for every tier): **13.9 min wall, 179 calls, $0.175**; mean on-time 95.7, mis-pick 0.42, LPH 84.5, **reslot 100% complete**; 10 quiet days, 18 standups, 2 escalations; 19 reslot / 14 pause / 6 staging_clearance / 2 protect decisions; hires: shift lead (63, 70), director (65, 67, 70, 72, 75, 78, 83, 85), safety (66, 70), replenishment (67, 83), quality (69, 71, 82). Not the recording yet: 35 fallbacks, all `RateLimitError` (it overlapped the three train runs; client now retries 429s and live jobs will be serialised), and the 14 pauses are the what-if safety constant. Rerun cleanly after A's fix.

**13:50 retro train runs** (crew v0 on train_1..3, 8.7 min, 3 processes): crew score 0.333 +- 0.217 vs static 0.337 +- 0.226; on-time 90.3 vs 90.0; progress 42.7 vs 67.8 (the pausing). Rate-limit fallbacks throughout. Used for a dry run of the Retro Coach only.

**Standup fixes from the day-63 review of train_1** (`ed3140a`): contradictory lever combinations normalised (pause vs reslot), same-outcome options no longer count as a 2% tie (that was escalating to Dana daily), breaches rendered as value vs limit, empty alternatives logged as "no alternative", 429s retried.

**14:05 Retro Coach dry run on the three train runs** (`learn.retro --horizon 3 --max-regret-days 1`, 5.8 min): each run's worst regret day traced through `cascade_edges` to its decision; the coach (Super, reasoning on) proposed an alternative + lesson; each was verified by a fork at that day; **3 accepted / 0 rejected** (deltas +0.022, +0.005, +0.009; no new hard-limit breaches); 3 candidate rules under v1: chair "allocate extra labor to the affected pool before scheduling reslot work" (labor_shortage), chair "prioritise reslot moves in the affected zone to restore capacity before peak" (pm_zone_down), safety "close the damaged aisle and suspend reslot moves there until repaired" (rack_damage). Gate dry run (crew v0 vs v1 on test_1..3) started 14:06.

**Eval fix** (`6d71a78`): `violations` now re-evaluates every run against the template thresholds. The hero candidate's chair had set its own on-time check at 97 (template 95), so the crew was charged 26 extra "violations" for judging itself more strictly. Corrected comparison on fresh data: crew 0.581 vs static 0.559 score, on-time 95.7 vs 95.3, both 100% progress, violations 39 vs 21 (PM load over 1.10 for 21 days vs 5, the cost of the pauses), LPH lift 18.6 vs 20.5.

**14:06 QA cross-review fixes (`cea7006`, 281 tests)** from an Opus pass over 63 live meetings: code tables a half-batch reslot when every reslot option breaches a hard limit (the org no longer pauses for a week when 200 legs breach and 100 would pass); a pause is always tabled when nothing feasible exists and a "REF"/"do nothing" pick maps to it; lever ids and values are validated before the what-if (`C001-C200`, `S0001-S2000`, zones 1-6, aisles 1-28, pools, headcount) with the reason in the transcript; every present role with levers proposes an alternative (shift lead's `labor_allocate` now appears on a labor shortage); the chair's escalate flag needs a code-detected reason (22 of 23 earlier escalations were ties between identical what-ifs, 9 more had no reason); chair fallback is the top feasible option; fallback positions leave stance memory and the chair's view; stance memory strips numbers (a nano-tier role had repeated "93.2%" for four days); lever help uses placeholders; hire triggers cover active events (Quality stays through the key-customer week); LLM workers default 2. A fixed the twin side at the same time (`fccbe90`: night safety double-counted legs in both aisles, which made every reslot what-if infeasible; `2d62ffa`: night dispatch cap).

**14:17 clean cycle (restarted on the merged twin `d0c015e`)**: hero `bt-abe8133f` done 14:38 (20.6 min, 224 calls, $0.33, 8 fallbacks; 22 standups / 7 quiet / 1 escalation). Two org faults found in it and fixed (`818f6dd`): options like `resize_slots` alone silently replaced the night's reslot (8 nights lost), and on day 77 the org set `labor_allocate(night, night_move, 0)`, zeroing its own night crew for the rest of the month with no feedback. Now: additive options carry the standing reslot, headcount must be 1..roster cap, the lever help shows current headcounts, and the brief warns when planned moves did not execute. Train runs started 14:38 on the fixed org; retro and gate follow. A fresh hero for the recording runs after the cycle.

**14:40 Version N+1 integrity review (Opus, read-only):** train/test separation holds (3 train, 3 unseen test, two event kinds never trained on); lessons do reach v1 prompts (22 of 84 calls in a v1 run carried rule advice, 0 of 74 in v0) by role + tag, and the cache cannot serve a v0 answer to a v1 prompt; the gate rule matches spec 13.2. Two blockers in verification, being fixed before the retro step runs: (1) a lesson was accepted from a fork whose alternative lever the twin rejected (the fork never applied it); (2) acceptance was `delta > 0` with no margin and no check for rate-limit fallbacks in the window. Fixes: the fork must have applied the alternative; delta > 0.01; no fallbacks in the fork or parent window; coach-fallback lessons never enter the playbook; fresh do-nothing REF per retro; fork day excluded from breach counts; the gate records fallbacks per side and gives no verdict if any. Stage caveats: 3 scenarios x 1 seed, 5-day forks: "evidence", not "proof"; coach on Super; numbers only from the clean-cycle gate row; if v1 does not win, the Done Means Done pitch (spec 21). The headline chart (13.3) is not in the console; the gate CLI table is the exhibit.

**15:07 cycle pass 1 result and restart.** Train runs (3 x 10 days, 7.8 min) done; retro under the strict rule rejected every lesson: +0.035 (8 fallbacks in the fork, 2 in the parent window), +0.012 (3 in the parent window), +0.007 and +0.007 (below the 0.01 margin), -0.010. Every fallback was a Crusoe 429: the request cap is per key and both machines share it; A agreed to hold live org runs (COMMS A-6). Pass 1 cancelled before its gate (v1 would have been empty); its orphaned runs marked failed. Pass 2 started 15:07 on final code, serialised (hero -> train -> retro -> gate, one process at a time, 2 LLM workers, 6 retries on 429, serial positions after the first 429).

**15:12 why a customer-first org pauses (not a bug).** Pass-2 hero `bt-cb97aeb0` day 64: both options feasible; pause scored 0.028, reslot 200 scored 0.017. A 200-leg night raises mis-picks by 0.046 pt for 3 days (learning dip), which customer_first weights 0.25 over a 0.5-pt scale (-0.023), more than the +0.009 the extra 9 pp of progress earn on a 3-day horizon. The chair took the top score. Ryan's "1.36 > 1.1" was a false breach (REF is 1.36 too; the options text now says hard limits are judged against REF, `ded74de`). Under efficiency_first (progress 0.20, mis-pick 0.05) the same night wins, so an efficiency-first hero was started in parallel (limits bumped, 0 fallbacks so far) for the "compare company priorities" beat (spec use case 3, decision 11). Known design limit worth saying on stage: the option scorecard sees the learning-dip cost at once and only 3 days of the progress benefit.

**14:45 Must-list verification (Opus, separate store):** 296 tests; traps ALL PASS; calibration 4 of 5 (PM peak 1.34 is trap 5 by design); consequence chains root in `dec:` nodes under big bang; every console endpoint 200; determinism 30/30 days; eval table static 0.559 vs crew (hero) 0.598 (+0.039) but with 37% progress (the night-crew fault, fixed). Gaps fixed on B's side: `scenarios` added to `pyproject` packages (the describe endpoint 503'd outside the repo root), `reslot_tonight` help says zones are Pick Module zones not aisles (13 rejections of "zones [7]"), PM-load feasibility gets a 5% band over an already-breaching REF (Sam's `close_aisle 7` at 1.38 vs REF 1.33 was rated infeasible, so the chair paused instead of closing the aisle), `engine/recording.py` exports a run + forks into `recordings/hero.db`. **For A:** the `protect_pm` fork preset (protect zones 1-6) changes nothing on a static parent because `phaser.select_tonight` excludes only destination zones while the remaining moves go *out of* the PM (0 identical-vs-different days on 30; `pause_3` diverges on 21) - skip moves whose source zone is excluded, or switch the demo preset; the `constraints` table stays empty when Ellen locks (the lock is a `protect` lever the twin tracks in `FloorView.protected`), so the board drawer should read protections from the day's levers/view; cascade sentences say "Megan chose" for director and big-bang decisions; a consequence can be dated day 91.

**Answers to the console lane's questions:**
1. `drift_flags.response` decoding: fixed on B's side (`_decode` is tolerant).
2. Constraint `owner_role`: now `args["owner_role"]` if the constraint event carries it, else `decided_by`. The org sets it for locks.
3. `utterances.role` is the **role id** (`program_manager`, `quality`, ...), not the first name. Map with `org.roles.load_roles()[id].name` (Megan, Ellen, ...). Stable across renames.
