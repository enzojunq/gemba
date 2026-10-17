# DEMO.md: the 3-minute demo on the Gemba console, and the 1-minute video

Spec section 22 is the storyboard; this maps it onto the console as built. Before you start:
`make demo` (replays `recordings/hero.db`, badge "Recorded · replaying") or `make console` for a live store.
Dark theme on the projector. Say "recorded run from this afternoon" the first time the badge is on screen.

Hero run id below is `<hero>`; the fork created on stage is `<fork>`.

| Time | On screen (URL or click) | Say |
|---|---|---|
| 0:00–0:20 | `/?run=<hero>&day=61` paused. Floor with 28 aisles, Pick Module, dock. | "I was the analytics engineer on a warehouse reslot. The target layout was easy. Getting there while the warehouse kept shipping was not, and the decisions in the daily standup were the real driver. No simulator models those decisions." |
| 0:20–0:50 | Press play at 16x to day 66. Rack damage closes aisle 7 (hatched). Room: Sam is called in, **VETOED** chip under his seat, stamp over option A, decision title "aisle 7 closed for 5 days; slow night". | "Gemba simulates the warehouse and the people who run it. Every claim cites a simulator run. Safety has a veto, Quality can lock a customer, the director owns priorities." |
| 0:50–1:20 | Day 67: click the red consequence marker on the timeline. Cascade drawer opens with the chain as one sentence: aggressive night → mis-picks on C001 → escalation → Ellen locks C001 → reslot slows. | "Decisions cascade. That is what hardcoded simulations miss: the follow-up decisions never happen in them." |
| 1:20–1:50 | **Fork** button, day 69, preset "pause the reslot 3 days" (the default; "protect the Pick Module" does not diverge yet, COMMS B-2). New run streams ("Live"). **Compare** → verdict sentence at the top, synced timelines, first divergent decision highlighted. | "Fork any day with a different decision and watch the other future, with the price of each trade-off." |
| 1:50–2:20 | **Scenario** button → type "peak week with 20% absences on the night crew, rack damage in aisle 9 on day 64" → Draft from words → Save & run (policy static for speed). Or: type a disruption in the **Inbox**. | "Any disruption, any month, in plain words. The sandboxed designer can only change inputs; inside a run the org still decides through typed levers." |
| 2:20–2:45 | B's chart: v0 vs v1 on the unseen test scenarios, static rules as the reference line. Eval table (do-nothing, big bang, static, crew). | "After each run the org reviews its worst cascades, proves a better move by forking, and keeps the lesson only if it wins on scenarios it has never seen." |
| 2:45–3:00 | Platform slide. | "Reslotting is the first pack. Swap the twin and the org chart and it rehearses any operational change. Wargaming for operations." |

## Numbers you can say (seed 1, days 61–90, measured; see DECISIONS.md)

- Do-nothing: on-time 95.2% on normal days, Pick Module zones 29% over capacity, forklift pickers 81% busy.
- Static rules (200 slot moves a night): on-time 96.5%, lines per labor hour +22%, Pick Module peak from 1.29 to 0.77, reslot done by day 81, 7 jam hours in the month, all on event days.
- Naive big bang (everything in two nights with temps): 18 jam hours, mis-picks 1.2% and 1.6% on two days, staging 100% for three nights, on-time 75% on day 63, and the full cascade staging congestion → customer escalation → director escalation.
- Org run: 10 days for about $0.10 in Nemotron calls; a 30-day run about $0.18, 14 minutes wall.
- Twin: 75 ms per simulated day, a 3-day what-if in half a second, 2.1 million order lines, 8 built-in traps verified by `make data`.

## Q&A pointers (spec section 23, with what we built)

- "Where's the harness?" The org twin plus the run engine: it builds each agent's context, routes the meeting, gives tools (what-ifs), turns answers into typed levers, enforces authority in code. The floor twin is the deterministic environment. The Scenario Designer is a meta-harness above it (`console/designer/`, `META_HARNESS.md`).
- "Isn't the what-if cheating?" No: forecasts resample visible history and announced events; only execution reads the held-out orders. A test asserts `whatif` never calls `actual_demand`.
- "Why does the twin matter?" Because decisions are endogenous: an aggressive night raises mis-picks, which triggers the customer escalation, which brings Quality in. Today's bug of the day proves it: a double-counted mover made every reslot look unsafe and the whole org paused for a week until the what-if was fixed.
- "Is it realistic?" Synthetic data shaped on a real site; we claim the mechanism and the comparisons, not savings.

## 1-minute video shot list (screen recording, no narration needed; captions below)

1. 0–8 s: floor at 16x, day 61–66, caption "A warehouse that keeps shipping while 1,200 SKUs move at night."
2. 8–20 s: day 66 standup, Sam's veto, decision title. Caption "Nine AI roles hold the standup. Safety can veto."
3. 20–32 s: cascade drawer sentence. Caption "Decisions cascade. Every link is traceable."
4. 32–44 s: fork at 69 + compare verdict. Caption "Fork any day. Compare the two futures."
5. 44–52 s: Scenario dialog, Draft from words. Caption "Any disruption, in plain words."
6. 52–60 s: v0 vs v1 chart. Caption "The org rewrites its playbook and proves it on unseen scenarios."
