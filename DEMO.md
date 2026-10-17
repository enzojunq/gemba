# DEMO.md: the 3-minute demo on the Gemba console, and the 1-minute video

Spec section 22 is the storyboard; this maps it onto the console as built. Before you start:
`make demo` (replays `recordings/hero.db`, badge "Recorded · replaying") or `make console` for a live store.
Dark theme on the projector. Say "recorded run from this afternoon" the first time the badge is on screen.

Recording (`make demo`, `recordings/hero.db`): customer-first hero `bt-cb97aeb0` (opens by default), efficiency-first hero `bt-449cd50d`, day-69 "pause 3 days" fork `fk-3b1e986f`. Pick any of them from the run menu (click the run name, top left). Below, `<hero>` = `bt-cb97aeb0`, `<fork>` = `fk-3b1e986f`.

| Time | On screen (URL or click) | Say |
|---|---|---|
| 0:00–0:20 | `/?run=<hero>&day=61` paused. Floor with 28 aisles, Pick Module, dock. | "I was the analytics engineer on a warehouse reslot. The target layout was easy. Getting there while the warehouse kept shipping was not, and the decisions in the daily standup were the real driver. No simulator models those decisions." |
| 0:20–0:50 | Press play at 16x to day 66. Rack damage hits aisle 7. Room: Sam proposes closing aisle 7; it scores −0.05 against doing nothing, Megan keeps the 200-move reslot, and his objection stays on the record. | "Gemba simulates the warehouse and the people who run it. Every claim cites a simulator run. Safety can object and veto, Quality can lock a customer, the director owns priorities." |
| 0:50–1:20 | Run menu → the big-bang run `ev-98548726` (or `/?run=ev-98548726&day=64`), click the red consequence marker on day 64. Cascade drawer: "On day 61 the big-bang plan chose aggressive night: 1200 slot moves tonight; 8 temps → staging buffer at 100% chokes nearby aisles → customer C001 escalated over mis-picks". Second example in the hero: `/?run=bt-cb97aeb0&day=73` (on-time miss → director called in → Dana decides). | "Decisions cascade. That is what hardcoded simulations miss: the follow-up decisions never happen in them." |
| 1:20–1:50 | **Fork** button, day 69, preset "pause the reslot 3 days" (the default; "protect the Pick Module" does not diverge yet, COMMS B-2). New run streams ("Live"). **Compare** → verdict sentence at the top, synced timelines, first divergent decision highlighted. | "Fork any day with a different decision and watch the other future, with the price of each trade-off." |
| 1:50–2:20 | **Scenario** button → type "peak week with 20% absences on the night crew, rack damage in aisle 9 on day 64" → Draft from words → Save & run (policy static for speed). Or: type a disruption in the **Inbox**. | "Any disruption, any month, in plain words. The sandboxed designer can only change inputs; inside a run the org still decides through typed levers." |
| 2:20–2:45 | B's chart: v0 vs v1 on the unseen test scenarios, static rules as the reference line. Eval table (do-nothing, big bang, static, crew). | "After each run the org reviews its worst cascades, proves a better move by forking, and keeps the lesson only if it wins on scenarios it has never seen." |
| 2:45–3:00 | Platform slide. | "Reslotting is the first pack. Swap the twin and the org chart and it rehearses any operational change. Wargaming for operations." |

## Numbers you can say (seed 1, days 61–90, measured; see DECISIONS.md)

- Do-nothing: on-time 95.2% on normal days, Pick Module zones 29% over capacity, forklift pickers 81% busy.
- Static rules (200 slot moves a night): on-time 96.5%, lines per labor hour +22%, Pick Module peak from 1.29 to 0.77, reslot done by day 81, 4 jam hours in the month on the final code (recorded `ev-6829bcf3`; 7 in the earlier calibration), all on event days.
- Naive big bang (everything in two nights with temps), final code (recorded `ev-a960a7b5`): 18 jam hours over the month, 8 of them on day 63 (on-time 75% that day), mis-picks 1.2% and 1.6% on two days, staging 100% for three nights, and the cascade staging congestion → customer escalation → director escalation. (The cascade shot uses `ev-98548726`, an earlier big-bang eval run whose chain reads as one sentence; do not quote its 179 jam hours, it predates the dispatch cap.)
- The recorded heroes (`bt-cb97aeb0` customer-first, `bt-449cd50d` efficiency-first): reslot 100% on day 79, on-time 95.5% / 95.6%, lines per labor hour +20.7% / +20.8%, 0 vetoes; customer-first beats static rules on score 0.573 vs 0.559 with 19 vs 21 violations (B, PROGRESS_B 15:35).
- Org run: 10 days for about $0.10 in Nemotron calls; a 30-day run about $0.18, 14 minutes wall.
- Twin: 75 ms per simulated day, a 3-day what-if in half a second, 2.1 million order lines, 8 built-in traps verified by `make data`.

