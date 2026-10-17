# Gemba pitch kit

Three pieces: the two-liner, the one-minute submission pitch, the three-minute one-on-one at the laptop.
Every number is from the recorded runs of 17 Oct in `recordings/hero.db` (customer-first hero
`bt-cb97aeb0`, efficiency-first `bt-449cd50d`, fork `fk-3b1e986f`, naive big bang `ev-98548726`). Say
"recorded this afternoon" when replaying; the inbox and the scenario dialog are live. **Two consoles on
stage:** `make demo` (recording; new run / fork / inbox answer 409 there by design) for the replay beats, and
`make console` (live store, `runs.db`) for the inbox, the scenario "Save & run" and any live fork on a static
parent. Switch tabs; say which one you are on.

---

## The two-liner

Gemba is a decision twin for operations: connect your floor twin, define your org (each stakeholder with a KPI
to defend), your priorities and your scenarios, and an AI version of that org rehearses the plan day by day,
negotiating trade-offs and acting through typed levers, so you see how decisions cascade over time before you
make them for real. Fork any day to backtest a different call, inject surprises, and keep the org's full
decision record: the tribal knowledge of why things were decided, which the org itself learns from.

---

## One minute: the submission pitch (about 170 words)

Every operational change gets planned on a spreadsheet and then survives contact with reality in a daily
standup. Simulators and digital twins model the floor. Nobody models the meeting, and nobody keeps why the
decisions were made.

Gemba is a decision twin. You connect the floor twin you have, define your org, each role with its own KPI to
defend, set the company's priorities, and write the scenarios. Then an AI version of that org runs the plan
day by day: it holds the standup, every option carries a simulator what-if, stakeholders negotiate, the chair
decides inside the authority rules, and the floor answers. A decision has consequences, and consequences
trigger the next decision, so you watch cascades unfold over weeks, not one night.

You fork any day to backtest a different call, inject a surprise in plain words, compare priorities on the
same month. Every option, objection and number stays in the decision record, so when an engineer leaves the
reasoning doesn't. And the org learns from its own mistakes: lessons are proven by forking and promoted only
on unseen scenarios. A meta-harness above it, sandboxed in NemoClaw, updates scenarios and org settings; fed
with live floor data and real decisions it becomes the standup's copilot.

Today it rehearses a warehouse reslot. The pattern is any operational change.

---

## What we show for each claim

| Claim | Where it is on screen |
|---|---|
| Connect your twin, define your org, priorities, scenarios | `org/roles/*.yaml` (a role is a file), `profiles/*.yaml`, the Scenario dialog; the twin behind a protocol (`CONTRACT.md`) |
| Stakeholders defend their own KPI and negotiate | Meeting room, day 66: Sam (safety) proposes closing aisle 7, Ryan (throughput) opposes the reslot on PM load, Megan decides; positions cite what-if evidence ids |
| Cascades over time | Cascade graph on the big-bang run `ev-98548726` day 64: decision → staging full → key-customer escalation → director; hero day 73: on-time miss → Dana |
| Fork and backtest a decision | Fork `fk-3b1e986f` at day 69 (pause 3 days) vs the hero in Compare: identical before, divergence on day 69, both finish day 79 |
| Compare priorities | Customer-first hero vs efficiency-first hero: day 64 one pauses, one moves; month within a tenth of a point |
| Inject surprises | Inbox (live) and the Scenario dialog "Draft from words" (live); the Scenario Designer in the NemoClaw sandbox on the second laptop |
| Tribal knowledge / decision record | Transcript, options with scores, hires with triggers, checks, drift flags per day; `decisions`/`utterances`/`evidence` tables; the Board drawer |
| The org learns from its mistakes | Learning view: three lessons verified by fork (bars, "Open the fork"); the gate on unseen scenarios with its verdict and rule |
| Meta-harness and copilot | Scenario Designer live on A's Mac (writes scenarios, starts runs, cannot touch the floor); copilot described as the next step: same loop fed with yesterday's actuals and today's real decisions |
| Self-checks and unattended operation | Timeline threshold lines and failed-check ticks; drift markers; 30 days, 20 meetings, 14 hires, $0.30, no human in the loop |

