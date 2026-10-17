# Gemba: a decision twin for operations

**Build spec and handoff for the Crusoe Recursive Agents Hackathon, Sat Oct 17 2026.**
Owner: Sudhersan. Working name "Gemba" (lean term for "the real place where work happens"); rename freely.

This document is self-contained. A fresh agent (or teammate) should be able to read it top to bottom and start building at 10:45 AM without any other context. It supersedes `research/HANDOFF.md`, `research/locked-plan.md` and the Claude Doc "Reslotting Crew: Project Plan". Those stay as history only.

How to read it:
- Part A (sections 1–5) explains what we are building and why. Read it once.
- Part B (sections 6–17) is the architecture, module by module, from input to output.
- Part C (sections 18–24) is the build plan for the day: phases, gates, demo, pitch, risks.
- Part D (section 25) records the design decisions we made and the alternatives we rejected, so nobody re-litigates them mid-build.

---

# PART A: WHAT AND WHY

## 1. One paragraph

Gemba simulates a warehouse **and the people who run it**. A deterministic floor twin replays orders, labor and equipment hour by hour. An org twin of LLM agents (Plant Director, Program Manager, Ops Manager, Reslotting Engineer, Analytics, plus Safety, Quality, Shift Lead and Replenishment on call) holds a daily standup, argues trade-offs with simulator evidence, and makes decisions through typed levers that change the floor. Decisions have consequences, and consequences trigger new events and new decisions, so you can watch **cascades** unfold: an aggressive reslot night causes a picker learning dip, which causes mis-picks, which triggers a customer escalation, which brings Quality into the room, which locks SKUs and slows the reslot. You can fork any day with a different decision and compare the two futures. After each run, a retrospective coach finds the decisions that caused the worst cascades, proves a better alternative by forking, and writes it into the org's playbook. The next version of the org scores higher on scenarios it has never seen. The hackathon demo scenario is a phased warehouse reslot.

## 2. The problem