## Q&A pointers (spec section 23, with what we built)

- "Where's the harness?" The org twin plus the run engine: it builds each agent's context, routes the meeting, gives tools (what-ifs), turns answers into typed levers, enforces authority in code. The floor twin is the deterministic environment. The Scenario Designer is a meta-harness above it (`console/designer/`, `META_HARNESS.md`).
- "Isn't the what-if cheating?" No: forecasts resample visible history and announced events; only execution reads the held-out orders. A test asserts `whatif` never calls `actual_demand`.
- "Why does the twin matter?" Because decisions are endogenous: an aggressive night raises mis-picks, which triggers the customer escalation, which brings Quality in. Today's bug of the day proves it: a double-counted mover made every reslot look unsafe and the whole org paused for a week until the what-if was fixed.
- "Is it realistic?" Synthetic data shaped on a real site; we claim the mechanism and the comparisons, not savings.

## 1-minute video shot list (screen recording, no narration needed; captions below)

1. 0–8 s: floor at 16x, day 61–66, caption "A warehouse that keeps shipping while 1,200 SKUs move at night."
2. 8–20 s: day 66 standup: Sam proposes closing aisle 7, Megan keeps the reslot, decision title. Caption "Nine AI roles hold the standup, with simulator evidence."
3. 20–32 s: big-bang run `ev-98548726` day 64, cascade drawer sentence. Caption "Decisions cascade. Every link is traceable."
4. 32–44 s: fork at 69 + compare verdict. Caption "Fork any day. Compare the two futures."
5. 44–52 s: Scenario dialog, Draft from words. Caption "Any disruption, in plain words."
6. 52–60 s: v0 vs v1 chart. Caption "The org rewrites its playbook and proves it on unseen scenarios."

## Video takes on the recording (B's PITCH_1MIN.md, one line per shot)

Console: `make demo`, browser ~1440x900 (B's spoken script is `PITCH.md`). Before recording, light the header chip "NemoClaw connected" with one real call from the sandbox (it writes `console/designer/sandbox_seen.json`, per machine): `nemoclaw gemba exec -- curl -s -H "X-Gemba-Client: nemoclaw" http://host.openshell.internal:8000/health` (Docker's bin on PATH). The run menu names the model behind each run (Nemotron 3 Super on Crusoe) and tags runs the sandbox started "via NemoClaw". `make demo` replays the recording and refuses live work with a readable message (new run, fork, inbox: "This console is replaying a recording… start the live console"). Scenario → Draft from words → Save works there (it writes a YAML file); "Save & run", the Inbox and a live Fork need `make console` (live store, your Crusoe key). Do those beats on `make console` with the 10-day org run `bt-6b1c573e`, or skip them., dark theme, speed 4x, pause on each beat. Every link opens paused with `&autoplay=0`.

| Pitch line | Open | Do |
|---|---|---|
| "Simulators model the floor. Nobody models the meeting." | `/?run=bt-cb97aeb0&day=61` | press play at 16x for a few seconds: slot moves at night, pickers by day |
| "Every morning they hold a standup... act through typed levers." | `/?run=bt-cb97aeb0&day=66&autoplay=0` | play 4x: rack damage, Sam proposes closing aisle 7, Megan keeps the reslot, decision banner lands |
| "...Quality in the room, SKUs locked..." | `/?run=bt-cb97aeb0&day=71&autoplay=0` | Ellen's LOCKED chip; decision "C001's SKUs locked in place for 3 days" |
| "An aggressive night of moves causes ... mis-picks, a key-customer escalation" | `/?run=ev-98548726&day=64&autoplay=0` | click the red marker on day 64: aggressive night → staging at 100% → C001 escalated |
| "You can see the whole chain" | `/?run=bt-cb97aeb0&day=73&autoplay=0` | click the red marker over day 72-73: on-time miss → director called in → Dana decides |
| "Fork any day with a different call" | `/?compare=bt-cb97aeb0,fk-3b1e986f` | verdict sentence, synced timelines (complete after B's second export) |
| "customer-first versus efficiency-first" | `/?compare=bt-cb97aeb0,bt-449cd50d` | first divergence day 64: customer-first pauses, efficiency-first moves 200 |
| "Type a disruption in plain words" | any run | Scenario → Draft from words (or the Inbox box) |
| "keeps the lesson only if it wins on scenarios it has never seen" | header **Learning** | lessons + their forks; v0 vs v1 chart fills after the gate (~16:00) |
