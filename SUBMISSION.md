# Submission kit

## 1-minute video script, with the page for each beat

Record on `make demo` (recording `bt-cb97aeb0`) except where marked live (`make console`). ~150 words spoken.

| t | Say | Show |
|---|---|---|
| 0:00 | "This is Gemba. It simulates a warehouse and the team that runs it. The floor is a digital twin. The team is a set of AI agents with real jobs: operations, safety, quality, a shift lead, a program manager, each defending its own KPI." | Floor view, day 61 (3 s), then the meeting room with the nine seats lit |
| 0:12 | "Every morning they hold the standup. Each option comes with a simulator what-if, they argue, the chair decides, and the decision changes the floor. Here a rack got damaged overnight: safety is called in, proposes closing the aisle, the chair keeps the reslot going, and the objection stays on the record." | Meeting room, day 66: Sam's seat lighting up, his option card, Megan's decision banner |
| 0:27 | "That is what a normal simulation can't do. Here a decision has consequences, and the consequences trigger the next decision. You click the chain." | Cascade drawer on the big-bang run `ev-98548726`, day 64: decision → staging full → key-customer escalation → director |
| 0:37 | "So an engineer rehearses a change before doing it for real. Fork any day with a different call and see the other future. Change the company's priorities. Type a surprise in plain words." | Compare: hero vs fork `fk-3b1e986f`, divergence at day 69; then the live console: type "Aisle 12 closed, forklift hit the upright" in the Inbox |
| 0:49 | "Everything stays in the record: options, objections, numbers, reasons. The knowledge doesn't leave with the engineer. And the team learns: lessons proven by forking, kept only if they win on scenarios it has never seen." | Board drawer, then the Learning view: the lesson with "Open the fork", the gate card "promoted" |
| 0:58 | "Today it's a warehouse reslot. Thirty days, unattended, thirty cents. Any floor twin, any org chart. Gemba." | Timeline, days 61–90, done-check lines; title card |

Spoken test: ~185 words, 62–65 s at a calm pace. If it runs long, cut the sentence "Change the company's priorities."

## Project description (paste)

Gemba is a decision twin for operations. Slotting software and digital twins tell a warehouse where things
should go and how the floor behaves; they treat the human decisions as fixed inputs, so they never show what a
decision triggers next, and when the engineer leaves, nobody knows why the plan was shaped the way it was.

Gemba simulates the warehouse and the people who run it. A deterministic hourly floor twin (28 aisles, a
six-zone pick module, about 2,000 orders a day, synthetic data shaped on a real site) is run by an org of LLM
agents defined in config: a program manager who chairs, ops, a safety lead with a veto, a quality lead who can
lock a customer's SKUs, a shift lead who owns the labor truth, a director for escalations. Every simulated
morning they hold a standup: options carry simulator what-ifs on forecast demand, stakeholders take positions
citing that evidence, code scores the options against doing nothing and checks authority, the chair decides,
and typed levers change the floor. A consequence engine turns outcomes into new events with causal links, so a
night of aggressive moves can cause a picker learning dip, mis-picks, a key-customer escalation, Quality in the
room, SKUs locked and the reslot slowed, and you can walk that chain back to the decision.

An engineer uses it to rehearse a change before living it, fork any day with a different call and compare the
two futures, run the same month under different company priorities, inject disruptions in plain words or
describe whole scenarios in a sentence, and keep the org's full decision record. The org also reviews its own
worst days: a coach traces the regret to its decision, proposes a lesson, proves it by forking that day, and
the playbook is promoted only if it beats the previous one on held-out scenarios. Done-checks are written by
the org within code-set bounds and verified every simulated day; drift detectors catch churn, stalls,
prediction error and overspend.

Recorded result: a 30-day reslot month ran unattended on Nemotron 3 Super via Crusoe in 24 minutes for $0.30,
with 20 meetings and 14 specialist hires; the reslot completed on day 79 and the crew beat a static-rules
baseline on every column of the eval table. The Scenario Designer, an OpenClaw agent in a NemoClaw sandbox,
sits above Gemba and can only act through the console API. The reslot is the first scenario; the twin is
behind a protocol and the org chart is config, so the same loop rehearses a peak season, a WMS cutover or a
new customer, and, fed with live floor data and real decisions, becomes the standup's copilot.

