# CONTRACT.md: the A/B boundary

Read this before writing any code. It defines who owns what, and the exact interface between the two halves of Gemba so that Person A and Person B (and their coding agents) can build in parallel and integrate at 13:00 without surprises.

- Product and design detail: `PLATFORM_SPEC.md` (section numbers below refer to it). Agent-ready summary: GitHub issue #1.
- If this file and the spec disagree on an interface, **this file wins**. If they disagree on behaviour, the spec wins.

## 0. Rules

1. **The contract lives in two places:** this file and `contracts.py` at the repo root (pydantic models + the `FloorTwin` / `DecisionPolicy` protocols). Nothing else is shared.
2. **Freeze at 11:45.** After that, a contract change needs both people to agree, and the change to `contracts.py` and `CONTRACT.md` goes in **one commit** whose message starts with `contract:`. Tell the other person before pushing.
3. **Stay in your folders.** Don't edit the other owner's folders. Need something from the other side? Ask, or add a stub on your side.
4. **Import direction:**
   - `floor/` and `data/` import only `contracts`.
   - `org/`, `engine/`, `learn/`, `eval/` and `llm/` import `contracts` and call the twin **only through the `FloorTwin` protocol**.
   - `console/` imports `contracts` and reads the run store. It calls the engine only through the functions in section 8.
5. **No LLM anywhere in A's code.** The floor twin, the data generator and the consequence engine are pure, deterministic Python.
6. **No mock LLM** (decision 24). B tests the engine with the LLM-free baselines and the org live on Crusoe, using the response cache.

## 1. Ownership

