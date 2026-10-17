# Talking points for the judges

What to say about each criterion, each sponsor, and each use case. The demo shows one scenario (a reslot);
the points below are about the platform. Numbers are from the recorded runs (`PITCH.md` has the card).

## What Gemba is (say once, early)
- A decision harness for operations: a deterministic twin of the floor plus an AI org that runs it, with a
  daily standup, simulator evidence for every option, typed levers with authority rules, and consequences that
  trigger the next decision.
- Gemba is the harness. The floor twin is the environment it acts on. NemoClaw's Scenario Designer is the
  layer above it that decides what Gemba runs.

## Why engineers need it (the gap in today's tools)
- Spreadsheets and slotting software give you the target. They say nothing about getting there while the
  building keeps shipping.
- Simulators and digital twins (FlexSim, AnyLogic, CreateASoft, Optioryx) model the physics well. Decisions
  are fixed inputs or hardcoded rules, so second-order effects never appear: the follow-up decisions are
  never made in the model.
- Real sites run on meetings with conflicting owners: ops wants throughput, safety can veto, quality protects
  customers, the shift lead owns headcount, the director owns priorities. Plans stall or flip in those rooms.
- Tribal knowledge: when the engineer leaves, nobody knows why the plan was shaped the way it was.

## What the engineer gets (use cases; the demo shows the first with a reslot)
1. **Rehearse (backtest):** run the month before you live it, with the scripted and surprise disruptions,
   and get a plan that already survived them plus the price of each trade-off.
2. **Fork a decision (what-if on a date):** pick any day, force a different call, see the other future next
   to this one, the first divergence and the cost. Deterministic twin plus cached model answers, so the history
   replays exactly.
3. **Compare company priorities:** same month under customer-first vs efficiency-first; see what each one
   trades away (day 64: one pauses to protect mis-picks, the other keeps moving).
4. **Cascades you can inspect:** every consequence, event, meeting and decision carries what caused it; the
   chain is a graph, not a hunch (big bang: decision -> staging full -> key-customer escalation -> director).
5. **Surprises in plain words:** type "Aisle 12 closed, forklift hit the upright" and the org re-plans next
   morning; describe a whole scenario in a sentence and it is drafted, validated and run.
6. **The decision record:** options, evidence ids, positions, vetoes, locks, rationale, hires with their
   triggers, done-checks and drift flags, per day, exportable. The next engineer can read why.
7. **Self-review (Version N+1):** the coach finds the worst day, traces it to the decision, proposes a lesson,
   proves it by forking that day, and the playbook is promoted only if it wins on unseen scenarios.
8. **Copilot at the real standup (future):** feed yesterday's actuals and today's known events; get tonight's
   recommended move list with the trade-offs already simulated; approved moves export to the WMS as tasks.

## Beyond the reslot (same harness, swap the twin or the calendar)
- Peak-season labor planning (temps, overtime, shift moves against a demand ramp).
- A WMS cutover or an automation go-live week (degraded throughput, training dips, rollback decisions).
- Onboarding a large customer (slotting, cutoffs, key-customer protection).
- Closing a dock or an aisle for repairs; equipment outage drills (conveyor zone down, forklift breakdowns).
- Safety and compliance drills: what a leadership visit or an audit week does to the plan.
- Any plant, 3PL or distribution network where decisions are made in a daily meeting by people with
  different KPIs. The org chart is config; adding a role (a union rep who blocks overtime) is a YAML file.

## Hackathon criteria, in our words
- **Technicality:** deterministic hourly floor twin (28 aisles, 6-zone pick module, ~24k lines/day); typed
  levers behind an authority matrix (veto, lock, capacity truth, director); consequence engine with causal
  links; forkable deterministic runs (per-day snapshots, LLM response cache); what-ifs on forecast only (never
  the held-out month); train/test scenario split with event kinds unseen in training; 308 tests.
- **Creativity:** the meeting is the simulated object. "The Sims for warehouse decisions."
- **Live demo:** the recorded month (say so), live fork on a static parent if asked, live inbox, live scenario
  dialog, live Scenario Designer on the second laptop.
- **Future potential:** wargaming for operations; copilot at the standup; any site, any change, any org chart.
- **Theme "a team of agents you can walk away from":** 30 days unattended, 20 meetings, 14 hires, $0.30.
- **Done Means Done:** the org writes its own done-checks (chair picks thresholds within code-set bounds),
  code verifies every simulated day, drift detectors catch churn, stalls, prediction error and overspend.
- **Shared Brain:** the board (constraints, locks, agreements, evidence, playbook) is read before every turn
  and written after; agents hold no hidden state, which is what makes forks and replays exact.
- **Version N+1:** lessons by tag, verified by counterfactual forks, gated on unseen scenarios. Today: three
  lessons passed their forks; the held-out gate did not promote. Say both; the rule is the point.

## Sponsors: where each one is used
- **NVIDIA Nemotron 3 (on Crusoe):** every agent call. Super 120B for all roles today (Ultra is not in the
  catalog; Nano is mapped to Super for quality, the tier map is config). Reasoning toggle on only for the
  chair's decision, the director's ruling, the threshold pick and the retro coach; off for stakeholders and
  narration. Strict JSON-schema output for every role (pydantic schemas); one validation retry then a safe
  default, never a crash. Prompt order fixed (role prompt -> playbook rules -> board -> task) to keep the KV
  cache warm. 209 calls and $0.30 for a 30-day month; 1.7 s per plain call, 12-20 s with reasoning.
- **Crusoe Managed Inference:** the endpoint for all of it, OpenAI-compatible; cached-token accounting; we
  learned its request cap the hard way and built backoff, serial positions after a 429, and a no-verdict rule
  when fallbacks touch a gate.
- **NVIDIA NemoClaw / OpenShell:** the Scenario Designer, an OpenClaw agent in a NemoClaw sandbox on the
  second laptop, with a custom skill that can reach only the console's scenario and run routes through a
  bearer token; it cannot fork, pull a lever or touch the floor. Production argument: agents that act on
  operations need a blast-radius boundary; Gemba's typed levers are the inner boundary, OpenShell the outer.
- **Claude Code (how we built it):** two people, each driving coding agents, a written contract between the
  halves, an append-only comms file between the two agents, independent review agents for the loop's
  integrity and the pitch.

## Honesty lines (use them before someone else does)
- Synthetic data shaped on a real site; hourly, pool-level; we claim the mechanism and the comparisons, not
  savings.
- The month on screen is a recording from this afternoon; live runs use the newest twin.
- What-ifs never see the held-out orders.
- The crew beats static rules by small margins on one seed; the gate covers 3 scenarios x 1 seed with 5-day
  forks: evidence, not proof.