## Form answers

- **Problem statement:** **Version N+1.** The 16:36 gate promoted playbook v1: under the strict acceptance
  rule (fork applied the alternative, delta > 0.01 over 5 days, no new hard-limit breach, no LLM fallback in
  the window) the retro accepted 1 lesson of 6 candidates; crew v1 then beat crew v0 on the three held-out
  scenarios (two event kinds never trained on): mean score 0.4794 → 0.4800, violations 18.67 → 18.00, zero
  fallbacks on either side. Say the margin out loud: a promotion by the rule, on 3 scenarios × 1 seed, with
  the learning pass on DeepSeek-V4-Pro (the recorded heroes are Nemotron). Done Means Done is the fallback
  story and is fully built (org-written checks verified daily, drift detectors, 30 days unattended); Shared
  Brain is the supporting one.
- **Best Use of Nemotron:** Yes. Every agent call of the recorded runs is Nemotron 3 Super on Crusoe: strict
  JSON-schema output for every role, the reasoning toggle on only for the chair's decision, the director, the
  threshold pick and the retro coach, a fixed prompt order for the KV cache, 209 calls and $0.30 for a 30-day
  month. Ultra was not in the catalog; the tier map is config.
- **Best Use of NemoClaw & OpenShell:** Yes. The Scenario Designer runs as an OpenClaw agent in a NemoClaw
  sandbox (OpenShell policy: egress to Crusoe and the console only; the console reachable through the gateway
  host, bearer token; it can create scenarios and start runs, cannot fork, lever or touch the floor).
- **NVIDIA tools:** Yes: Nemotron 3 Super (and Nano in the smoke tests), NemoClaw, OpenShell, OpenClaw.
- **You.com tools:** No.
- **Crusoe Managed Inference, how we used it:** the only inference endpoint for the project, through the
  OpenAI-compatible API. Nemotron 3 Super for all org roles (chair, stakeholders, specialists, coach,
  event interpreter, scenario designer), with `response_format: json_schema` strict mode, the Nemotron
  `chat_template_kwargs.enable_thinking` toggle, temperature 0 and a local response cache keyed on the
  request so forks and replays are exact and free; `prompt_tokens_details.cached_tokens` read for cost. Also
  DeepSeek-V4-Pro from the same catalog for a second, faster learning pass. Roughly 1,700 calls over the day.
- **Feedback on Crusoe:** strict JSON-schema mode worked first time with Nemotron and DeepSeek, latency was
  good (Super 1.7 s plain, 12–20 s with reasoning; DeepSeek-V4-Pro about 1 s), and the OpenAI compatibility
  meant zero SDK work. Three things cost us time: the request cap (`429 max_requests_limit (orchestrator)`)
  is opaque, shared per key across machines and sometimes hit at 3 concurrent requests, with no `Retry-After`
  and no visible quota in the console; Nemotron 3 Ultra was absent from the catalog and the Nano id differed
  from the launch blog; Super pricing was not shown next to the model. A per-key concurrency readout and a
  documented limit would have saved an hour.
- **Feedback on partner products:** NemoClaw/OpenShell: the sandbox and skill model are the right shape for
  agents that touch operations, and the OpenClaw skill format is simple. The Windows path (WSL2 + Docker
  Desktop + a 2.4 GB image) is heavy for a hackathon day, loopback is blocked so a local console needs a
  gateway host alias, and the network-policy YAML keys are not fully documented; on macOS it installed and
  ran the same afternoon. Nemotron: the reasoning toggle returns thinking in `reasoning_content` and counts
  against `max_tokens`, which bit our 20 s timeout until we raised it for reasoning calls. Claude Code: two
  people each drove coding agents against a written contract, with an append-only comms file between the two
  agents and independent review agents for the loop's integrity and the pitch; that structure is what let a
  two-person team ship both halves in a day.
