# Gemba demo workflow: a reslotting engineer, end to end

Every command, URL and click below exists in this repo as of commit `86ef905`. Measured numbers come from `PROGRESS_A.md` / `PROGRESS_B.md` (seed 1, real twin). Spec references are to `PLATFORM_SPEC.md`.

## 1. Who and when

You are the reslotting / industrial engineer two weeks before a rolling reslot. Slotting software gave you the target layout (`slotting_target`, 1,111 changed active slots, ~2,200 legs). You have two months of order history (days 1–60).
You want to rehearse the held-out month (days 61–90) with disruptions in it before the floor lives through it.

## 2. Setup in 5 minutes

```
cp .env.example .env      # CRUSOE_API_KEY, CRUSOE_BASE_URL, MODEL_ULTRA, MODEL_SUPER, MODEL_NANO
                          # optional: INPUTS_DB_URL (default sqlite:///inputs.db), RUN_STORE_URL (default sqlite:///runs.db)
make install              # uv sync --extra dev
make data SEED=1          # python -m data.gen --seed 1 --verify
make console              # http://localhost:8000
```

`make data` writes `inputs.db` (repo root, ~134 MB, about 6 s): 90 days, 180,000 orders, 2,136,415 lines, 2,000 SKUs, 28 aisles, 6 Pick Module (PM) zones, labor roster and calendar, current and target slotting. The target is built from days 1–60 only. `--verify` measures the 8 traps on the written file and exits 1 if one fails. Seed 1 values:

