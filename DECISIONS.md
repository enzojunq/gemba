# DECISIONS.md

Shared log of measured calibration and the decisions behind it. Append, don't rewrite.

## Person A: floor twin and data (Oct 17)

Measured with `uv run python -m floor.calibrate` and `engine.run`, seed 1, days 61–90, final `inputs.db` (13:00). "Normal days" = no scripted event active.

### 1. Calibration (spec 9.3a)

| Target | Do-nothing | Static rules (200 legs/night) | Oracle | Naive big bang | Holds |
|---|---|---|---|---|---|
| 1. Forklift utilization 0.80–0.95 | 0.81 | 0.78 on day 61, 0.2–0.5 once lines move to the PM | – | – | ✓ |
| 2. PM peak zone load 0.85–1.00 | 1.34 (trap 5) | 1.29 (d61) → 1.21 (d75) → 0.77 (d90) | 0.8 | – | ✓ falls with progress; stale layout breaches by design |
| 3. On-time 94–97 normal, < 90 on the labor-shortage day | 95.2 / 85.5 | 96.5 normal days; 0 jam hours on normal days | – | 75 on day 63 | ✓ |
| 4. Oracle LPH +15–30% | 71.2 | 86.6 (+21.6%) | 84.7 (+18.9%) | – | ✓ |
| 5. Big bang fails: ≥ 1 jam hour, mis-picks > 1% for 2 days, staging overflow | – | – | – | 8 jam hours (d63), mis-picks 1.19% / 1.63% (d62–63), staging 100% three nights, C4 → C2 → C6 | ✓ |

Other measured points: step_day 72–80 ms, 3-day what-if ~0.5 s, 30-day run 2.8 s; static progress 100% by day 81 (2,164 legs); safety_risk_max under static 0.91 on day 61 (daytime forklift traffic), 0.37–0.66 afterwards, never above 1.0; big bang night 61 breaches at 1.125. Hero cascade (customer_escalation caused by a `dec:` node) fires under big bang on seeds 1–3.

### 2. Tuned constants (`floor/constants.py`) and why

- `FORKLIFT_PER_M` 0.8 → 0.65: at 0.8 the forklift pool ran at 0.93 and held orders open ~3 h, jamming the consolidator 5–7 h on normal days under every policy. 0.65 keeps utilization at 0.81 with no normal-day jams.
- `REPLEN_TRIP_SECONDS` 360 → 300: with the original slot sizing the data implied ~610 just-in-time trips/day against ~576 of capacity; after the 3-day cover resize the pool runs at ~65% on normal days and ~85% during the surge, which is the intended "replen alert" pressure.
- `PM_FLEX_PENALTY` 1.25, `FORKLIFT_HELPS_PM` with `FORKLIFT_HELP_FRACTION` 0.5: pickers pinned to a zone could never clear zone 1's trap-5 backlog, and after a reslot 68–88% of lines land in the PM while forklift pickers idle. Idle pickers help at ×1.25; forklift help capped at half their idle time because 1.0 absorbed big bang's jams entirely (sweep 0.3–1.0).
- `CREW_LEGS_PER_NIGHT` 280 and `DIP_OVERLOAD_MAX` 2: a night beyond the regular crew's capacity scales that night's SKUs' dip multipliers (time and mis-pick) linearly up to ×2. Needed for target 5: at spec base rates, ×3 on PM lines tops out near 0.9% mis-picks. Static at 200 legs is unaffected by construction.
- `MAX_MOVERS_PER_AISLE` 2: the night scheduler dispatches round-robin across aisles. Without it a value-ordered batch put 33 legs in one aisle-hour, which read as 3.3 people and gave every reslot what-if a safety risk of exactly 1.10. Legs are also booked half to each end and capped at the movers on shift. Temps are not dispatched, so big bang's 9.6-mover night still breaches.
- `CONGESTION_PEOPLE` 0.5 (was a flat 1.0) and `DAMAGED_AISLE_PEOPLE` 1.0: congested staging adds half a truck to each adjacent aisle; a damaged aisle that stays open carries one extra person-equivalent per day hour so that `close_aisle` shows a visible benefit (day 66 what-if: risk 0.478 open vs 0.382 closed, one point of on-time).
- `CARRY_HOURS` 2: backlog carried to the next day is capped at two hours per pool; without the cap one returns spike kept the forklift pool a full day behind for the rest of the month.