**Real problem (from Sudhersan's experience and industry sources):** slotting software already computes the ideal target layout. Getting there is the hard part. A rolling reslot takes 2–6 weeks for a mid-size site and months for large ones. While it runs, the warehouse must keep shipping, and reality keeps interrupting: labor shortages, demand surges, rack damage closing an aisle, a conveyor zone down, a key customer's critical week, a leadership visit. Every interruption forces a meeting where departments with conflicting goals renegotiate the plan. Reslots stall, priorities flip, and when the analyst or engineer leaves, nobody knows why decisions were made.

**Why existing simulation doesn't solve it:** digital-twin tools (FlexSim, AnyLogic, CreateASoft, Optioryx) simulate the physics of the floor. They treat human decisions as fixed inputs or hardcoded rules. But in an operation, decisions are **endogenous**: they react to outcomes, and outcomes react to decisions. If you hardcode decisions, you cannot measure second-order effects, because the follow-up decisions never happen in the model. Gemba closes the loop.

**Sources:** [Optioryx: how to reslot without shutting down](https://www.optioryx.com/blog/how-to-reslot-a-warehouse) (2–6 weeks, staging 10–15% of active space, mis-pick risk, peak timing), [Fortna: sustainable slotting](https://www.fortna.com/?p=3617) (labor availability and disruption as the blockers), [Omni: prioritizing slotting moves](https://omnionlinestrategies.com/blog/how-to-prioritize-slotting-moves) (diminishing returns, rank moves by value), [Racklify: mis-picks](https://racklify.com/encyclopedia/what-is-a-mispick-causes-and-operational-impact/).

## 3. What a user can do with it (the use cases)

| Mode | Who | What they do | What they get |
|---|---|---|---|
| **Rehearse (backtest)** | Reslotting / industrial engineer before a reslot | Load 2 months of history plus the target layout. Run the held-out month with injected surprises. | A phase plan that already survived disruptions, the price of each trade-off, and a decision record. |
| **Fork (what-if a decision)** | Engineer, ops manager, plant director | Pick any day, force a different decision ("protect the Pick Module all week", "hire 6 temps", "pause the reslot"), run forward. | Two timelines side by side and the cascade trace showing why they diverged. |
| **Compare policies** | Leadership | Run the same month under customer-first vs efficiency-first company profiles. | Different plans, with what each priority costs in speed, labor and on-time. |
| **Copilot (future, shown via inbox)** | Engineer at the daily standup during a live reslot | Feed yesterday's actuals and today's known events. | Tonight's recommended move list with trade-offs already simulated. |
| **Learn (recursive)** | The platform itself | Run training scenarios, retro, update the playbook. | A better org on unseen scenarios, with proof. |

Reslotting is the first "pack". The platform pattern (floor twin + org twin + rituals + levers + consequence engine) generalizes to any operational change: peak-season labor planning, a WMS cutover, onboarding a big customer, closing a dock. We pitch that as future potential, not as something built today.

## 4. Hackathon fit

| Criterion / statement | How Gemba answers it |
|---|---|
| Theme "a team of agents you can walk away from" | A 30-day run executes unattended: daily standups, hiring specialists, re-planning on events, verifying every day. |
| **Version N+1** (primary track, see the decision gate in section 21) | Retrospective coach rewrites the org's playbook (prompt-level lessons per role), keeps a change only if it improves the score on **unseen** scenarios. Before/after chart is the headline. |
| **Done Means Done** (fallback track, also built in) | The org writes its own done-checks from the company profile; code verifies them every simulated day; drift detectors catch loops, churn, stalls and prediction error. |
| **Shared Brain** (supporting) | The board (constraints, agreements, evidence, decisions, playbook) is what every agent reads before speaking and writes after. |
| Technicality 40% | Deterministic hourly floor twin, typed decision levers with an authority matrix, consequence engine with causal links, forkable deterministic runs (LLM response cache), forecast-only what-ifs (no leakage), train/test scenario split. |
| Creativity 25% | Nobody simulates the decisions and politics around operations. "The Sims for warehouse decisions." |
| Live demo 20% | 2D animated floor, meeting room with characters, live fork, live inbox disruption, live role added from a config file. |
| Future potential 15% | Wargaming for operations: any site, any operational change, any org chart. Copilot at the daily standup. |
| Best use of Nemotron | Model tiering by role: Ultra for the chair, director and retro coach (reasoning on, 1M context reads the whole run), Super for stakeholders, Nano for analytics phrasing, event parsing and quiet-day fast path. Prompt layout designed for Crusoe's KV-cache. |
| Best use of NemoClaw / OpenShell (stretch) | A Scenario Designer (an OpenClaw agent sandboxed by NemoClaw) is a meta-harness over Gemba: it turns plain-language requests into scenario files, starts runs through the console API and summarizes results. The sandbox limits it to Crusoe and the Gemba API, and it can never touch the floor except through Gemba's typed levers. Production argument: agents that make operational decisions need a blast-radius boundary. |

Banned-list check: not a dashboard (the product is the agent org and its loop; the UI is a window onto it), not RAG (no vector store; playbook rules are selected by event tags), not Streamlit.

## 5. Scope for the day (must / should / stretch)

**Must (demo fails without it):**
1. Synthetic warehouse data in a local SQLite inputs database (`inputs.db`, WMS-like schema), shaped so the hero cascade is likely (trap 8 in section 7.2).
2. Floor twin: hourly simulation of picking, replenishment, night moves, consolidator, trucks, mis-picks, safety risk.
3. Consequence engine with at least 4 cascade rules and causal links.
4. Org twin: 9 roles from config files, standup protocol, typed levers, authority matrix, veto, lock, escalation, hiring on demand.
5. 30-day backtest run, unattended, with scripted events and inbox.
6. Done-checks and drift detection.
7. Fork from any day with a forced decision, and compare.
8. UI: 2D floor replay, meeting room, timeline, compare view.
9. Eval table: do-nothing, naive big bang, static rules, crew.

**Should (headline for Version N+1):**
10. Retrospective coach + playbook + train/test gate showing v0 vs v1 on unseen scenarios.
11. Two company profiles compared on the same month.

**Stretch (only if ahead at 15:00):**
12. Scenario Designer: an OpenClaw agent in a NemoClaw sandbox that writes `scenarios/*.yaml` from plain language and starts runs (section 16). Fallback: a "Describe a scenario" box in the console backed by Nano with the same scenario schema.
13. Add a role live from YAML during the demo (e.g., union rep who blocks overtime).
14. Copilot mode reading a "today" partition.

---

# PART B: ARCHITECTURE

## 6. System overview

```
                         +---------------------------+
   inputs.db (SQLite)    |  INPUTS                   |
   (WMS-like schema) --->|  warehouse data           |
   scenarios/*.yaml ---->|  scenario (events,profile)|
   org/*.yaml ---------->|  org spec (roles, powers) |
   playbook vN --------->|  playbook rules           |
                         +-------------+-------------+
                                       |
                                       v
 +-------------------+     +-----------------------+     +-------------------+
 |  FLOOR TWIN       |<--->|  RUN ENGINE (loop)    |<--->|  ORG TWIN         |
 |  sim (hourly)     |     |  day clock, events,   |     |  roles, standup,  |
 |  forecast/whatif  |     |  rituals, verifier,   |     |  levers, powers,  |
 |  consequence eng. |     |  drift, fork, cache   |     |  hiring, LLMs     |
 +-------------------+     +-----------+-----------+     +---------+---------+
                                       |                           |
                                       v                           v
                         +---------------------------+   Crusoe Managed Inference
                         |  RUN STORE (runs.db)      |   (Nemotron 3 Ultra/Super/Nano)
                         |  board, decisions, KPIs,  |
                         |  utterances, cascade graph|
                         |  llm_calls (cache, cost)  |
                         +-------------+-------------+
                                       |
                 +---------------------+---------------------+
                 v                     v                     v
          CONSOLE (FastAPI + SSE)   EVAL HARNESS        RETRO COACH
          floor, meeting, timeline  baselines, seeds    playbook vN -> vN+1
          fork, compare, inbox      train/test split    verified by forking
```

Harness view (use this framing in the pitch): the **org twin plus the run engine is the harness**. It builds each agent's context, decides who speaks, gives agents tools (what-ifs, queries), turns answers into typed levers, enforces authority and vetoes, and logs and caches every call. The **floor twin is the environment** the harness acts on: deterministic code with no LLM in it. The optional Scenario Designer (section 16) is a **meta-harness** one level up: it chooses what Gemba runs and reads the results, but never bypasses Gemba's levers.

Process model: one Python process per run (async inside). The console server reads the run store and streams. Many runs can execute in parallel (eval, retro forks).

Tech stack:
- Python 3.11, `pydantic` v2, `numpy`, `pandas`, `sqlalchemy` (SQLite), `pyyaml`, `fastapi`, `uvicorn`, `sse-starlette`, `openai` (OpenAI-compatible client pointed at Crusoe) or `langchain-crusoe` behind the same interface, `pytest`.
- Frontend: one static page, plain ES modules, Canvas 2D for the floor, SVG/DOM for the rest. No build step, no framework.
- Storage: local SQLite only, two files behind SQLAlchemy URL switches. `INPUTS_DB_URL` (default `inputs.db`) holds the WMS-like input tables written by the generator; `RUN_STORE_URL` (default `runs.db`) holds the run store. The "plug in your data" story is pointing `INPUTS_DB_URL` at your own export with the same schema. No Neon, no Parquet. Never depend on venue wifi for a run.

## 7. The warehouse (floor twin data model)

Calibrated to Sudhersan's real site, simplified. All numbers are defaults in `scenarios/*.yaml` and can be tuned.

### 7.1 Layout
- **28 forklift aisles** in a row, numbered 1–28. Each aisle: 30 bays × 4 levels × 2 sides. Levels 1–2 are **active** (pick faces), levels 3–4 are **reserve** (pallets).
- **Pick Module (PM)** between aisles 14 and 15: conveyor with shelving on both sides, **6 zones**, 120 active slots per zone (720 total). Pick-to-tote, the conveyor carries totes to the consolidator.
- **Consolidator** at the front: where an order's totes and forklift picks meet. Capacity 400 open orders; above that it jams.
- **Staging area** for reslot moves: 150 temporary slots.
- **Reslot size:** the target layout (built from days 1–60 only) changes about 1,100 SKU slots. A direct move into an empty slot is 1 leg; a swap is 3 legs through staging (A to staging, B to A's slot, A to B's slot). Expect about 2,400 legs in total, so the reslot takes about 12 nights at full speed and 15–20 nights with disruptions, inside the 30-day window. Default budget: 200 legs/night.
- **Shipping dock**: 6 doors. Carrier cutoffs at 13:00, 17:00 and 21:30.
- Distance: aisle 14 and 15 are nearest the consolidator (10 m), +4 m per aisle away. PM distance 0.

### 7.2 Products and demand
- 2,000 SKUs, 300 families of 3–8 co-ordered SKUs, weight, cube, units per pallet, hazmat flag, fragile flag.
- About 2,000 orders/day, about 12 lines/order, so about 24,000 lines/day. Release hours 05:00–19:00.
- 90 days: days 1–60 visible history, days 61–90 held out.
- Built-in traps (named generator functions, each unit-tested):
  1. Zipf velocity (top 20% of SKUs ≥ 75% of lines).
  2. Families co-ordered (p = 0.55 per other member).
  3. Month-3 shift: a seasonal group of 120 SKUs ×3, a fading group ×0.3.
  4. Stale layout: 40% of the top-200 SKUs sit in aisles 1–5 and 24–28; 25 fast SKUs have undersized active slots.
  5. Tight PM: greedy fill of top SKUs overloads PM zones by about 20%.
  6. 5% rush orders with a 2-hour due window.
  7. 3% full-pallet lines (reserve picks).
  8. Key-customer concentration: one key customer's lines come mostly from the fast and seasonal families that `phaser.rank_moves` moves first, so a learning dip after an aggressive night can push that customer's mis-picks over the C2 threshold. This makes the hero cascade likely without scripting it.

### 7.3 Workforce (per day; two pick shifts plus night)

| Role (people) | Shift | Default headcount | What they do in the twin |
|---|---|---|---|
| PM pickers | Day (06:00–14:00), Swing (14:00–22:00) | 2 per zone × 6 = 12 per shift | Pick-to-tote in their zone, 25 s/line (18 s pick + tote handling; section 9.2 is authoritative) |
| Forklift order pickers | Day, Swing | 14 per shift | Travel to aisle slots, case and each picks |
| Reach-truck drivers (replenishment) | Day, Swing | 4 per shift | Pallet down from reserve to active, 6 min/trip |
| Pallet pickers | Day, Swing | 3 per shift | Full-pallet lines from reserve, 180 s/line |
| Consolidators / packers | Day, Swing | 10 per shift | Close orders, 40 s/order |
| Loaders | Day, Swing | 6 per shift | Load trucks before cutoffs, 2 loaders per door |
| Night movers (reslot crew) | Night (22:00–06:00) | 4 | Execute reslot move legs, 6 min/leg, 7 productive hours (max about 280 legs/night) |
| Temp pool (on request) | Any | up to 8 | 70% productivity and ×2 mis-pick rate for their first 5 working days |

Total about 110 people on payroll, about 104 on a normal day. Each person is a row in `labor_roster` with a name, so the UI can show individuals, but the twin computes at the pool level.

### 7.4 Four types of picking
1. **PM zone pick** (conveyor, fastest).
2. **Forklift aisle pick** (each or case pick from active levels; slower with distance and height).
3. **Full-pallet pick** from reserve.
4. **Rush pick** (any of the above, released with priority, due within 2 hours).

## 8. Data layer (SQLite)

### 8.1 Input schema (looks like a WMS export)

```
site(site_id, name, opening_hour, closing_hour, cutoffs jsonb)
aisles(aisle_id, x, distance_m, bays, levels)
pm_zones(zone_id, slots, pickers_default, seconds_per_line)
locations(loc_id, kind pm|aisle|staging, aisle_id, zone_id, bay, level, slot_type active|reserve,
          capacity_units, max_weight_kg, x, y)
skus(sku_id, family_id, weight_kg, cube_l, units_per_pallet, hazmat, fragile, customer_tier_hint)
customers(customer_id, name, tier key|standard)
slotting_current(sku_id, active_loc, reserve_loc, active_capacity_units)
slotting_target(sku_id, active_loc, reserve_loc, active_capacity_units)   -- "from slotting software" (our slotter)
orders(order_id, day, release_hour, customer_id, priority normal|rush, cutoff_hour)
order_lines(order_id, sku_id, qty)
labor_roster(person_id, name, role, shift, productivity, is_temp)
labor_calendar(day, shift, role, scheduled)          -- before events
```

`order_lines` is the only big table (90 × 24k ≈ 2.2M rows). Load days into memory per run; never query per simulated hour.

### 8.2 Run store schema

```
runs(run_id, parent_run_id, fork_day, fork_decision jsonb, scenario_id, profile_id,
     playbook_version, org_spec_hash, seed, mode backtest|fork|eval|retro, status, started_at, finished_at, llm_usd)
day_results(run_id, day, kpis jsonb, hourly jsonb, predicted jsonb)
events_log(run_id, event_id, day, hour, kind, args jsonb, source scripted|inbox|consequence, caused_by)
constraints(run_id, constraint_id, kind, args jsonb, owner_role, day_start, day_end, active, caused_by)
agreements(run_id, agreement_id, text, parties, day, active, retired_reason)
evidence(run_id, evidence_id, spec_hash, spec jsonb, horizon_days, kpis jsonb, scorecard jsonb, created_day)
meetings(run_id, meeting_id, day, kind standup|escalation|quiet, attendees, agenda jsonb, outcome)
utterances(run_id, meeting_id, seq, role, kind proposal|position|veto|lock|question|decision, text, evidence_ids, option_id)
options(run_id, meeting_id, option_id, levers jsonb, proposed_by, evidence_id, score, feasible, vetoed_by)
decisions(run_id, decision_id, day, option_id, levers jsonb, decided_by, rationale, deviates_from_top_score bool, caused_by)
hires(run_id, day, role, trigger, released_day)
checks(run_id, day, check_name, scope, passed, value, threshold)
drift_flags(run_id, day, kind, detail, response)
cascade_edges(run_id, from_id, to_id, kind)          -- decision->consequence->event->meeting->decision
llm_calls(call_id, run_id, role, model, prompt_hash, request jsonb, response jsonb, tokens_in, tokens_out,
          cached_tokens, latency_ms, usd, from_cache bool)
playbook_rules(version, rule_id, role, tags, trigger, advice, evidence jsonb, score_delta, status)
eval_results(eval_id, scenario_id, profile_id, contender, playbook_version, seed, metrics jsonb)
```

Every record that results from something else carries `caused_by`. That single field is what makes the cascade trace possible.

## 9. Floor twin (`floor/`)

### 9.1 Interface (the platform contract; any future twin implements this)

```python
class FloorTwin(Protocol):
    def initial_state(self, scenario) -> FloorState
    def apply_levers(self, state, levers: list[Lever], day) -> FloorState      # validated, deterministic
    def step_day(self, state, day, demand: DayDemand, events: list[Event]) -> tuple[FloorState, DayResult]
    def forecast_demand(self, state, day, horizon, known_events) -> list[DayDemand]   # from visible history only
    def whatif(self, state, option_levers, days, known_events) -> Evidence            # uses forecast_demand, never actuals
```

`FloorState` holds: current slotting, slot capacities, move queue and staging occupancy, labor availability by pool, frozen aisles, locked SKUs, learning-dip timers, temp ramp timers, fatigue counters, consequence counters. It is immutable per day (copy on write) so any day can be snapshotted and forked.

### 9.2 Hourly simulation (per day, hours 06–22, then night 22–06)

1. **Route lines** to a pool: PM zone z, forklift (aisle a, level l), pallet, or blocked (frozen aisle → pallet pool at 1.5× time, logs violation if a decision caused it).
2. **Service time per line (seconds):**
   - PM: 25 (pick plus tote handling)
   - Forklift: 25 + 0.8 × distance_m + 4 × (level − 1)  (about 35–75 s)
   - Pallet: 180
   - Multipliers: learning dip ×1.15 (SKU moved within 2 days), temp ×1.43, staging congestion ×1.10 (aisles adjacent to staging when staging > 90%), wave-priority policy reorders but does not change time.
3. **Capacity per hour per pool** = available people × 3600 s (minus breaks: 0.9 factor).
4. **Queueing:** work released in hour h joins the pool queue; served FIFO within priority (policy lever). Unserved work carries over. Backlog at shift end goes to overtime if approved, else rolls to the next shift.
5. **Replenishment:** each active SKU depletes by picked units; when it reaches 0 before the next replen, its lines wait (stockout) until a replen trip is served (reach-truck pool, 6 min/trip). Undersized slots create many trips.
6. **Consolidator:** an order is open from its first finished line until its last. Open orders per hour = queue. Jam if > 400: closing time +50% for that hour.
7. **Trucks / on-time:** an order is on time if consolidated + 40 min pack/load ≤ its cutoff. Loaders cap orders loaded per hour per door.
8. **Mis-picks:** expected rate per line, base PM 0.3%, forklift 0.5%; ×3 learning dip, ×2 temp, ×4 during a WMS-sync-delay event. Use seeded RNG keyed by (seed, day, sku) so results are deterministic.
9. **Night moves:** approved moves execute 22:00–06:00 at 6 min each with available movers; swaps that need a temp slot occupy staging; unfinished moves stay queued.
10. **Safety risk:** per aisle-hour, people-and-trucks present / safe limit (3). Above 1.0 the risk index rises and a seeded near-miss draw happens (p = 0.03 × excess). In what-ifs, use the expected number of near-misses instead of a draw.

Details the coding agent must not guess:
- Orders released before 06:00 queue until the day shift starts.
- `cutoff_hour` = first carrier cutoff ≥ release_hour + 3 h (rush: + 2 h); orders after the last cutoff ship next day and count late only if they miss tomorrow's first cutoff.
- Loaders: each door loads 120 orders/hour with 2 loaders; orders not loaded by a cutoff wait for the next one and count late.
- Mis-pick RNG is keyed by (seed, day, order_id, line_no), so forks don't change unrelated draws.
- Staging: a swap's first leg occupies a staging slot; it is freed when the last leg completes; unfinished legs carry to the next night.
- `returns_spike(pct)`: adds putaway work to the forklift pool equal to pct × lines × 60 s.
- `new_sku_launch(n)`: n new SKUs arrive unslotted; until slotted they pick from aisle 28 at far-aisle time.
- `wms_sync_delay`: mis-pick ×4 on SKUs moved in the last 2 nights.
- Implement only the event kinds used by the scenario files; stub the rest.

### 9.3 Day KPIs (`DayResult.kpis`)
`lines, lph` (lines per productive labor hour: lines ÷ busy hours of the PM, forklift, pallet and replenishment pools; night move hours are excluded so reslotting isn't penalized twice), `throughput_lph_site` (lines per clock hour), `on_time_pct, mispick_rate, pm_peak_zone_load, consolidator_queue_max, jam_hours, replen_trips, stockout_lines, overtime_hours, labor_cost_usd, moves_done, moves_queued, reslot_progress_pct, staging_occupancy_max, safety_risk_max, near_misses, violations[]`.

Note: the motion-graphic primer used an illustrative "1,180 LPH". The twin uses lines per productive labor hour per person. Don't mix them in the pitch.

### 9.3a Calibration targets (tune until these hold on the default scenario, days 61–90, do-nothing)
In priority order. Tunable constants: service times, headcounts per pool, lines per order, PM share of the stale layout, consolidator cap. Never tune by editing the traps.
1. Forklift pool utilization 80–95% on normal days on the stale layout (so disruptions bite).
2. PM peak zone load 85–100% on normal days.
3. On-time 94–97% on normal days; below 90% on the labor-shortage day without intervention.
4. Oracle (target layout applied instantly at no cost) gives LPH +15–30% vs do-nothing. Real reslots report 8–15% throughput gain; the oracle is an upper bound, and the crew should land between static rules and the oracle.
5. Naive big bang (all legs on nights 61–62 with 3× movers as temps, ignoring constraints): at least one jam hour, mis-picks above 1% for 2 days, and staging overflow.
Write the measured values into `DECISIONS.md` once they hold.

### 9.4 Consequence engine (`floor/consequences.py`)
Runs at the end of each day. Each rule reads state and KPIs and may emit an event for a future day, with `caused_by` pointing at the decision or KPI that triggered it. These are what make decisions cascade.

| ID | Rule | Emits |
|---|---|---|
| C1 | SKUs moved last night get a 2-day learning dip (time ×1.15, mis-pick ×3) | state change |
| C2 | A key customer's mis-picks > 0.8% for 2 consecutive days | `customer_escalation(customer)` next day |
| C3 | A pool worked overtime on 3 of the last 4 days | `absenteeism(+15%, 3 days)` |
| C4 | Staging occupancy > 90% at 06:00 | `staging_congestion` (aisles near staging slow down, safety risk up) |
| C5 | Near-miss occurred in an aisle | `safety_review(aisle)`; a second near-miss within 5 days forces `aisle_closure(aisle, 3 days)` |
| C6 | On-time < profile target for 2 consecutive days | `director_escalation(on_time)` |
| C7 | Stockout lines > 1% of lines | `replen_alert` (Replenishment Lead hired) |
| C8 | Temps active | ramp state (productivity, mis-picks) for 5 days |

**Cascade links (who writes `cascade_edges`).** Node ids are typed: `dec:<id>`, `evt:<id>`, `kpi:<run>:<day>:<name>`, `mtg:<id>`, `con:<id>` (constraint). The store writes an edge whenever a record with `caused_by` is inserted. Attribution rules: a mis-pick on a SKU in a learning dip is attributed to the decision that moved it; overtime fatigue to the decisions that approved the overtime; staging congestion to the reslot decisions that filled staging; a meeting to the events on its agenda; a decision to its meeting. KPI nodes link to the consequence rule that fired. The cascade shown in the demo (section 22) is emergent: trap 8 makes it likely, a test checks that the chain fires on at least one seed by 13:50, and if needed you tune the hero scenario's calendar and pick the hero run from several seeds. Never script the chain.

### 9.4a Forecast demand (used by every what-if; never sees future actuals)
- For day d, build demand by resampling orders from the same weekday in the 14 days before d (seeded RNG keyed by (seed, d)). Days already executed in the held-out month count as history, so the forecast adapts to the month-3 shift with a lag, as a real planner would.
- Scale by events that have been announced by day d (see lead times below).
- Recalibration: when the prediction-error drift flag fires, multiply each family's forecast by actual ÷ forecast over the last 3 days.
- The target layout (`slotting_target`) is computed from days 1–60 only.

Announcement lead times (an event is visible to agents this many days before it starts): demand_surge 2, key_customer_week 3, leadership_visit 5, returns_spike 1, new_sku_launch 3; labor_shortage, rack_damage, pm_zone_down, wms_sync_delay, forklift_breakdown 0 (morning of).

### 9.5 Exogenous events (scripted calendar, plus inbox)
`labor_shortage(pool, pct, days)`, `demand_surge(skus|family|customer, factor, days)`, `rack_damage(aisle, days)`, `pm_zone_down(zone, hours)`, `leadership_visit(day, no_moves_in_aisles)`, `key_customer_week(customer, days)`, `new_sku_launch(n, day)`, `returns_spike(pct, days)`, `wms_sync_delay(days)`, `forklift_breakdown(count, days)`.
Default scenario calendar (held-out month): day 63 labor shortage, day 66 rack damage aisle 7, day 69 demand surge on the seasonal family, day 72 key customer week, day 75 PM zone 2 down for 4 hours, day 78 leadership visit, day 82 WMS sync delay, day 86 returns spike.

### 9.6 Deterministic tools used by agents (pure functions)
- `slotter.propose_target(state, params)` → target slotting (the "slotting software"). Params: PM share target, family weight, zone cap %, days-of-cover sizing, recency half-life, excluded aisles, protected zones.
- `phaser.rank_moves(current, target, state)` → moves ranked by value per move (seconds saved per day ÷ move cost), with payback days.
- `phaser.select_tonight(ranked, budget, include_zones, exclude_zones, protect_skus, frozen)` → tonight's move list.
- `economics.price_option(option, evidence)` → labor cost, overtime cost, expected payback.

Performance target: `step_day` < 300 ms (vectorized per day, small hourly loop), so a 3-day what-if takes under 1 s.

## 10. Org twin (`org/`)

### 10.1 Hierarchy

```
                    Dana, Plant Director  [Ultra]
                    owns company profile, escalations, overrides
                               |
                    Megan, Reslotting Program Manager (chair)  [Ultra]
                    runs standups, calls specialists, decides
         ______________________|_____________________________
        |                      |                             |
  Ryan, Ops Manager [Super]   Luke, Reslotting Engineer [Super]   Paige, Analytics [Nano + code]
  LPH, PM, consolidator      progress to target                 simulator, evidence, forecasts

  On call (hired by Megan when a trigger fires; released when it clears):
  Sam, Safety [Super]   Ellen, Quality [Super]   Marcus, Shift Lead [Nano]   Tom, Replenishment [Nano]

  Meta agents (not in the org chart):
  Event Interpreter [Nano]: turns inbox text into typed events/constraints
  Retro Coach [Ultra]: runs after a run, writes playbook lessons
  Verifier (code, not an LLM): done-checks, drift, authority validation
```

### 10.2 Role spec (one YAML per role in `org/roles/`, plus a prompt in `org/prompts/`)

```yaml
id: ops_manager
name: Ryan
title: Ops Manager
model_tier: super          # ultra | super | nano
character: >
  Ran the floor for 9 years. Protective of the Pick Module and the swing shift.
  Blunt, numbers first, hates surprises on peak days.
owns_kpis: [lph, pm_peak_zone_load, consolidator_queue_max]
power: vote                 # vote | veto | lock | chair | director | capacity_truth | none
levers_can_propose: [reslot_tonight, labor_allocate, wave_priority, pause_reslot]
levers_can_issue: [labor_allocate, wave_priority]
attends: always             # always | on_trigger
triggers: []
red_lines: ["PM peak zone load > 100% on any day"]
```

Roster (defaults):

| Role | Model | Owns | Power | Attends | Hire triggers |
|---|---|---|---|---|---|
| Dana, Plant Director | Ultra | Company profile, revenue, customer promises | director: change profile, override locks, approve temps and overtime over budget, break ties | Escalations only | `director_escalation`, chair escalates, veto deadlock |
| Megan, Program Manager | Ultra | Schedule, done-checks, meeting | chair: sets agenda, calls specialists, decides among feasible options | Always | — |
| Ryan, Ops Manager | Super | LPH, PM load, consolidator | vote | Always | — |
| Luke, Reslotting Engineer | Super | Reslot progress, value per move | vote | Always | — |
| Paige, Analytics | Nano + code | Evidence, forecasts | none (only role that runs the simulator) | Always | — |
| Sam, Safety | Super | Safety risk, near-misses, closures | **veto**; can issue `close_aisle` unilaterally | On trigger | `rack_damage`, `safety_review`, risk index > 1.0, `forklift_breakdown` |
| Ellen, Quality | Super | On-time, mis-pick rate, key customers | **lock** (protect SKUs/customer up to 3 days alone, longer with chair) | On trigger | `customer_escalation`, `key_customer_week`, mis-pick > 0.6%, `wms_sync_delay` |
| Marcus, Shift Lead | Nano | Headcount, overtime, temps | **capacity truth** (sets available labor; can refuse overtime beyond policy) | On trigger | `labor_shortage`, `absenteeism`, overtime requested |
| Tom, Replenishment | Nano | Replen trips, stockouts | vote | On trigger | `replen_alert`, `demand_surge` |

### 10.3 Decision levers (the only way agents change the world)

Typed, validated by code, mapped to `FloorTwin.apply_levers`.

| Lever | Args | Who may issue (after decision) | Effect |
|---|---|---|---|
| `reslot_tonight` | n_moves, order (value/zone/family), include_zones, exclude_zones | chair | queues tonight's moves |
| `pause_reslot` | days | chair | no moves |
| `labor_allocate` | shift, pool → headcount | ops_manager within Shift Lead's caps | moves people between pools |
| `overtime` | shift, pool, hours | shift_lead (within policy), director (over budget) | extends capacity, cost ×1.5, fatigue counter |
| `hire_temps` | count, start_day, days | director | temp pool with ramp |
| `protect` | zone or skus or customer, days | quality (≤3 days), chair (longer) | no moves touching them |
| `resize_slots` | skus, days_cover | chair (proposed by replenishment or engineer) | moves needed, fewer replen trips |
| `wave_priority` | rush_first, by_cutoff, by_zone | ops_manager | changes queue order |
| `close_aisle` / `reopen_aisle` | aisle, days | safety | routes picks elsewhere |
| `staging_clearance` | n_movers | chair | tonight's movers clear staging instead of new moves |
| `set_profile` | profile weights | director | changes the scorecard |
| `escalate` | question | chair | opens an escalation meeting |

**How authority works at commit time (`org/authority.py`).** The chair approves an *option*; each lever in it is then *issued* by its owner. For each lever: if the owner is in the room, the owner issues it. If the owner is an on-call role who is absent (Shift Lead for overtime, Quality for protect, Safety for close_aisle), the chair hires that role into the meeting first and the role may accept or refuse with a reason. If the lever needs the Director (`hire_temps`, overtime over budget, `set_profile`, overriding a lock), the meeting escalates and the Director rules the same morning. Proposers may only put levers in an option that their role lists under `levers_can_propose`; other levers are rejected before scoring and the rejection is logged.

### 10.4 Company profile (scorecard)

```yaml
id: customer_first
weights: {on_time_pct: 0.35, mispick_rate: 0.25, lph: 0.20, reslot_progress_pct: 0.10, labor_cost_usd: 0.10}
targets: {on_time_pct: 97.0, mispick_rate: 0.5, lph_lift_pct: 8}
hard_limits: {safety_risk_max: 1.0, expected_near_misses: 0.5, pm_peak_zone_load: 1.10}
budgets: {overtime_hours_per_week: 120, temps_max: 8, llm_usd_per_run: 1.50}
```
`efficiency_first` flips weights toward LPH 0.35, cost 0.25, progress 0.20, on-time 0.15, mis-picks 0.05.

**Scoring (one formula for options, days and whole runs):**

score = Σ_k w_k × clip( sign_k × (KPI_k − REF_k) / SCALE_k, −1, 1 )

| KPI k | sign | SCALE | Aggregation for a run |
|---|---|---|---|
| on_time_pct | + | 5 points | mean of days |
| mispick_rate | − | 0.5 points | mean of days |
| lph | + | 10% of REF (relative) | mean of days |
| reslot_progress_pct | + | 100 points (REF = 0) | value at the end of the horizon |
| labor_cost_usd | − | 10% of REF (relative) | sum |

- REF for an option = the do-nothing what-if over the same horizon on the same forecast. REF for a day or a run = the do-nothing baseline run on the same scenario.
- Weight keys are exactly the KPI names above; targets like `lph_lift_pct` are only used by done-checks.
- **Feasibility:** an option is infeasible if its what-if breaches a hard limit (expected near-misses > 0.5, safety_risk_max > 1.0, PM peak zone load > 1.10). If every option is infeasible, the chair takes the one with the smallest total breach and the meeting escalates to the Director.
- The chair sees the scores; code computes them.

### 10.5 The standup ritual (one per simulated morning)

```
06:00 BRIEF (code)       yesterday actual vs predicted, today's events and constraints,
                         drift flags, open locks, playbook rules matching today's tags
      TRIAGE (code+chair) quiet day (code) -> standing plan continues, Nano writes one line
                         else: code hires roles whose triggers fired; chair (Ultra) sets the agenda
      PROPOSE            Engineer proposes 1 option (levers); any attendee may add 1 alternative;
                         max 3 options
      EVIDENCE (Paige)   whatif() for each option over 3 days on FORECAST demand -> evidence ids
      POSITIONS          each attendee: support | oppose | amend, must cite an evidence id;
                         Safety veto check; Quality lock check; Shift Lead capacity check
      AMEND (max 1 round) amended options get new evidence
      SCORE (code)       scorecard per feasible option under the company profile
      DECIDE (chair)     pick an option; picking a non-top option needs a rationale (logged as deviation)
                         all vetoed or tie within 2% or over budget -> ESCALATE to Director
      COMMIT (code)      authority check, apply levers, record decision with caused_by links
06:00-06:00 next day     floor twin runs the day and the night; consequence engine runs;
                         verifier evaluates done-checks; drift detectors run
```

Message order in every LLM call (keeps Crusoe's KV cache warm): role prompt (static) → playbook rules for this role (changes per version) → board snapshot (changes per day) → task. Never reorder.

**Quiet days and hiring are decided by code; the LLM only narrates.**
- A day is **quiet** if there are no new or newly announced events, no pending consequences, no drift flags, no open inbox messages, and every yesterday KPI is within its target band. On a quiet day the standing plan continues (same reslot budget and settings) and one Nano call writes Megan's one-line standup note.
- **Hire** a role when its trigger appears in today's events, newly announced events, or yesterday's KPIs (table in 10.2). **Release** it after 2 consecutive days with no trigger. Every hire and release is logged with its trigger.
- Expect about 10–14 meeting days in a 30-day run.

### 10.6 Agent output schemas (`org/schemas.py`, pydantic, JSON mode)

```python
class Proposal(BaseModel):   option_label: str; levers: list[Lever]; rationale: str  # <= 60 words
class Position(BaseModel):   option_id: str; stance: Literal['support','oppose','amend']
                             amendment: list[Lever] | None; rationale: str; evidence_ids: list[str]
class Veto(BaseModel):       option_id: str; rule: str; evidence_ids: list[str]
class Lock(BaseModel):       target: Literal['zone','skus','customer']; ids: list[str]; days: int; reason: str
class ChairTriage(BaseModel): meeting: bool; agenda: list[str]; hire: list[Hire]; release: list[str]
class ChairDecision(BaseModel): option_id: str | None; escalate: bool; rationale: str; evidence_ids: list[str]
class DirectorRuling(BaseModel): option_id: str | None; set_profile: str | None; override_lock: str | None; rationale: str
class EventParse(BaseModel): kind: str; args: dict; confidence: float   # inbox text -> typed event
class Lesson(BaseModel):     role: str; tags: list[str]; trigger: str; advice: str  # <= 40 words
```
Validation rules enforced in code: positions without valid evidence ids are rejected and re-asked once ("cite evidence or support"); levers outside authority are rejected; schema failure retries once with the error, then falls back to a safe default (`support` the engineer's option, or `pause_reslot` for the chair) and logs `llm_fallback`.

### 10.7 Memory (the Shared Brain)
- **Board snapshot** (≤ 1,500 tokens): active constraints, locks, agreements, last 5 evidence rows, yesterday's KPIs vs targets, open inbox, who is in the room.
- **Stance memory per role**: that role's last 3 positions (keeps characters consistent).
- **Playbook rules** for that role whose tags match today's events (max 5).
- Agents hold no hidden state between calls. Everything they "know" is in the store, which is what makes runs replayable and forkable.

## 11. Run engine (`engine/`)

### 11.1 Day loop

```
run(scenario, profile, policy, playbook_version, mode, fork=None):   # policy: DecisionPolicy
  load inputs; state = twin.initial_state(); write runs row
  if fork: copy parent's store rows up to fork_day; restore state snapshot; force fork_decision on fork_day
  done_checks = verifier.template(profile)          # code builds the checks from targets + hard limits
  done_checks = policy.pick_thresholds(done_checks) # org: chair picks within code-set bounds; baselines: defaults
  for day in heldout_days:
      events = calendar[day] + inbox.pop() (parsed by Event Interpreter) + consequences.pending(day)
      state = apply_event_constraints(state, events)
      meeting = policy.decide(day, state, events)       # org: standup (10.5); baselines: fixed rules
      state = twin.apply_levers(state, meeting.decision.levers, day)
      state, result = twin.step_day(state, day, actual_demand[day], events)
      store day_results (with predicted kpis from the chosen option's evidence)
      consequences.evaluate(state, result) -> pending events (caused_by)
      verifier.check_day(result, done_checks); drift.check(day)
      if drift flag: chair.respond(flag)  (continue | replan | shrink | stop)
      snapshot(state, day)
  final = verifier.check_final(); write results; status = done
```

**DecisionPolicy.** The org twin and every eval baseline (section 15) implement one interface: `decide(day, state, events) -> decision with levers` and `pick_thresholds(checks)`. The engine never knows which one it is driving. Baselines need no LLM, so engine, floor, consequence, fork and determinism tests run on them in seconds at no cost. There is no mock LLM: org behavior is tested live on Crusoe, made free and repeatable by the response cache.

### 11.2 Determinism, cache and forks
- Floor twin is pure given (inputs, seed, levers, events).
- LLM calls use temperature 0 (or the lowest stable value) and a response cache keyed by `sha256(model, messages, schema)`. A re-run replays exactly; a fork reuses cached calls up to the fork day, then diverges naturally. The cache is exported to `recordings/llm_cache.jsonl` and the hero run to `recordings/hero.db`; both are tracked in git (an exception to the `*.db` ignore rule), so org tests and `make demo` replay offline at $0.
- **Snapshots:** after each day the engine writes `snapshots/<run_id>/<day>.pkl` containing FloorState, active constraints, locks, hires, pending consequence events, done-checks, drift counters and the RNG state. Add `snapshot_path` to `day_results`.
- **Fork** = new `runs` row with `parent_run_id`, `fork_day`, `fork_decision`. The engine loads the parent's snapshot from the end of `fork_day − 1`, copies the parent's store rows before `fork_day` (with the new run_id), and runs on. The forced decision **replaces that day's standup only**; a multi-day intent ("protect the PM all week") is entered as a constraint with an end day. From `fork_day + 1` the org decides normally.

### 11.3 Verifier (done-checks)
Allowed check metrics are exactly the KPI names in 9.3. Code builds every check from the profile's targets and hard limits (`verifier.template`); the chair only picks each threshold within bounds the code sets, and that call is cached like any other. This keeps checks comparable across runs while the org still "writes its own done-checks". Daily scope: no hard-limit breach, on-time ≥ target − 2 pts, no jam hours, moves ≤ budget. Final scope: profile targets, reslot progress ≥ 90% of target moves (or a logged chair decision to stop early with reason), scorecard ≥ static-rules baseline.

### 11.4 Drift detectors
- Same what-if spec requested 3+ times (evidence `spec_hash` repeats).
- Plan churn: the approved reslot plan changed 3 days in a row.
- Stall: no reslot progress for 3 days without an active constraint explaining it.
- Prediction error: actual vs predicted KPI gap > 15% for 2 days → Analytics recalibrates the forecast (logged).
- Meeting overrun: amendment round exceeded, or LLM spend above budget.
A flag that recurs twice forces the chair to choose `shrink` or `stop`.

## 12. LLM layer (`llm/`) and model plan

### 12.1 Crusoe Managed Inference
- OpenAI-compatible client with `CRUSOE_API_KEY`; base URL from the Crusoe console (or `langchain-crusoe`'s `ChatCrusoe`, which defaults to the Crusoe endpoint).
- Model ids (confirm in the Intelligence Foundry catalog at 09:15; the blog lists these):
  - Ultra: `nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B` (1M context, $1.00 in / $3.20 out per 1M, cached input $0.25)
  - Nano Omni: `nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B` ($0.30 / $1.83)
  - Super 120B: copy the exact id from the console (pattern seen elsewhere: `nvidia/nemotron-3-super-120b-a12b`)
- Reasoning toggle: on for Director, chair decisions and Retro Coach; off everywhere else (latency).
- Structured output: try `response_format` JSON schema; if unsupported, instruct + parse + validate + one retry.

### 12.2 Which model does what

| Step | Model | Why |
|---|---|---|
| Quiet-day standup line (narration only), Event Interpreter, Analytics summaries, Shift Lead, Replenishment | Nano | fast, cheap, narrow tasks |
| Stakeholder positions (Ops, Engineer, Safety, Quality), proposals | Super | needs judgment, many calls |
| Chair decisions on meeting days, Director rulings, done-check writing | Ultra (reasoning on) | few, high-stakes calls |
| Retro Coach (reads the whole run log) | Ultra (1M context) | long-context analysis |

Budget per 30-day run: estimate about $0.40–0.80 (~12 meeting days × ~10 calls + ~18 quiet-day calls, ~3k tokens in and ~300 out each); hard cap $1.50 per run (profile budget, enforced by the drift detector). 10-day retro runs cost about a third. $50 covers the day with margin. Log every call's cost in `llm_calls`.

### 12.3 Concurrency
Positions within a meeting run in parallel (asyncio, max 4 in flight). Eval and retro forks run as parallel processes (max 4) to respect rate limits.

## 13. Recursive improvement: Retro Coach and playbook (`learn/`)

### 13.1 Scenario split
A **scenario** = the shared generated dataset + an event calendar + a demand perturbation (seeded: ±10% scale per family, a different seasonal group, shifted surge timing), applied at load time. Never regenerate the 2.2M rows per scenario. Retro scenarios are **10-day windows** (days 61–70 style) to keep runs short; the hero demo run uses the full 30 days.

- **Train set:** 3 scenarios (different seeds and event calendars).
- **Test set:** 3 unseen scenarios (different seeds, different event types and timing, one with an event kind never seen in training, e.g. `forklift_breakdown`).
- Same company profile for the gate. Profiles are compared separately.

### 13.2 Loop

```
for version in v0 -> v1 (-> v2 if time):
  run org(playbook=v) on train scenarios (parallel)
  for each train run:
     regret_days = days with the largest scorecard drop or a failed check
     trace cascade_edges back to the originating decision
     coach (Ultra, full log) proposes: alternative decision for that day + general Lesson (role, tags, advice)
     VERIFY: fork the run at that day with the alternative forced; accept lesson if
             delta scorecard over the remaining days > 0 and no new hard-limit breach
  candidate = v + accepted lessons (cap 12 rules; merge duplicates; drop rules that never fired)
  GATE: run test scenarios with v and candidate
        promote candidate to v+1 only if mean test score improves and violations do not increase
  record playbook_rules + eval_results
```

Lessons are injected into the matching role's prompt (section 10.7), selected by tags, not embeddings. That keeps it inside the rules (no RAG) and makes each lesson auditable.

Example lessons we expect the coach to find (because the traps produce them):
- Engineer: "Within 2 days after a demand surge is announced, don't move SKUs in the surging family."
- Chair: "When Shift Lead reports > 10% absence, switch to staging_clearance instead of new moves."
- Quality: "Lock key-customer SKUs the day before their week starts, not on day 1."

### 13.3 What we show
A small chart: test-set scorecard for v0 vs v1 (and v2), plus violations and on-time, with the static-rules baseline as a reference line. One click opens a lesson and the fork that proved it.

## 14. Console (`console/`)

FastAPI serves one page and streams with SSE. Endpoints: `GET /runs`, `GET /runs/{id}/stream?speed=`, `GET /runs/{id}/day/{d}`, `POST /runs/{id}/inbox`, `POST /runs/{id}/fork {day, levers}`, `GET /compare?a=&b=`, `GET /cascade/{run_id}/{node_id}`, `POST /runs {scenario, profile, playbook}`, `POST /scenarios` (validated scenario YAML, used by the Scenario Designer and the console box).

Layout (one screen, light and dark):

```
+----------------------------------------------------------------------------------------------+
| Gemba  scenario: Reslot Oct  profile: customer-first  playbook: v1   Day 69 09:00  [>] 4x [Fork]|
+-------------------------------------------------------+--------------------------------------+
|  FLOOR (canvas, hourly replay)                         |  MEETING ROOM                        |
|  28 aisles, PM conveyor with 6 zones, consolidator,    |  avatars around a table; who is in   |
|  dock with trucks and cutoff clocks.                   |  the room lights up (hired = badge)  |
|  Dots = pickers per pool; forklifts move; totes ride   |  speech bubbles stream; veto = red   |
|  the conveyor; queues glow; frozen aisles hatched;     |  stamp; lock = padlock; options with |
|  night moves animate SKUs to new slots.                |  scores; decision banner             |
+-------------------------------------------------------+--------------------------------------+
| TIMELINE  day 61 ------------------- day 90   event markers, decision markers, KPI sparklines  |
|           with done-check threshold lines; click a marker -> CASCADE TRACE drawer           |
+----------------------------------------------------------------------------------------------+
| BOARD drawer: constraints, locks, agreements, evidence | INBOX: type a disruption              |
+----------------------------------------------------------------------------------------------+
```

Compare view: two runs (fork A vs B or profile A vs B) with synced timelines, KPI deltas, and the first point where their decisions diverged. Cascade trace: a small left-to-right graph from `cascade_edges` (decision → consequence → event → meeting → decision), each node clickable.

Animation note: the twin is hourly and pool-level. The canvas samples a representative number of dots per pool and interpolates within the hour. It is a faithful visualization of pool loads and queues, not a per-person simulation. Say so if asked.

## 15. Eval harness (`eval/`)

Contenders on every scenario (3 seeds each where time allows):
1. **Do-nothing:** current layout, no moves, default labor.
2. **Naive big bang:** move everything to target on day 61, ignore constraints.
3. **Static rules:** fixed 200 moves/night by value (the same budget as the crew), pause on any event day, no other levers. This is the "hardcoded decisions" baseline.
4. **Crew v0:** org, empty playbook.
5. **Crew vN:** org with the promoted playbook.

Report: profile scorecard, LPH lift, on-time, mis-pick rate, violations, near-misses, overtime, reslot progress, LLM cost, meetings held, hires. Report every metric, including ones where a baseline wins.

## 16. Scenario Designer on NemoClaw / OpenShell (stretch)

What it is: a meta-harness over Gemba. An OpenClaw agent runs inside a NemoClaw (OpenShell) sandbox. The user tells it in plain language what to rehearse ("test a peak week with 20% absences on nights"); it writes a scenario file, starts a run, waits, and explains what happened by reading the run store and cascade graph. Gemba itself stays a controlled harness: the designer can only change inputs (`scenarios/*.yaml`, profile, playbook choice), never decisions inside a run.

Interface (all through the console API in section 14, no direct DB writes):
- `POST /scenarios` with a scenario YAML, validated against the scenario schema (events must be known kinds, days inside 61–90, pools and aisles must exist). Invalid files are rejected with the validation error so the agent can fix them.
- `POST /runs {scenario, profile, playbook}`, then `GET /runs/{id}/day/{d}` and `GET /cascade/{run_id}/{node_id}` to read results.
- Give OpenClaw these as a small tool/skill file with the endpoint URL and the scenario schema.

Sandbox policy:
- Egress allowlist: Crusoe inference host, the Gemba console host. Nothing else.
- Filesystem: writable only its own workspace; Gemba inputs read-only to it.
- Credentials stay outside the sandbox (OpenShell injects at the boundary).
- In copilot mode, write-back to a real WMS would go only to a `proposed_move_tasks` table that a human approves.

Fallback (build this first, it's 20 minutes): a "Describe a scenario" box in the console. Nano turns the text into a scenario YAML using the same schema and endpoint. The demo works the same; only the hosting story changes.

Schedule: setup probe and wiring at 14:50–15:30, time-boxed to 40 minutes (the pre-10:45 probe slot has passed). If it doesn't run by then, demo the fallback and describe the design.

Docs: [NemoClaw architecture](https://docs.nvidia.com/nemoclaw/latest/about/how-it-works.html). NemoClaw hosts agent runtimes (OpenClaw, Hermes, Deep Agents `dcode`); we use OpenClaw as the designer and keep the Gemba engine as an ordinary Python process outside the sandbox.

## 17. Copilot mode (future; demo via inbox)
Same loop, different inputs: a nightly job loads yesterday's actuals into the input tables (`orders`, `order_lines`, completed moves, labor attendance), the engine runs one day ahead, and the morning brief plus recommended option lands in the console before the real standup. The engineer approves or edits; approved moves export to the WMS as tasks. Each day of actual-vs-predicted recalibrates the twin. For the hackathon, the inbox is the stand-in: a judge types "Aisle 12 closed, forklift hit the upright" and the org re-plans live.

---

# PART C: THE BUILD DAY

## 18. Before 10:45 (no project code)

| Time | Who | Step |
|---|---|---|
| 09:00 | Sudhersan | Arrive, wifi, claim Crusoe credits, create the org, invite teammate. |
| 09:15 | Teammate | `curl` one chat call to each of Ultra, Super, Nano; note exact model ids, JSON mode support, latency. |
| 09:40 | Both | Read sections 1–5 of this doc together (15 min). Agree on the must/should/stretch cut. |
| 10:00 | Teammate | Skim the Crusoe starter repo for the hackathon; note anything that changes section 12. |
| 10:15 | Both | Create the public repo (empty), open the build agent(s) with this document as the brief. |
| 10:30 | Optional | `nemo-deepagents host probe` (read-only) to see if NemoClaw is feasible on the laptop. |

Rule: no code before 10:45. Reading, accounts, empty repo and an empty database are fine.

## 19. Repo layout

```
gemba/
  Makefile  pyproject.toml  README.md  DECISIONS.md  .env.example
  scenarios/   reslot_default.yaml  train_{1,2,3}.yaml  test_{1,2,3}.yaml
  profiles/    customer_first.yaml  efficiency_first.yaml
  org/roles/*.yaml   org/prompts/*.md   org/authority.py  org/schemas.py  org/agents.py
  org/standup.py  org/hiring.py  org/memory.py
  floor/model.py  floor/sim.py  floor/consequences.py  floor/forecast.py  floor/slotter.py
  floor/phaser.py  floor/economics.py  floor/levers.py
  data/gen.py  data/load.py  data/schema.sql
  engine/run.py  engine/events.py  engine/verifier.py  engine/drift.py  engine/fork.py  engine/store.py
  llm/client.py  llm/cache.py
  recordings/  hero.db  llm_cache.jsonl        (tracked in git)
  learn/retro.py  learn/playbook.py  learn/gate.py
  eval/baselines.py  eval/run_eval.py
  console/server.py  console/static/{index.html, app.js, floor.js, meeting.js, timeline.js, styles.css}
  tests/
```

Make targets: `make db` (create schema), `make data SEED=1` (generate `inputs.db`), `make test`, `make baselines`, `make run SCENARIO= PROFILE= PLAYBOOK= POLICY=org|static|do_nothing|big_bang`, `make fork RUN= DAY= LEVERS=`, `make eval`, `make retro`, `make console`, `make demo` (replay `recordings/hero.db`).

## 20. Phases, owners, gates (10:45–17:00)

Two people, each driving AI coding agents. Person A = floor + data + UI. Person B = org + engine + learning.

| # | Time | Owner | Build | Gate (must pass before moving on) |
|---|---|---|---|---|
| 0 | 10:45–11:00 | Both | Scaffold repo, env, LLM smoke test via `llm/client.py`, DB connect | One JSON-validated call per model tier with **measured latency** (if Super > 8 s or Ultra > 20 s per call, move more roles to Nano now); local SQLite stores (`inputs.db`, `runs.db`) work; a repeated call is served from the cache |
| 1 | 11:00–11:50 | A | `data/gen.py` with the 8 traps; write `inputs.db` | `--verify`: top-20% share ≥ 75%, family co-occurrence ≥ 0.4, 40% of top-200 in far aisles, PM overload ≈ 20%, key customer's lines concentrated in top-ranked move families |
| 1 | 11:00–11:50 | B | Role YAMLs and prompts (9 roles), schemas, authority matrix, `DecisionPolicy` interface, store | Live standup on Crusoe produces a decision with evidence ids and a rejected unauthorized lever; the re-run replays from cache at $0 |
| 2 | 11:50–13:00 | A | `floor/sim.py` hourly twin, levers, forecast, whatif (calibration can continue in phase 3) | Determinism test; targets 1–3 of 9.3a hold |
| 2 | 11:50–12:50 | B | `org/standup.py` full ritual on a stub twin; hiring; veto; lock; escalation | Scripted test: rack damage → Safety hired → veto → chair picks feasible option |
| 3 | 13:00–13:50 | Both | Integrate: `engine/run.py`, consequences C1, C2, C5, C6 first (others if time), verifier, drift, snapshots + fork | `make run POLICY=static` finishes 30 days; one cascade chain visible in `cascade_edges`; fork at day 69 diverges |
| 4 | 13:50–14:30 | B | Live run with Crusoe on default scenario (this becomes the first hero candidate); eval baselines; **start retro train runs (10-day) in the background at ~14:00** | Live 30-day run ≤ $1.50; eval table with 4 contenders on 1 scenario |
| 4 | 13:50–14:50 | A | Console: floor canvas, meeting room, timeline, compare, inbox | Replay of the live run end to end; fork button creates and streams a fork |
| 5 | 14:30–15:30 | B | Retro coach on finished train runs, verification forks, then the test gate (2–3 unseen 10-day scenarios, v0 vs v1 in parallel) | v1 produced; gate result recorded (pass or fail) |
| 6 | 15:30 | Both | **Decision gate** (section 21) | Pick the primary track and the hero runs |
| 7 | 15:30–16:15 | Both | Freeze. Record hero run, fork, profile comparison; README; 1-min video | Clean-clone `make demo` works offline |
| 8 | 16:15–17:00 | Both | Rehearse the 3-minute demo twice; submit | Submitted before 17:00 |

Cut order when a phase overruns by > 15 minutes: (1) Scenario Designer on NemoClaw (keep the Nano console box), (2) second profile, (3) consequences C3/C4/C8, (4) on-call roles beyond Safety and Quality, (5) retro (fall back to Done Means Done), (6) canvas animation (keep static map with colored loads).

Never cut: the floor twin, the standup with evidence, done-checks, one fork, the eval table.

**Critical path:** data gen → floor twin → live standup (cached) → run loop → live hero run → console replay. Everything else hangs off it. If the critical path is late, stop all side work and help it.

## 21. Decision gate at 15:30
- If v1 beats v0 on the test set with no new violations: submit under **Version N+1**. Headline = the before/after chart, then show the cascade and fork.
- If not: submit under **Done Means Done**. Headline = unattended month with self-written checks, drift recovery, and a fork proving a better decision. Show the retro as "work in progress".
Either way the same system is demoed; only the first 20 seconds of the pitch change.

## 22. Demo script (3 minutes)

| Time | On screen | Say |
|---|---|---|
| 0:00–0:20 | Title, then the floor | "I was the analytics engineer on a warehouse reslot. The target layout was easy. Getting there while the warehouse kept shipping was not, and the decisions made in daily standups were the real driver. No simulator models those decisions." |
| 0:20–0:50 | Run starts at day 61; standup streams; Megan calls in Sam after rack damage; Sam vetoes; floor shows aisle 7 hatched | "Gemba simulates the warehouse and the people who run it. Every claim cites a simulator run. Safety has a veto, Quality can lock customers, the director owns priorities." |
| 0:50–1:20 | Cascade trace: aggressive night → learning dip → mis-picks → customer escalation → Ellen locks SKUs → reslot slows | "Decisions cascade. That's what hardcoded simulations miss." |
| 1:20–1:50 | Fork at day 69 with "protect the Pick Module"; compare view shows divergence | "Fork any day with a different decision and watch the other future." |
| 1:50–2:20 | Judge types a disruption into the inbox; org re-plans live. If the Scenario Designer works, the judge instead asks it for a new scenario and it starts the run | "Any disruption, in plain words." |
| 2:20–2:45 | v0 vs v1 on unseen scenarios (or done-checks + drift if fallback), plus eval table | "After each run the org reviews its own worst cascades, proves a better move by forking, and keeps the lesson only if it wins on scenarios it has never seen." |
| 2:45–3:00 | Platform slide | "Reslotting is the first pack. Swap the twin and the org chart and it rehearses any operational change. Wargaming for operations." |

Backup: the hero run is recorded during the event with live Crusoe calls and committed as an SQLite export, so `make demo` replays it offline in the same console. If you use the replay on stage, say so ("this is the recorded run from 2 PM; here's the live fork"). Hiding it is an integrity risk.

Lead with the agents (the meeting room and the decisions), not the console, so nobody reads it as a dashboard project.

Explainer film (about 3 minutes, made before the event, figures illustrative): https://claude.ai/artifact/PDnNstug3PKZocwCLxPdxQ. It follows this calendar (rack damage on day 66, fork at day 69). Use it for the opening 20 seconds or as a backup if the live run fails. Shared as anyone-with-the-link.

## 23. Pitch lines and Q&A

- One line: "We simulate the warehouse and the people who run it, so you can rehearse a reslot before you live it."
- Positioning: "Slotting software tells you where everything should go. Nothing helps you get there while the warehouse keeps running."
- "Why not an optimizer with weights?" On a fixed scenario a tuned optimizer can match us, and our static-rules baseline is in the table. The org's edge is unstructured events, authority and veto dynamics, and the explanation a human needs before approving. And the optimizer can't tell you what happens when its own decisions trigger the next decision.
- "Is it realistic?" Synthetic data shaped on a real site, day-level and hourly approximations. We claim the mechanism and the comparisons, not real-world savings.
- "Do agents predict what real people will do?" No. They rehearse plausible positions backed by numbers. It's stress-testing, not forecasting.
- "Isn't the what-if cheating with future data?" No. What-ifs use a forecast from visible history and announced events. Only execution uses the actual held-out orders.
- "Where's the harness?" The org twin is the harness: it builds each stakeholder's context, routes the meeting, gives tools, and enforces authority in code. The floor twin is the deterministic environment it acts on. The Scenario Designer on NemoClaw is a meta-harness above it that picks what to rehearse.
- "What's recursive here?" The coach rewrites the org's playbook, verifies each lesson with a counterfactual fork, and the change is kept only if it improves unseen scenarios.

## 24. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Simulator numbers nonsensical by 12:50 | Tune constants, not data; fall back to day-level formulas from `research/HANDOFF.md` §4 |
| LLM latency makes runs slow | Quiet-day fast path, Nano for narrow roles, parallel positions, response cache, reasoning only for chair/director |
| JSON output flaky | Schema validation + one retry + safe fallback; engine tests use LLM-free baselines; org tests replay the committed LLM cache |
| Crew doesn't beat static rules | Check traps exist; give the org the right levers (staging clearance, protect); report honestly |
| Retro doesn't improve | Decision gate → Done Means Done track |
| Venue wifi down | All data is local SQLite; `recordings/` (LLM cache + hero run) replays offline. Crusoe itself has no fallback provider by decision |
| Scope creep | Section 5 is the contract; anything else goes in `DECISIONS.md` as "later" |
| Demo shows pre-built work | Everything in the repo is created after 10:45; this doc is a plan, not code. Don't paste code from earlier research files; write fresh code from this spec |

---

# PART D: DECISION LOG

Decisions we made, the alternatives, and why. Revisit only with new evidence.

1. **Two twins (floor + org) instead of agents placing SKUs.** Agents choose levers and negotiate; deterministic code moves SKUs and computes numbers. LLMs never invent metrics. Rejected: agents writing layouts directly (unverifiable, slow).
2. **Program Manager chairs daily; Director only on escalation.** Matches real sites, where directors don't sit in daily reslot standups. Rejected: Director as chair (less realistic), neutral Manager without a company profile (decisions had no anchor).
3. **Code scores options, LLMs generate and argue them.** Keeps decisions traceable to the company profile while the agents add what code can't: options, amendments, interpreting messy events, hiring, escalation. The chair may deviate from the top score with a logged rationale; the retro learns from deviations.
4. **Hourly floor twin, not second-by-second.** Enough to produce queues, jams, cutoffs and stockouts; fast enough for 3-day what-ifs in under a second. Rejected: discrete-event per picker (too slow to build and run).
5. **What-ifs use forecast demand; execution uses actual orders.** Fixes the leak in the earlier spec, where what-ifs ran on held-out actuals.
6. **Consequence engine with `caused_by` links.** This is the core novelty: endogenous events make decisions cascade, and the links make cascades explainable.
7. **Typed levers + authority matrix + veto/lock/capacity-truth powers.** Gives "politics" a precise, testable meaning and makes the org safe to extend with new roles.
8. **Explicit state machine, not a general agent framework.** Needed for determinism, replay, forks and cost control. Frameworks like Deep Agents stay optional behind the LLM client.
9. **Playbook lessons by tag, verified by fork, gated on unseen scenarios.** That is the Version N+1 loop. Rejected: embeddings/vector retrieval (banned "basic RAG" risk, less auditable), unverified self-edits.
10. **Local SQLite as the WMS stand-in (revised on the day).** `inputs.db` holds the WMS-like input tables and `runs.db` the run store, behind SQLAlchemy URL switches. Rejected: Neon Postgres (credential and wifi risk, no demo payoff that a local export can't show) and Parquet (a second format to maintain). Forks copy rows from daily snapshots.
15. **What-ifs see only the past and announced events.** Forecasts resample recent same-weekday history; the target layout uses days 1–60. This removes the held-out leak and makes the prediction-error drift flag meaningful.
16. **One scoring formula everywhere.** Options, days, runs, the eval and the train/test gate all use the formula in 10.4, so "better" means the same thing in every part of the system.
17. **Code decides quiet days, hiring and authority; LLMs argue and narrate.** Keeps runs cheap, deterministic and auditable.
11. **Plain JS + Canvas UI, no framework.** No build step to break; AI agents write it quickly.
12. **Primary track decided at 15:30 by evidence.** Version N+1 if the gate passes, else Done Means Done. Same system either way.
13. **NemoClaw is stretch, as a meta-harness.** OpenClaw in a NemoClaw sandbox designs scenarios and starts runs through the API; Gemba stays a controlled harness that the designer cannot bypass. Rejected: running the Gemba engine itself inside NemoClaw (custom runtime path, setup risk, no demo payoff). Nano console box is the fallback.
14. **Characters have names and personalities.** Purely for readability and demo; behavior is driven by owned KPIs, powers and levers, not by personality text.

Decisions settled on the day (Oct 17, after the 11:00 grilling session):

18. **Two-person plan stands.** If the teammate doesn't show by 10:45, switch to the solo cut and plan for Done Means Done from the start.
19. **Hero cascade made likely, never scripted.** Trap 8 (key-customer concentration) plus a test that the chain fires on at least one seed by 13:50.
20. **PM service time is 25 s/line** (section 9.2); 18 s is the bare pick time only.
21. **Static rules use the crew's 200-moves-a-night budget**, so the baseline comparison is fair.
22. **Done-checks come from a code template**; the chair only picks thresholds within code-set bounds.
23. **No fallback LLM provider.** We trust Crusoe; latency is handled by moving roles to Nano at the phase 0 gate.
24. **No mock LLM.** The org and the baselines share a `DecisionPolicy` interface; engine gates run on baselines, org gates run live on Crusoe with the response cache.
25. **`recordings/` is tracked in git**: `llm_cache.jsonl` (readable diffs) and `hero.db`, for offline tests and `make demo`.
26. **Ultra tier runs on Super (12:25).** Nemotron 3 Ultra is not listed in the Crusoe catalog today, so `MODEL_ULTRA` points at `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B`. The three-tier mapping in code is unchanged (chair/director/retro still run with reasoning on); switching back is one `.env` line. Live ids: Super `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B`, Nano `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` (not the Omni id in 12.1). Measured: Super 1.7 s, Nano 0.26 s per structured call; strict `json_schema` mode accepted.

Open questions to settle on the day (with defaults if nobody decides):
- Exact Super model id: copy from the console at 09:15.
- Temperature: 0 if outputs stay sensible, else 0.2 with the cache guaranteeing replay.
- Number of test scenarios: 3 (cut to 2 if runs take > 8 minutes each).