| Trap | Measured |
|---|---|
| Zipf velocity (top 20% SKUs' share of lines) | 0.790 |
| Family co-ordering, P(other member \| member) | 0.625 |
| Month-3 shift (seasonal ×2.90, fading ×0.297) | yes |
| Stale layout (top-200 SKUs in far aisles) | 0.445 |
| Tight PM (peak zone load) | 1.223 |
| Rush orders | 4.98% |
| Pallet lines | 3.0% |
| Key customer concentration (C001 share of lines) | 8.1% |

Re-check an existing file with `uv run python -m data.verify --db inputs.db`. To view without spending anything: `make demo` replays `recordings/hero.db` once it is committed (check `ls recordings/`), or build the hand-made store with `uv run python -m console.fixture --out fixtures/demo_runs.db` and run `RUN_STORE_URL=sqlite:///fixtures/demo_runs.db make console`.

## 3. Workflow A: rehearse the month (backtest)

```
make run POLICY=org PROFILE=customer_first        # scenario reslot_default, playbook v0, seed 1
make run POLICY=static                            # LLM-free reference, ~10 s for 30 days
```

`reslot_default` (`scenarios/reslot_default.yaml`) is the month: d63 forklift shortage 20% for 2 days, d66 rack damage in aisle 7, d69 seasonal surge ×3 for 5 days, d72 key-customer week for C001, d75 PM zone 2 down for 4 h, d78 leadership visit (no moves in aisles 10–14), d82 WMS sync delay, d86 returns spike. Trim the window with `--days 61-70`.

What happens unattended, day by day: code builds the board (the Shared Brain), decides whether today is quiet, and hires on-call roles from triggers (Sam for rack damage, Marcus for labor, Tom for the surge, Ellen for the key-customer week, Dana on escalation). Megan chairs the standup. Luke proposes and others table alternatives (max 3 options). Every option gets a `twin.whatif` plus a do-nothing reference. Positions cite evidence ids. Code scores the options and checks feasibility. Megan decides, and the levers go to the floor for tonight. On a quiet day the standing plan continues with one Nano call.

Measured cost on the real twin: 30 days took 13.9 min wall, 179 LLM calls and $0.175 (`bt-36957ece`, reslot 100% complete, on-time 95.7). An earlier run took 19.2 min, 219 calls and $0.242. Plan for ~14–19 min and $0.18–0.25 per 30 days. The profile cap is $1.50. Do not run two live org runs at once: they share the Crusoe rate limit.

Watch it in the console: `http://localhost:8000/?run=<id>` (with no `run=`, the newest non-fork run opens). The stream tails a running run. Speed buttons are 1×/4×/16× sim-hours per second, space plays or pauses, and ←/→ steps a day.
- **Floor**: 28 aisles coloured by load, hatched when closed, PM zones as % of capacity, consolidator meter, staging bar, dock with the next cutoff. Night slot moves animate old slot → new slot.
- **Meeting room**: who is in the room (hired badges), "What just happened", the transcript, and the options dock.
- **Timeline**: days 61–90 with event / decision / standup markers and four KPI lanes with target lines. Failed done-checks show as ticks. Click a day to jump there.
- **Board** (header button): the snapshot the agents see, with the floor view, actual vs predicted, events, locks, recent evidence, inbox and drift flags.

How to read a decision: once the meeting closes, the DECISION banner is the room's title, with the plain-language effect first (for example "aisle 7 closed for 5 days; slow night: 120 slot moves tonight") and the raw levers underneath. Each option card shows its letter, proposer, score bar vs the do-nothing reference and feasibility. Hard limits come from the profile: `safety_risk_max` 1.0, `pm_peak_zone_load` 1.10, near-misses 0.5. A Safety veto puts a "VETOED by Sam" stamp on the card and a red ring on Sam's seat. A Quality lock shows a padlock and a LOCKED chip, and adds a `protect` lever to every option. If the chair picks something other than the top score, the banner says so ("A vetoed by Sam, so B") and "show why" opens the rationale.

Done-checks and drift: code builds the checks from the profile targets and hard limits, the chair may tighten them within code-set bounds, and the verifier checks them every day (`checks` table, failed ticks on the timeline). Drift detectors raise `repeat_whatif` (continue), `plan_churn` (replan), `stall` (replan), `prediction_error` (recalibrate the forecast) and `overrun` (shrink). The chair responds to each one at the next standup.

The decision record is the run store (`runs.db`, or `recordings/hero.db` for the recording): `decisions` (levers, decided_by, rationale, deviates_from_top_score, caused_by), `utterances` (who said what, evidence_ids, option_id), `evidence` (what-if spec, KPIs, scorecard), plus `options`, `meetings`, `hires`, `checks`, `drift_flags` and `cascade_edges`. Export example:

```
uv run python -c "import sqlite3,csv,sys; c=sqlite3.connect('runs.db'); w=csv.writer(sys.stdout); [w.writerow(r) for r in c.execute(\"select day,decided_by,levers,rationale from decisions where run_id='<id>' order by day\")]" > decisions.csv
```

## 4. Workflow B: fork a decision (what-if)

Console: open the run, click **Fork**, set "Fork at day" (default 69), pick one of three presets and click **Fork and stream**. The child run opens and streams. CLI equivalent:

```
make fork RUN=<run_id> DAY=69 LEVERS='[{"kind":"protect","target":"zone","ids":["1","2","3","4","5","6"],"days":7}]'   # protect the Pick Module all week
make fork RUN=<run_id> DAY=69 LEVERS='[{"kind":"hire_temps","count":6,"start_day":69,"days":10}]'                        # hire 6 temps
make fork RUN=<run_id> DAY=69 LEVERS='[{"kind":"pause_reslot","days":3}]'                                                # pause 3 days
```

The child copies the parent's rows before day 69 and resumes from the parent's day-68 snapshot. On day 69 the forced levers replace the meeting, and from day 70 the policy decides normally. A fork of an org run therefore makes LLM calls for the remaining days. Pass `--policy static` to `python -m engine.fork` for a free one. Lever schemas are in `contracts.py`. The 11 kinds are reslot_tonight, pause_reslot, labor_allocate, overtime, hire_temps, protect, resize_slots, wave_priority, close_aisle, reopen_aisle and staging_clearance.

Measured: the fork at day 69 with `pause_reslot(5)` matched the parent on all 8 days before 69 and differed on 21 of 22 days after.

Compare: click **Compare**, pick Run A and Run B (or open `/?compare=<a>,<b>`). You get synced timelines, per-day KPI deltas, a one-sentence verdict from the totals and the first divergent decision. API: `GET /compare?a=&b=`.

Cascade trace: click a red consequence marker on the timeline. The drawer opens with the chain as one sentence and a left-to-right graph built from `cascade_edges`, for example big bang → `staging_congestion` → `customer_escalation` → `director_escalation`. Click a node to re-centre. API: `GET /cascade/{run}/{node}`.

## 5. Workflow C: compare company priorities

```
make run POLICY=org PROFILE=customer_first
make run POLICY=org PROFILE=efficiency_first
```

Then **Compare** the two run ids. The scorecard is one formula (spec 10.4) with profile weights (`profiles/*.yaml`):

| KPI | customer_first | efficiency_first |
|---|---:|---:|
| on_time_pct | 0.35 | 0.15 |
| mispick_rate | 0.25 | 0.05 |
| lph | 0.20 | 0.35 |
| reslot_progress_pct | 0.10 | 0.20 |
| labor_cost_usd | 0.10 | 0.25 |

Targets (on-time 97, mis-pick 0.5, LPH lift 8%), hard limits and budgets (120 overtime h/week, 8 temps, $1.50 LLM) are the same in both profiles. Only the trade-off changes. Expect efficiency_first to push more legs per night and spend less on labor, and customer_first to pause or protect around C001 and the surge. The compare view shows what each priority costs in on-time, LPH, labor and progress. For a cheap, scripted side-by-side, run `make baselines PROFILE=efficiency_first`.

## 6. Workflow D: inject a disruption in plain words

**Inbox** (bottom bar): type "Aisle 12 closed, forklift hit the upright" and click Send (`POST /runs/{id}/inbox {text, day_hint}`). The Event Interpreter (Nano, `org/interpreter.py`) turns the text into a typed `Event`. It checks the args against `contracts.EVENT_ARGS` and drops anything below confidence 0.5. The org sees the event at the next simulated morning's brief, and a pending chip shows until then. The event then triggers hires and options like a scripted one. This only works on a run that is still running live; a finished or recorded run never consumes the message.

**Scenario dialog** (header **Scenario** button): type a description into "Describe in plain words" (for example "peak week with 20% absences on nights, rack damage in aisle 7 on day 66") and click **Draft from words**. `POST /scenarios/describe` sends it to `org/scenario_designer.design_scenario` (temperature 0, cached; it keeps exogenous kinds only, keeps events inside 61–90, and runs one repair round). The draft fills in the id, seed, days, events, perturbation and headcounts, and the YAML pane shows what will be saved. Edit anything, choose a profile and policy (org / static rules / do nothing), then click **Save** or **Save & run**. Save goes through `POST /scenarios`, which runs the loader rules, the exogenous-kind check and `contracts.Scenario`, then writes `scenarios/<id>.yaml`. It refuses the protected `train_*`, `test_*` and `reslot_default` ids. Save & run then calls `POST /runs` and switches to the new stream. With `GEMBA_API_TOKEN=secret make console`, the three write endpoints need the token, which goes in the dialog's API token field.

NemoClaw Scenario Designer: Person A is building the sandboxed version (META_HARNESS §3/§5) on a Mac. It is the same designer running in an OpenShell sandbox, with egress only to Crusoe and the Gemba API and the console endpoints as its only interface. It cannot reach `/inbox`, levers or the databases, and it cannot overwrite the train/test scenarios.

## 7. Workflow E: let the org learn (Version N+1)

Baselines table (LLM-free, about 26 s for three 30-day runs):

```
make eval                                   # do_nothing, big_bang, static on reslot_default, customer_first
uv run python -m eval.run_eval --scenario reslot_default --contenders do_nothing,big_bang,static,crew --out eval_out.md
uv run python -m eval.run_eval --scenario test_1,test_2,test_3 --contenders static,crew,crew_v1 --parallel 2
```

Columns: score, score_vs_static, lph_lift_pct, on_time, mispick, violations, near_misses, overtime, progress, llm_usd, meetings_held and hires. The best value in each column is bold, and every metric is reported even where a baseline wins. Measured on fresh data: crew scored 0.581 vs static 0.559, on-time 95.7 vs 95.3, both at 100% progress. Static won on violations (21 vs 39) and LPH lift (20.5 vs 18.6). Rows land in `eval_results`.

Retro Coach on the training scenarios (`train_1..3` are 10-day windows with seeded perturbations):

```
uv run python -m learn.retro --train train_1,train_2,train_3 --version v0 --horizon 5 --max-regret-days 2
```

For each train run the coach:
1. Finds the regret days (biggest scorecard drop vs do-nothing, or a failed check).
2. Traces `cascade_edges` back to the originating decision.
3. Has the coach (Super, reasoning on) propose an alternative lever set and a general lesson (role, tags, advice).
4. Verifies the lesson by forking the run at that day with the alternative forced. The lesson is kept only if the scorecard delta over the horizon is above 0 with no new hard-limit breach.

Accepted lessons become candidate rows under v1, capped at 12 rules, with duplicates merged and rules that never fired dropped. In the dry run (horizon 3) 3 of 3 lessons were accepted, with deltas +0.022, +0.005 and +0.009. For example, Safety: "close the damaged aisle and suspend reslot moves there until repaired" (tag rack_damage), and the chair: "allocate extra labor to the affected pool before scheduling reslot work" (labor_shortage).

Gate on unseen scenarios (`test_1..3` include an event kind never seen in training):

```
uv run python -m learn.gate --test test_1,test_2,test_3 --from v0 --to v1
```

The gate runs `crew` (v0) and `crew_v1` on the test set. v1 is promoted only if the mean test score improves and mean violations do not increase (`eval/report.gate_summary`). Otherwise the candidate rules are marked rejected. The result is recorded in `eval_results` with contender `gate`. The full clean-cycle gate result is pending in `PROGRESS_B.md`.

Read the rules:

```
uv run python -c "import sqlite3; [print(r) for r in sqlite3.connect('runs.db').execute('select version,role,tags,advice,score_delta,status from playbook_rules')]"
```

Use them with `make run POLICY=org PLAYBOOK=v1`. The engine loads that version's rules, and each day `org/playbook_select.py` injects up to 5 rules into the matching role's prompt by tag overlap with today's events, KPIs and drift (tags, not embeddings, so every rule is auditable).

## 8. Workflow F: copilot at the daily standup (future, NOT BUILT)

Spec 17 describes this mode; none of it exists in the repo:
- A nightly job loads yesterday's actuals (orders, order lines, completed moves, labor attendance) into the input tables.
- The engine runs one day ahead, and the morning brief plus the recommended option land in the console before the real standup.
- The engineer approves or edits the plan, and approved moves export to the WMS as tasks.
- Each day of actual vs predicted recalibrates the twin.
- What exists today is only the stand-in: the Inbox, where a disruption typed during a run is re-planned live (Workflow D).

## 9. Honesty notes

- The data is synthetic, shaped on a real site, with day-level and hourly approximations. We claim the mechanism and the comparisons, not real-world savings.
- What-ifs use a forecast built from visible history (same-weekday resample of days before today) and announced events. Only execution uses the actual held-out orders. The target layout uses days 1–60 only.
- Agents rehearse plausible positions backed by simulator numbers. They do not predict what real people will do.
- When the store path is under `recordings/`, every API response carries `recorded: true` and the header pill reads "Recorded · replaying". Say so on stage. A running engine shows "Live".
- The recording was made on the 14:07 code. Later twin changes (rack damage now marks an aisle damaged rather than closing it, `staging_clearance` effect, whatif raising on invalid levers) apply to live runs and forks made on stage.
- Static rules win some columns (violations, LPH lift). Show the whole table.

## 10. Three-minute stage script (spec 22)

Before the demo: run `make demo`, open `http://localhost:8000/` in the dark theme and check the "Recorded · replaying" pill. Keep one live store (`make console`) ready for the fork if you want it live.

| Time | Spec 22 beat | Console action |
|---|---|---|
| 0:00–0:20 | Title, then the floor | Hero run open at day 61 with playback paused. Point at the floor: aisles, PM zones, dock cutoff. |
| 0:20–0:50 | Standup streams, Sam called in after rack damage, veto, aisle 7 | Click day 66 on the timeline: Sam's hired badge, the "VETOED by Sam" stamp on option A, the decision banner, aisle 7 on the floor. Say it is the recorded run. |
| 0:50–1:20 | Cascade trace | Click day 67: Ellen's LOCKED chip. Click the red consequence marker and read the one-sentence chain, then the graph. |
| 1:20–1:50 | Fork at day 69, "protect the Pick Module" | **Fork** → day 69 → "Protect the Pick Module all week" → **Fork and stream**. Then **Compare** the fork with the hero run and read the verdict sentence and the first divergent decision. |
| 1:50–2:20 | Disruption in plain words | On a live run: type "Aisle 12 closed, forklift hit the upright" into the Inbox, then Send and point at the pending chip, which clears next morning. Or **Scenario** → describe a week → **Draft from words** → **Save & run**. |
| 2:20–2:45 | v0 vs v1 on unseen scenarios + eval table | Show the `learn.gate` output and the `eval.run_eval --out` table. Open one lesson from `playbook_rules` with the fork that proved it. Fallback: done-check ticks and drift flags on the timeline and Board. |
| 2:45–3:00 | Platform slide | "Reslotting is the first pack. Swap the twin and the org chart and it rehearses any operational change." |

Lead with the meeting room and the decisions, not the dashboard. The backup for 0:00–0:20 is the explainer film linked in spec 22.