---

## Three minutes: one on one with a judge, at the laptop

Not a speech. You are standing next to someone, the console is open on the recorded run. Say what you click.
Brackets mark the action and whether it is recorded or live. Roughly 430 words plus clicks.

**Why we built it (about 25 s).** [recorded hero, floor view, day 61]
"Quick context. I was the analytics engineer on a warehouse reslot. The slotting software gave us the target
layout in a day. Getting there took six weeks, because the warehouse keeps shipping while you move things, and
stuff keeps happening: people call in sick, a rack gets hit, a customer has a big week. Every time there's a
meeting where ops, safety, quality and the program manager argue about tonight. Our simulators modelled the
floor fine. They didn't model that meeting, and nobody kept why we decided what we decided. Gemba is a
decision twin: you plug in your floor twin, define your org and priorities, and it rehearses the plan with
the people in it."

**The org (about 40 s).** [scrub to day 66: rack damage, Sam called in, his option on screen]
"Gemba is a harness: code that runs the meeting, hands the agents simulator evidence, and enforces who may
decide. Around it, a deterministic twin of the site, hourly. The agents are Nemotron 3 Super on Crusoe,
reasoning on for the chair, the director and the coach; the tier map is config. Megan chairs. Ryan owns
throughput. Sam is safety, he has a veto. Ellen is quality, she can lock a customer's SKUs. Marcus owns how
many people we really have. Dana only shows up on escalations. Day 66: a rack got hit overnight, so the code
pulls Sam into the room. He proposes closing aisle 7. The what-ifs say that costs more than it saves, Megan
keeps the reslot running, and Sam's objection stays on the record. Those levers are typed; the floor only
takes what the authority rules allow. This month ran with no human in the loop: thirty days, twenty meetings,
fourteen people hired into the room, thirty cents of inference. Recorded this afternoon."

**The cascade (about 30 s).** [switch to the big-bang run `ev-98548726`; click the day-64 consequence marker]
"This is what a normal simulation can't give you. Here's the naive plan, move everything in two nights. That
decision fills staging on day 62, the pickers don't know the new slots, mis-picks hit the key customer, that
escalates on day 64, and on 65 the director is pulled in. Every record points at the decision that caused it,
so you walk the chain instead of guessing. In the crew's month the chains are gentler: the surge empties
slots, two days of stockouts bring the replenishment lead in, a missed on-time day escalates to Dana."

**Rehearsing and forking (about 35 s).** [back to the hero; Fork, day 69, "pause 3 days" (recorded `fk-3b1e986f`); Compare]
"So the way you use it: run the month before you live it, then second-guess yourself on purpose. This fork
ran this afternoon: same history up to the morning of day 69, then a three-day pause instead of moving. The
twin is deterministic and every model answer is cached, so the history replays exactly; what-ifs only ever see
a forecast, never the held-out month. Compare shows where they diverge and what it cost: the pause holds
progress at fifty-five percent for three days, the crew catches up, both finish on day 79. And priorities
change minds: on day 64 the customer-first org pauses to protect mis-picks, the efficiency-first org keeps
moving. Over the month they land within a tenth of a point."

**Surprises (about 25 s).** [live: Inbox "Aisle 12 closed, forklift hit the upright"; then the Scenario dialog]
"You can throw things at it mid-run. Type a disruption in plain words and the org reads it next morning.
Or describe a whole scenario in a sentence: a Nemotron call drafts it, the console validates it, and it runs.
On the second laptop the same request comes from an OpenClaw agent in a NemoClaw sandbox that can only reach
the console's scenario and run routes: it sits above Gemba and can't touch the floor."

