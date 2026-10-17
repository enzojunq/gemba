# Gemba: a decision twin for operations

Simulates a warehouse **and the people who run it**. A deterministic floor twin replays orders, labor and equipment hour by hour; an org twin of LLM agents holds a daily standup, argues with simulator evidence, and decides through typed levers. Decisions cascade, any day can be forked, and a retro coach improves the org's playbook on unseen scenarios.

Built at the Crusoe Recursive Agents Hackathon, Oct 17 2026.

## Read first
- `PLATFORM_SPEC.md`: what we're building and why (Part A), architecture (Part B), build plan (Part C), decision log (Part D).
- `CONTRACT.md`: the A/B boundary. Person A owns `data/ floor/ scenarios/ console/`; Person B owns `llm/ org/ engine/ eval/ learn/`. `contracts.py` is the only shared code.
- GitHub issues: #1 parent spec, #2 Person A, #3 Person B.

## Setup
```
cp .env.example .env      # fill CRUSOE_API_KEY, CRUSOE_BASE_URL, MODEL_ULTRA/SUPER/NANO
make install              # uv sync --extra dev
make test
```

## Run
```
make data SEED=1                         # generate inputs.db and verify the 8 traps
make run POLICY=static                   # 30-day run, no LLM
make run POLICY=org PROFILE=customer_first
make fork RUN=<id> DAY=69 LEVERS='[...]'
make eval
make retro
make console                             # http://localhost:8000
make demo                                # replay recordings/hero.db offline
```

## Console

One screen, FastAPI + SSE, no build step (`console/`). Reads the run store, writes only `inbox` rows and
`scenarios/<id>.yaml`, starts runs only through `engine.api`.

```
make console                                   # live store (runs.db), http://localhost:8000/?run=<id>
make demo                                      # replays recordings/hero.db and shows the Recorded badge
RUN_STORE_URL=sqlite:///path/to/runs.db uv run uvicorn console.server:app --port 8000
GEMBA_API_TOKEN=secret make console            # writes then need "Authorization: Bearer secret"
uv run python -m console.fixture --out fixtures/demo_runs.db   # hand-made two-run store for UI work
```
URL params: `run=<id>`, `day=<d>`, `autoplay=0`, `compare=<a>,<b>`. With no `run=`, the newest run that is
not a fork opens. Keys: space play/pause, ←/→ day, Esc closes drawers.

Layout (dark theme is the demo default; toggle top right):
- **Floor**: canvas plan view, 28 aisles coloured by load, hatched when closed, Pick Module zones as % of
  capacity, consolidator meter, staging buffer, dock with the next truck cutoff; night slot moves animate.
- **Meeting room**: who is in the room, "What just happened", the decision as the room's title once a
  meeting closes, the transcript as the biggest block, options in the dock (veto stamp, lock padlock).
- **Timeline**: days 61–90, event / decision / standup markers, four KPI lanes with target lines; click a
  day to jump, a marker for its cascade.
- **Cascade trace**: the chain behind a decision as one sentence plus a left-to-right graph.
- **Compare**: two runs side by side, synced timelines, a one-sentence verdict, the first divergent decision.

Scenario dialog (header button): describe in words, load, tweak events / demand perturbation / headcounts,
live YAML, Save, Save & run.

| Endpoint | What it does |
|---|---|
| `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/day/{d}` | run list, header + per-day rows, everything for one day |
| `GET /runs/{id}/stream?speed=&from_day=` | SSE replay at `speed` sim-hours/s, then tails a running run |
| `POST /runs {scenario, profile, playbook, policy, seed}` | start a run (`engine.api`, 503 if absent) |
| `POST /runs/{id}/fork {day, preset|levers}` | fork a run at `day`; the console opens the child |
| `POST /runs/{id}/inbox {text, day_hint}` | a disruption in plain words; the org reads it next morning |
| `GET /compare?a=&b=` | per-day KPI deltas, totals, first divergent decision |
| `GET /cascade/{run}/{node}` | nodes with human labels, edges, and the chain as a sentence |
| `GET /scenarios`, `GET /scenarios/{id}`, `GET /scenarios/meta` | list, raw YAML, field metadata from `contracts` |
| `POST /scenarios {yaml}` | validate (loader rules, exogenous kinds, `contracts.Scenario`) and write; protected ids refused |
| `POST /scenarios/describe {text}` | plain words → draft via `org.scenario_designer` (503 until present) |

Honesty rule: when the store path is under `recordings/`, every response carries `recorded: true` and the
header pill reads "Recorded · replaying". Say so on stage; a running engine shows "Live".

### Demo checklist
1. `make demo` (or `make console` for a live store); add `--host 0.0.0.0` to the uvicorn line if a second device must reach it.
2. Open `http://localhost:8000/` in the dark theme on the projector; the hero run opens by itself, check the "Recorded · replaying" pill.
3. Jump to day 66 (click the day): rack damage, Sam called in, the VETO stamp on option A, aisle 7 hatched.
4. Jump to day 67: Ellen's LOCKED chip; click the red consequence marker for the cascade sentence and graph.
5. Fork at day 69 with "Protect the Pick Module all week"; the child run opens streaming.
6. Compare the fork with the hero run: read the verdict sentence and the first divergent decision.
7. Type a disruption into the Inbox ("Forklift 3 is down for the afternoon"); the pending chip shows until the next simulated morning.
8. Open the Scenario dialog, describe a week in words or tweak an event, Save & run.