### 3. Data decisions (`data/gen.py`, `inputs.db`)

- Active slot capacity = 3–6 days of cover (floor 20 units, rounded to 10), the 25 undersized trap SKUs at 0.4 days. The first sizing implied more replenishment trips than the reach-truck pool can serve.
- `slotting_target` is zone-capped (every zone ≤ ~3,350 lines/day, 0.81 of a zone's 4,147 capacity) at 0.68 PM share. The first target put 5,200–5,600 lines/day into three zones and 88% of lines into the PM, so even the oracle ran late.
- The target is swap-biased: 1,238 changed active slots, 463 moves into occupied slots, 2,164 legs, so the reslot takes ~11 nights at 200 legs as spec 7.1 intends.
- `skus.demand_group` (seasonal | fading | normal) carries the month-3 shift; scenarios refer to it as `family: seasonal` and the perturbation re-picks the groups from it.

### 4. Semantic decisions in the twin

- `reslot_progress_pct` = (planned legs − remaining legs) / planned legs. Executed legs undercount because a swap whose slot has emptied runs as one leg; progress reads 100 exactly when the queue is empty.
- `rack_damage` marks an aisle damaged, not closed: picking continues at extra risk, no moves. The org closes it with `close_aisle`, which zeroes the risk and reroutes lines to the pallet pool at ×1.5.
- `whatif(state, [], …)` is the do-nothing reference: no new decision, legs already approved for tonight still run. `pause_reslot` additionally halts them. `whatif` raises `ValueError` with the rejection reasons instead of silently dropping invalid levers.
- Night legs run in waves across the whole night (every approved move's first leg, then seconds, then thirds); an overloaded night therefore fills staging and leaves swaps in flight, which `staging_clearance` finishes first the next night.
- Temps work the day shift as forklift pickers (70%, ×2 mis-picks for 5 days) and join the night crew when approved legs exceed the regular movers, but are not dispatched by the aisle rule.
- `labor_allocate.headcount` is the absolute total for that pool on that shift; `demand_surge` is applied by the twin as seeded line resampling (actual and forecast), never baked into orders.

### 5. Known limits

- PM peak zone load on the stale layout is 1.17–1.7 daily against the profile's 1.10 hard limit, by design (trap 5). Feasibility must be judged against the do-nothing reference, which B's `feasibility(option, profile, ref)` does; the absolute check fails every option including do-nothing.
- Surge days 71 and 73 sit at 91–92% on-time under every lever combination tried (replen +2, seasonal slots resized, PM +4): a cutoff-mix effect of those days' orders, not a capacity constraint. Replen capacity is the binding constraint for stockouts during the surge, not for on-time.
- Big bang's night-time near-misses are expected values near 0.02 per night; the breach shows in `safety_risk_max`, not in drawn near-misses.
- The mini test fixture (281 lines/day) never queues, so headcount effects on on-time are asserted on the real database only.

## Person B: org, engine and learning (Oct 17)

Measured on the real twin, seed 1, days 61-90, final code (`PROGRESS_B.md` has the per-run detail; ids are in `recordings/hero.db`).

### 1. Measured

| Run | Wall | LLM calls / $ | Meetings (standup / escalation / quiet) | Hires | On-time | Mis-pick | LPH lift | Progress | Score vs do-nothing |
|---|---|---|---|---|---|---|---|---|---|
| Static rules (200 legs/night) | 10 s | 0 | - | - | 95.3 | 0.403 | +20.5% | 100% | 0.559 (21 template violations) |
| Crew, customer-first `bt-cb97aeb0` | 24 min | 209 / $0.30 | 19 / 1 / 10 | 14 | 95.5 | 0.399 | +20.7% | 100% (day 79) | 0.573 (19 violations) |
| Crew, efficiency-first `bt-449cd50d` | 23 min | 194 / $0.26 | 18 / 1 / 11 | 14 | 95.6 | 0.405 | +20.8% | 100% | 0.575 on the customer-first REF |
| Fork `fk-3b1e986f` (pause 3 at day 69) | 16 min | - | - | - | - | - | - | 100% (day 79) | identical 8/8 days before 69, different 22/22 after |

Per standup: 5-9 LLM calls, 1 what-if per option plus the do-nothing reference (3-6 per meeting, ~0.5 s each on the real twin); a quiet day is 1 Nano-tier call and 1 what-if. Super with reasoning on: 12-20 s per call, up to 57 s; Super plain: 3-4 s.

### 2. Decisions and why

- **Scoring is the spec 10.4 formula everywhere** (options, days, runs, gate), REF = the do-nothing what-if/run. Consequence worth knowing: on a 3-day horizon under customer-first, a 200-leg night's learning-dip mis-picks (+0.05 pt, weight 0.25 over 0.5 pt) cost more than its 9 pp of progress earn (weight 0.10 over 100 pt), so a customer-first chair rationally pauses on marginal days (hero day 64) while an efficiency-first chair moves. Kept as the profile's voice; the chair prompt says a paused night delays the whole reslot and ties break toward progress.
- **Feasibility is judged against REF** (`org/scoring.py`): the stale layout starts at PM load 1.3-1.4 against a 1.10 limit (trap 5), so an option breaches only if it is worse than the reference, with a 5% band on PM load; safety stays strict. Without this every reslot option was infeasible and the org only paused.
- **Code decides, LLMs argue**: quiet days, hiring/release (triggers in the role YAMLs, active while the event lasts), authority at commit (`org/authority.py`), lever validation before the what-if (ids C001-C200 / S0001-S2000, zones 1-6, aisles 1-28, headcount 1..roster cap), contradictory lever sets normalised (pause vs reslot: last word wins), additive options carry the standing reslot, a half-batch option is tabled by code when the engineer's batch breaches, a pause is always tabled when nothing feasible exists, same-outcome options never count as a tie, and the chair's escalate flag needs a code-detected reason.
- **Done-checks** come from a code template of the profile; the chair picks thresholds within bounds (it chose on-time 97 against the template's 95). The eval table counts violations at the template thresholds for every contender so the crew is not charged for judging itself more strictly.
- **Lesson acceptance (`learn/retro.py`)**: coach answered without fallback; fork done; its fork-day decision applied every alternative lever with no floor rejections; delta > 0.01 over the next 5 days; no LLM fallback in the fork or in the parent's window; hard-limit breaches not increased after the fork day. Gate (`learn/gate.py`): mean test score up and violations not up on test_1..3; no verdict if either side had fallbacks. Pass-1 retro rejected every lesson under this rule because every window had Crusoe 429s; pass 2 runs serialised on a bumped limit.
- **Rate limits**: Crusoe's Super request cap is per key and was shared by both laptops; the client retries 429s six times with backoff and positions go serial for the rest of a run after the first 429; eval and retro run one process at a time.
- **Models**: every call on Nemotron 3 Super on Crusoe (Ultra not in the catalog; Nano mapped to Super for quality); reasoning on only for chair decisions, the director, thresholds and the coach. Tier map is config.
- **Determinism and replay**: temperature 0, response cache keyed by model + messages + schema (`recordings/llm_cache.jsonl`), per-day pickled snapshots, forks copy the parent's rows before the fork day and restore the org's memory (`org_state`). `recordings/hero.db` is exported with `engine/recording.py` and replays offline.