| Area | Owner | Folders |
|---|---|---|
| Data generator, `inputs.db` schema, verify mode (8 traps) | **A** | `data/` |
| Floor twin: state, hourly sim, levers, forecast, what-if, consequence engine, slotter, phaser, economics, calibration | **A** | `floor/` |
| Scenario YAML schema + files | **A** | `scenarios/` |
| Console server (FastAPI/SSE) + static UI | **A** | `console/` |
| LLM client, cache, cost log, `recordings/llm_cache.jsonl` | **B** | `llm/` |
| Org twin: roles, prompts, schemas, authority, standup, hiring, memory, Event Interpreter | **B** | `org/` |
| `DecisionPolicy` + baselines (do-nothing, naive big bang, static rules 200/night) | **B** | `eval/` |
| Scoring + feasibility (section 10.4), profile YAML | **B** | `org/` or `engine/`, `profiles/` |
| Run engine: day loop, run store (`runs.db`), cascade edges, verifier, drift, snapshots, forks | **B** | `engine/` |
| Retro Coach, playbook, train/test gate, eval harness | **B** | `learn/`, `eval/` |
| `contracts.py`, `CONTRACT.md` | **both** (B drafts, A reviews by 11:45) | repo root |
| `Makefile`, `pyproject.toml`, `README.md`, `DECISIONS.md` | both (append, don't rewrite) | repo root |

## 2. Pipeline at a glance

```
          A                                   B                                  A
 data/gen ──> inputs.db ──> FloorTwin  <── engine day loop ──> runs.db ──> console (reads)
 scenarios/*.yaml ─────────>   ^   |            |    ^                        |
                               |   |  DayResult,|    | Decision (levers)      | inbox rows,
                     levers ───┘   |  WhatIf,   v    |                        | start/fork calls
                                   |  Events  DecisionPolicy (org | baseline)  v
                                   └──────────────────────────────────────> engine API (sec 8)
```

The engine owns the clock. The twin never calls the org and never writes to `runs.db`. The org never touches `FloorState`.

## 3. Shared types (`contracts.py`)

These are the exact field names. Types are pydantic v2 models unless noted. Ids are strings.

### 3.1 Ids and causal links
- Node id prefixes: `dec:<id>`, `evt:<id>`, `mtg:<id>`, `con:<id>`, `kpi:<run_id>:<day>:<kpi_name>`.
- Every `caused_by` field is one of these node ids, or `None` for root causes (scripted events).
- B's store writes a `cascade_edges` row for every insert that has `caused_by`. A only has to fill `caused_by` correctly.

### 3.2 `Lever` (B produces, A applies)
A discriminated union on `kind`. Every lever carries `decision_id: str`.

| kind | args |
|---|---|
| `reslot_tonight` | `n_moves: int`, `order: "value"\|"zone"\|"family"`, `include_zones: list[int]`, `exclude_zones: list[int]` |
| `pause_reslot` | `days: int` |
| `labor_allocate` | `shift: "day"\|"swing"\|"night"`, `pool: Pool`, `headcount: int` |
| `overtime` | `shift`, `pool: Pool`, `hours: float` |
| `hire_temps` | `count: int`, `start_day: int`, `days: int` |
| `protect` | `target: "zone"\|"skus"\|"customer"`, `ids: list[str]`, `days: int` |
| `resize_slots` | `skus: list[str]`, `days_cover: float` |
| `wave_priority` | `mode: "rush_first"\|"by_cutoff"\|"by_zone"` |
| `close_aisle` | `aisle: int`, `days: int` |
| `reopen_aisle` | `aisle: int` |
| `staging_clearance` | `n_movers: int` |

- `Pool` = `"pm" | "forklift" | "pallet" | "replen" | "consolidation" | "loading" | "night_move" | "temp"`.
- `set_profile` and `escalate` are **not** floor levers. They never reach the twin (B handles them in the org/engine).
- A **must** tag every SKU move, overtime hour and staging slot with the lever's `decision_id`. C1/C2/C3/C4 attribution depends on it.

### 3.3 `LeverRejection` (A returns)
`LeverRejection(lever: Lever, reason: str)`. This is for **physical** invalidity only (unknown aisle/zone/SKU, headcount above roster, more temps than the pool allows). **Authority** (who may issue what) is B's, checked before calling the twin.

### 3.4 `Event` (shared)
```
Event(event_id: str, kind: EventKind, day: int, hour: int = 6, args: dict,
      announced_day: int, source: "scripted"|"inbox"|"consequence", caused_by: str | None)
```
- **Exogenous kinds (section 9.5):** `labor_shortage`, `demand_surge`, `rack_damage`, `pm_zone_down`, `leadership_visit`, `key_customer_week`, `new_sku_launch`, `returns_spike`, `wms_sync_delay`, `forklift_breakdown`.
- **Consequence kinds (section 9.4):** `customer_escalation`, `absenteeism`, `staging_congestion`, `safety_review`, `aisle_closure`, `director_escalation`, `replen_alert`.
- The `args` keys per kind are listed in `contracts.py` as a dict `EVENT_ARGS: dict[EventKind, list[str]]`. A owns the meaning of each kind.
- `announced_day = day − lead_time(kind)` (section 9.4a). **B filters**: agents and what-ifs only get events with `announced_day <= today`. A's `step_day` receives only events active today.
- The Event Interpreter (B) must output this exact type.

### 3.5 Demand
```
DayDemand(day: int, orders: DataFrame, lines: DataFrame)   # arbitrary_types_allowed
  orders columns: order_id, release_hour, customer_id, priority ("normal"|"rush"), cutoff_hour
  lines  columns: order_id, line_no, sku_id, qty
```

### 3.6 `FloorView` (A produces, B reads; B never reads `FloorState`)
```
FloorView(day, frozen_aisles: list[int], closed_aisles: list[int],
          protected: list[Protection],            # target, ids, until_day, decision_id
          staging_occupancy: float, move_queue: int, moves_done_total: int, moves_target_total: int,
          reslot_progress_pct: float,
          labor_available: dict[Shift, dict[Pool, int]], temps_active: int,
          learning_dip_skus: int, overtime_days_last4: dict[Pool, int])
```
`FloorState` is private to A. It must be **picklable** (B snapshots it after every day) and immutable per day (copy on write).

### 3.7 `DayResult` and `WhatIf` (A produces)
```
DayResult(day, kpis: Kpis, hourly: dict, violations: list[str])
WhatIf(levers: list[Lever], days: int, kpis_by_day: list[Kpis], kpis_total: Kpis,
       expected_near_misses: float, safety_risk_max: float, pm_peak_zone_load: float)
```
- `Kpis` holds exactly the names from section 9.3: `lines, lph, throughput_lph_site, on_time_pct, mispick_rate, pm_peak_zone_load, consolidator_queue_max, jam_hours, replen_trips, stockout_lines, overtime_hours, labor_cost_usd, moves_done, moves_queued, reslot_progress_pct, staging_occupancy_max, safety_risk_max, near_misses`, plus `mispick_rate_by_customer: dict[str, float]`.
- `hourly` is **owned by A**. A's sim writes it, A's console reads it, and B stores it as opaque JSON.
- `WhatIf` runs on **forecast demand only**. It never touches actual held-out orders, and near-misses are expected values, not draws.
- **Scoring is B's job.** A returns raw KPIs. B computes the scorecard, the feasibility verdict and the do-nothing reference (`whatif(state, [], days, known)`).

### 3.8 `Decision` (B produces)
```
Decision(decision_id, day, levers: list[Lever], constraints: list[Event] = [], decided_by: str,
         rationale: str, option_id: str | None, meeting_id: str | None)
```

### 3.9 Scenario (A owns the schema) and Profile (B owns)
- **Scenario YAML:** `id, seed, days: [start, end], events: [Event without event_id/announced_day/source], perturbation: {family_scale_pct, seasonal_group_seed, surge_shift_days}, overrides: {headcounts, budgets, ...}`. A's `load_scenario(path) -> Scenario` validates it; `POST /scenarios` uses the same validator.
- **Profile YAML:** section 10.4 exactly (`weights, targets, hard_limits, budgets`). B's `load_profile(path) -> Profile`.

## 4. `FloorTwin` protocol (A implements, B calls)

```python
class FloorTwin(Protocol):
    def initial_state(self, scenario: Scenario) -> FloorState: ...
    def view(self, state: FloorState) -> FloorView: ...
    def apply_levers(self, state, levers: list[Lever], day: int) -> tuple[FloorState, list[LeverRejection]]: ...
    def actual_demand(self, day: int) -> DayDemand: ...
    def step_day(self, state, day: int, demand: DayDemand, events: list[Event]) -> tuple[FloorState, DayResult]: ...
    def consequences(self, state, result: DayResult, run_id: str) -> list[Event]: ...   # future-dated, caused_by set
    def forecast_demand(self, state, day: int, horizon: int, known_events: list[Event]) -> list[DayDemand]: ...
    def whatif(self, state, levers: list[Lever], days: int, known_events: list[Event]) -> WhatIf: ...
    def recalibrate(self, state, actual: list[DayResult], predicted: list[Kpis]) -> FloorState: ...
```

Invariants A guarantees:
- Same `(inputs, scenario.seed, levers, events)` gives the same outputs. Mis-pick RNG is keyed by `(seed, day, order_id, line_no)`. No global RNG, no wall-clock.
- `whatif` and `forecast_demand` never read days later than `day − 1` from actuals.
- `step_day` < 300 ms and a 3-day `whatif` < 1 s on the default scenario.
- `apply_levers` + `step_day` covers the day 06:00 to 06:00 the next day, including night moves.

Deterministic tools agents may call, through B's Analytics role: `slotter.propose_target`, `phaser.rank_moves`, `phaser.select_tonight`, `economics.price_option` (section 9.6). They are A's, take and return contract types or plain dicts, and are documented in their docstrings.

## 5. `DecisionPolicy` protocol (B implements, engine calls)

```python
class DecisionPolicy(Protocol):
    def pick_thresholds(self, checks: list[DoneCheck]) -> list[DoneCheck]: ...
    def decide(self, day: int, view: FloorView, events: list[Event], twin: FloorTwin, state: FloorState) -> Decision: ...
```
- `twin`/`state` are passed only so the org can request `whatif`s through Analytics; the policy must not mutate `state`.
- The implementations are `OrgPolicy` (the standup, section 10.5), `DoNothing`, `NaiveBigBang` and `StaticRules`. A can run the real twin end to end with `make run POLICY=static` without any LLM.

## 6. Day loop (B owns, shown so A knows the call order)

```
state = twin.initial_state(scenario)
checks = policy.pick_thresholds(verifier.template(profile))
for day in scenario.days:
    events   = calendar[day] + inbox(run_id, day) + pending_consequences[day]
    view     = twin.view(state)
    decision = policy.decide(day, view, visible(events, day), twin, state)
    state, rejections = twin.apply_levers(state, decision.levers, day)
    state, result     = twin.step_day(state, day, twin.actual_demand(day), active(events, day))
    pending_consequences += twin.consequences(state, result, run_id)
    store.write_day(run_id, day, decision, rejections, result, predicted=chosen_whatif)   # one transaction
    verifier.check_day(...); drift.check(...); snapshot(state, day)
```

## 7. Run store (`runs.db`): B writes, A's console reads

- Schema: spec section 8.2 exactly, plus `day_results.snapshot_path` and one new table:
  `inbox(run_id, msg_id, day_hint, text, received_at, consumed_day NULL)`.
- B commits **each simulated day's rows in one transaction** at the end of that day. The console tails `runs.db` by `(run_id, day)` and `(meeting_id, seq)`, and turns new rows into SSE. No other channel between the engine and the console.
- The console **writes only** `inbox` rows. The engine consumes them at the start of the next simulated day and sets `consumed_day`.
- `utterances.kind` ∈ `proposal | position | veto | lock | question | decision`. The meeting-room UI renders from `meetings`, `utterances`, `options`, `decisions`, `hires`.
- The hero run and the LLM cache are exported to `recordings/hero.db` and `recordings/llm_cache.jsonl` (tracked in git). `make demo` points the console at `recordings/hero.db`.

## 8. Engine API (B exposes, A's console calls)

```python
engine.api.start_run(scenario: str, profile: str, playbook: str, policy: str = "org", seed: int | None = None) -> str   # run_id; runs in a background process
engine.api.fork_run(parent_run_id: str, day: int, levers: list[Lever] = [], constraints: list[Event] = []) -> str
engine.api.run_status(run_id: str) -> Literal["queued", "running", "done", "failed"]
```
A builds the HTTP endpoints from section 14 on top of these plus direct reads of `runs.db`. `POST /runs/{id}/inbox` inserts an `inbox` row.

## 9. Stubs so nobody waits

| Needed by | Stub | Written by | Replaced at |
|---|---|---|---|
| B (standup, engine) | `StubTwin`: implements `FloorTwin` with fixed `WhatIf`/`DayResult` values, honours `close_aisle`/`protect` in `view()` | **B**, in `engine/` or `tests/` | 13:00 |
| A (sim, consequences) | runs the twin with `StaticRules`/`DoNothing`, or a 10-line loop in `tests/` | **A** | 13:00 (real engine) |
| A (console) | a hand-made `runs.db` fixture with 3 days of rows | **A** | ~14:00 (first live run) |

## 10. Timeline and gates per person (spec section 20)

| Time | Person A | Gate | Person B | Gate |
|---|---|---|---|---|
| now–11:45 | review `contracts.py` | contract frozen | draft `contracts.py`, LLM client + cache, Crusoe smoke test | one JSON call per tier with latency; cache hit on repeat |
| 11:45–12:30 | `data/gen.py`, `inputs.db`, `--verify` | 8 traps hold | roles, prompts, schemas, authority, store, `StubTwin` | live standup → decision with evidence ids + one rejected lever; re-run from cache at $0 |
| 12:30–13:00 | `floor/sim.py`, levers, forecast, what-if | determinism test; calibration targets 1–3 | standup ritual, hiring, veto, lock, escalation | rack damage → Safety hired → veto → feasible pick |
| 13:00–13:50 | **integrate:** real twin into B's engine; consequences C1, C2, C5, C6 | `make run POLICY=static` 30 days; a cascade chain in `cascade_edges`; fork at day 69 diverges; hero cascade test passes on ≥ 1 seed | **integrate:** engine loop, verifier, drift, snapshots, forks | same |
| 13:50–14:50 | console: floor, meeting room, timeline, compare, inbox | replay of live run; fork button streams | live hero run, baselines eval, start retro train runs ~14:00 | run ≤ $1.50; eval table with 4 contenders |
| 14:50–15:30 | calibration polish, stretch (Scenario Designer) | — | retro, verification forks, test gate | v1 produced, gate recorded |
| 15:30 | **decision gate** together | | | |

## 11. Open points to settle at the 11:45 review
- `EVENT_ARGS` keys per event kind (A proposes in `contracts.py`).
- `Kpis` units: percentages as 0–100 (`on_time_pct`, `mispick_rate` in %) or fractions. **Default: 0–100 everywhere, matching the profile targets.**
- Days are integers 1–90; hours 0–23; the night belongs to the day it starts on.