**Memory and learning (about 30 s).** [Learning view, if the gate row is in; otherwise the decision record]
"Two more things. When an engineer leaves, this is the record: every option, objection and number, with the
reasoning. And the org learns from itself: after a run a coach finds the worst day, traces it to the decision,
proposes a different move and a one-line lesson, and tests it by forking that day. We keep a lesson only if
the fork actually applied it and did better by a margin, with no rate-limit fallbacks in the window."
- Gate result (16:36, learning pass on DeepSeek-V4-Pro): "This afternoon the coach proposed six lessons; the
  rule accepted one. The new playbook then beat the old one on three scenarios it never trained on: score
  0.4794 to 0.4800, violations 18.7 to 18.0, no fallbacks. A promotion by the rule, by a hair. Three
  scenarios, one seed, five-day forks: evidence, not proof. Earlier today a lenient version of the rule
  accepted three lessons and the gate refused them; both are in the Learning view."

**Close (about 20 s).** [floor]
"The reslot is just today's scenario. The twin is behind a protocol and the org chart is config, so the same
loop rehearses a peak season, a WMS cutover, a new customer. The meta-harness above it changes scenarios and
org settings; feed it live floor data and the real decisions people make, and it's the copilot in tomorrow's
standup, steered by what actually happened. That's a new kind of simulation: one that argues, remembers, and
learns from its own mistakes. Happy to dig in."

---

## Numbers to have ready (recorded, seed 1, `reslot_default`)

- Site: 28 aisles, 6-zone pick module, 2,000 SKUs, ~2,000 orders/day, ~24,000 lines/day, 90 days (60 visible,
  30 held out); 2,164 move legs.
- Customer-first hero `bt-cb97aeb0`: 30 days, 24 min, 209 calls, $0.30 (8 calls fell back on rate limits);
  19 standups + 1 escalation + 10 quiet days; 14 hires, each with a logged trigger; reslot 100% on day 79;
  on-time 95.5%, mis-pick 0.40%, LPH 86.0 (+20.7% vs doing nothing).
- Versus static rules (200 moves a night, pause on event days): ahead on every column by small margins on one
  seed: score 0.573 vs 0.559, on-time 95.5 vs 95.3, LPH +20.7 vs +20.5, mis-pick 0.399 vs 0.403, template
  violations 19 vs 21. (Crew score from B's log; static from `eval_results`.)
- Efficiency-first `bt-449cd50d`: on-time 95.6, mis-pick 0.41, LPH +20.8%, 100%, $0.26.
- Fork `fk-3b1e986f` (pause 3 at 69): identical 8/8 days before, different 22/22 after; both 100% on day 79.
- Naive big bang `ev-98548726`: `dec:d61` → staging_congestion d62 → customer_escalation d64 →
  director_escalation d65. On final code (`ev-a960a7b5`): 18 jam hours over the month, 8 of them on day 63;
  mis-picks 1.2–1.6% on days 62–63; staging full three nights. (Reconciled with A, COMMS A-11.)
- Tests: 308 passing. Models: every call on Nemotron 3 Super (Ultra not in the catalog today).
- Lesson rule: fork applied the alternative, delta > 0.01 over the next 5 days, no new hard-limit breach, no
  LLM fallback in the window. Gate: mean score up and violations not up on test_1..3 (two unseen event kinds).

## Q&A one-liners

- Optimizer with weights? On a fixed scenario a tuned optimizer can match us; static rules are in the table. The
  org's edge is unstructured events, authority and veto dynamics, and the explanation a human needs before
  approving. An optimizer cannot tell you what its own decision triggers next.
- Realistic? Synthetic data shaped on a real site, hourly, pool-level; agents rehearse plausible positions,
  they don't predict people. We claim the mechanism and the comparisons, not real-world savings.
- Cheating with future data? No. What-ifs use a forecast from visible history and announced events. Only
  execution uses the held-out orders.
- Where is the harness? The org twin plus the engine: it builds each agent's context, routes the meeting,
  gives tools, enforces authority in code. The floor twin is the environment. NemoClaw's Scenario Designer is a
  meta-harness above it that chooses what Gemba runs.
- Recursive? The coach rewrites the playbook, each lesson is verified by a counterfactual fork, and a change
  is kept only if it improves unseen scenarios (rule; today's result as per the gate row).
- Why Super for everything? Ultra isn't in the Crusoe catalog today; the tier map is config, Nano and Ultra
  drop in.
