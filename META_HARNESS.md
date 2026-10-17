# META_HARNESS.md: the Scenario Designer (spec 16), feasibility and wiring plan

Written 13:25 Oct 17 against `main` @ 3da943f. Spec refs: 4 (NemoClaw row), 5 item 12, 6 (harness view), 14, 16, 20 (cut order), 22 (1:50-2:20), decision 13.

## 1. What the meta-harness is

In Gemba's harness view, the **org twin plus the run engine is the harness** (it builds context, gives tools, turns answers into typed levers, enforces authority, logs and caches), and the **floor twin is the environment** (deterministic, no LLM). The **Scenario Designer is a meta-harness one level up**: an agent that decides *what Gemba runs* (a scenario YAML, a profile, a playbook version, a policy) and *reads what happened* (day bundles, cascade traces), then explains it in plain language. It only touches Gemba's **inputs** and the **public console API**. It never issues levers, never edits a decision inside a run, never reads `FloorState`, never writes `runs.db`. Inside a run, the org still decides through the typed levers and the authority matrix; the designer just picks the rehearsal.

## 2. Feasibility verdict for today (NemoClaw path): **NO-GO**

Do not spend the 14:50-15:30 slot on NemoClaw. Blockers, in order of severity:

1. **Nothing is installed, and the install is a multi-GB, multi-step Linux path.** Local probe (13:25):
   - `where nemoclaw` / `where openshell` / `where openclaw`: not found (all three).
   - `docker --version`: `Docker version 29.6.2` (CLI only). `docker info`: **daemon not running** (`dockerDesktopLinuxEngine` pipe missing).
   - `wsl --status` / `wsl -l -v`: default distro `Ubuntu`, WSL 2, **Stopped**; `docker-desktop` distro Stopped. Not `Ubuntu-24.04` (the docs' default; `-DistroName Ubuntu` is accepted).
   - Host: 15.3 GB RAM (min 8, recommended 16), ~100 GB free on C:, Windows Node v22.16 (irrelevant; NemoClaw runs inside WSL and needs Node >= 22.19 there).
   - Windows is supported **only via WSL2 + Docker Desktop (WSL backend)**, all NemoClaw commands run inside WSL, not PowerShell; Docker Desktop must be running with WSL integration enabled for the distro ([Windows preparation](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/get-started/additional-setup/windows-preparation.md)). Platform matrix lists Windows WSL2 as "Tested with limitations"; native Windows is not listed ([platform support](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/reference/platform-support.md)).
   - Sandbox image is ~2.4 GB compressed; the image push runs the container runtime, k3s and the OpenShell gateway together, OOM risk under 8 GB ([prerequisites](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/get-started/prerequisites.md)). "Sandbox creation can take a few minutes on first run" ([quickstart](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/get-started/quickstart.md)). On hackathon Wi-Fi, download + k3s + onboarding alone plausibly eats most of the 40 minutes before any Gemba wiring.
2. **Reaching the Gemba console from inside the sandbox is the hard part, not the agent.** The console runs on the Windows host (`uvicorn ... --port 8000`). OpenShell always blocks loopback and link-local, even with `allowed_ips`; private RFC 1918 addresses need an exact hostname or `allowed_ips` ([OpenShell network rules](https://docs.nvidia.com/openshell/latest/how-it-works/policies/network-rules), [security best practices](https://docs.nvidia.com/openshell/latest/security/best-practices)). NemoClaw advises exposing host services on a host IP the gateway can reach and warns `host.docker.internal` "should not be relied on" ([customize network policy](https://docs.nvidia.com/nemoclaw/user-guide/deepagents/network-policy/customize-network-policy)). Windows host -> WSL2 VM -> Docker Desktop VM -> k3s pod is three NAT hops plus Windows Firewall; debugging that is unbounded.
3. **Credential plumbing is fine for Crusoe, awkward for the console token.** Crusoe works as "Other OpenAI-compatible endpoint" with `COMPATIBLE_API_KEY` (`NEMOCLAW_PROVIDER=custom` non-interactively), and OpenShell keeps the provider credential outside the sandbox ([quickstart](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/get-started/quickstart.md), [how it works](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/about/how-it-works.md)). But "a network rule that allows traffic to a destination does not allow OpenShell to send provider credentials there" ([network rules](https://docs.nvidia.com/openshell/latest/how-it-works/policies/network-rules)); boundary injection for a custom bearer needs a provider profile / credential binding (or managed MCP over **authenticated HTTPS** Streamable HTTP, [how it works](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/about/how-it-works.md)). Our console is plain HTTP. Realistic shortcut today: put the token in the workspace, which defeats the "credentials at the boundary" pitch line.
4. **The custom tool itself is easy.** OpenClaw skills are a folder with `SKILL.md` (YAML frontmatter `name`, `description`, optional `metadata.openclaw.requires.{bins,env}`), loaded from `<workspace>/skills` first ([OpenClaw skills](https://docs.openclaw.ai/tools/skills)). The skill tells the agent to call the API with `curl`; the binary must be listed in the policy entry's `binaries` (the network rule is per-binary). Note: skill `env`/`apiKey` injection applies to the host process, not the sandbox ([OpenClaw skills](https://docs.openclaw.ai/tools/skills)).

Net: blockers 1 and 2 each could take the whole slot alone; the spec's cut order already puts this first to cut. **Verdict: NO-GO for a live demo; describe it as the production design (section 3) and demo plan B.** Revisit only if, at 15:00, someone has a spare laptop that already has NemoClaw running (then it is a 20-minute wiring job using section 3).

## 3. Wiring plan A (NemoClaw / OpenClaw), for the pitch and for "after the hackathon"

**Install (WSL Ubuntu, Docker Desktop running, WSL integration on):**
```bash
curl -fsSL https://www.nvidia.com/nemoclaw.sh | NEMOCLAW_NON_INTERACTIVE=1 NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 \
  NEMOCLAW_AGENT=openclaw NEMOCLAW_PROVIDER=custom COMPATIBLE_API_KEY=$CRUSOE_API_KEY \
  NEMOCLAW_POLICY_TIER=restricted NEMOCLAW_SANDBOX_NAME=gemba-designer bash
# endpoint/model variables for `custom` are not named in the quickstart; use the interactive wizard if they fail
nemoclaw gemba-designer status ; nemoclaw launch gemba-designer
```
Source: [quickstart](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/get-started/quickstart.md), tiers in [network policies](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/reference/network-policies.md).

**Skill file** `<workspace>/skills/gemba/SKILL.md` (format per [OpenClaw skills](https://docs.openclaw.ai/tools/skills)):
```markdown
---
name: gemba
description: Design warehouse rehearsal scenarios for Gemba, start runs, and explain the results via the Gemba console API.
metadata: { "openclaw": { "requires": { "bins": ["curl"], "env": ["GEMBA_URL"] } } }
---
You are Gemba's Scenario Designer. You change only Gemba's INPUTS (scenario, profile, playbook, policy).
You never issue levers or edit decisions; the org inside the run decides.

API (JSON; send header `Authorization: Bearer $GEMBA_TOKEN` on POSTs):
1. POST $GEMBA_URL/scenarios        body {"yaml": "<scenario yaml>"}  -> {ok, id, path, errors[]}
   On ok=false, fix every error and POST again (max 3 tries).
2. POST $GEMBA_URL/runs             body {"scenario": id, "profile": "customer_first"|"efficiency_first",
                                          "playbook": "v0"|"v1", "policy": "org"}  -> {run_id}
3. GET  $GEMBA_URL/runs/{run_id}/status   poll every 15 s until done|failed
4. GET  $GEMBA_URL/runs/{run_id}          header, KPIs, decisions, events
5. GET  $GEMBA_URL/runs/{run_id}/day/{d}  one day: meeting, levers, KPIs, events
6. GET  $GEMBA_URL/cascade/{run_id}/{node_id}  why something happened (decision -> consequence -> event)

Scenario YAML: see {baseDir}/scenario_schema.md (id, seed, days [start,end] within 61-90,
events [{kind, day, hour, args}], perturbation, overrides). Prefer 10-day windows.
Finish with: what you ran, the 3 biggest KPI moves, and one cascade chain with node ids.
```
Plus `{baseDir}/scenario_schema.md`: generated from `contracts.EVENT_ARGS` and `scenarios/loader.py`'s rules (one line per kind with its args), and one example (`scenarios/test_1.yaml`).

**Sandbox policy** (OpenShell schema keys: `version`, `filesystem_policy`, `landlock`, `process`, `network_policies`; filesystem is static, network hot-reloadable; [policy schema](https://docs.nvidia.com/openshell/latest/reference/policy-schema), entry shape per [network rules](https://docs.nvidia.com/openshell/latest/how-it-works/policies/network-rules)):
```yaml
version: 1
filesystem_policy:
  include_workdir: true
  read_only: [/usr, /lib, /etc, /proc]
  read_write: [/sandbox, /tmp]          # its own workspace only; Gemba repo is not mounted
network_policies:
  gemba_console:
    endpoints:
      - host: gemba-console.lan          # exact hostname mapped to the host's LAN IP, NOT localhost
        port: 8000
        protocol: rest
        enforcement: enforce
        rules:                           # only the designer endpoints; no /fork, no /inbox
          - {method: POST, path: /scenarios}
          - {method: POST, path: /runs}
          - {method: GET,  path: "/runs/**"}
          - {method: GET,  path: "/cascade/**"}
        allowed_ips: [192.168.0.0/16]
    binaries: [{path: /usr/bin/curl}]
  # Crusoe inference goes through the NemoClaw managed provider (credential held by OpenShell);
  # no direct egress entry to api.inference.crusoecloud.com is needed for the agent itself.
```
(The `rules` method/path syntax is paraphrased from the docs; verify exact keys with `nemoclaw gemba-designer policy get`.) Apply live: `openshell policy set --policy gemba.yaml --wait gemba-designer` ([network policies](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/reference/network-policies.md)).

**Console auth (minimal change, A, ~5 min):** env `GEMBA_API_TOKEN`; a FastAPI dependency on the mutating designer endpoints (`POST /scenarios`, `POST /scenarios/describe`, `POST /runs`) that, **only when the env var is set**, requires `Authorization: Bearer <token>` and returns 401 otherwise. Unset = today's behaviour, so `make demo` and the console UI are unaffected. In production the token is an OpenShell credential binding injected at the boundary; today it would be an env var in the sandbox.

**Demo flow:** judge: "rehearse a peak week with 20% absences on nights" -> agent writes YAML (`demand_surge` family seasonal x3 days 63-65 + `labor_shortage` pool night_move pct 20 days 5 from 62) -> `POST /scenarios` (if rejected, fixes and retries, visible on screen) -> `POST /runs` -> console shows the new run streaming -> agent polls status -> reads `/runs/{id}` and one `/cascade` chain -> 4-line summary. A 10-day crew run is minutes, so on stage use `policy: static` for speed or show a pre-run.

## 4. Wiring plan B (fallback, build first): "Describe a scenario" box

**What exists today:** `POST /scenarios` does **not** exist in `console/server.py` (endpoints: `/runs`, `/runs/{id}`, `/day`, `/stream`, `/inbox`, `/fork`, `/forks/presets`, `/status`, `/compare`, `/cascade`, `/health`). `POST /runs` exists and takes `{scenario, profile, playbook, policy, seed}`. A's `scenarios/loader.validate_scenario(raw) -> list[str]` exists and is the stricter validator (args per kind, aisles, zones, pools, surge targets, overrides). Note `engine/events.load_scenario` validates with `contracts.Scenario` only, so files must pass A's validator at save time.

**Ownership (CONTRACT 1):**
- B: `org/scenario_designer.py` (LLM call + cleaning), `org/prompts/scenario_designer.md`, add `"scenario_designer"` to the `meta_prompt` Literal in `org/roles.py`, `tests/test_scenario_designer.py`.
- A: `console/server.py` endpoints `POST /scenarios` and `POST /scenarios/describe` (thin), the console box in `console/static/`, `scenarios/` writes.

**LLM output schema** (B, in `org/scenario_designer.py`; not a contract type, so no `contract:` commit):
```python
class EventDraft(BaseModel):
    kind: EventKind                      # contracts.EventKind (exogenous kinds only, enforced in code)
    day: int
    hour: int = Field(6, ge=0, le=23)
    args: dict[str, Any] = {}

class ScenarioDraft(BaseModel):
    slug: str                            # short snake_case name, e.g. "peak_week_night_absence"
    summary: str                         # one sentence, shown to the judge
    days: tuple[int, int] = (61, 70)
    events: list[EventDraft] = Field(default_factory=list, max_length=8)
    perturbation: dict[str, Any] = {}    # family_scale_pct, seasonal_group_seed, surge_shift_days
    overrides: dict[str, Any] = {}       # headcounts{shift:{pool:int}}, budgets{legs_per_night:int}
    confidence: float = Field(ge=0, le=1)
```
API: `design_scenario(llm, text: str) -> tuple[dict, list[str], ScenarioDraft]` returns the raw scenario dict, errors, and the draft.

**Prompt outline** (`org/prompts/scenario_designer.md`, system; static so it caches): role ("turn a plain-language rehearsal request into a Gemba scenario; inputs only"); the window rule (days inside 61-90, default 61-70, events inside the window); the kind table generated at import from `contracts.EVENT_ARGS` restricted to the 10 exogenous kinds, with arg types (pool names from `contracts.Pool`, aisle 1-28, zone 1-6, customer `C###` with C001-C003 key, family `F###` or `seasonal`, pct 0-100, positive ints for days/hours/count/n); `demand_surge` needs exactly one of skus|family|customer; overrides and perturbation optional; two worked examples (`scenarios/test_1.yaml` and one with overrides); "if the request is not a scenario, confidence < 0.5". User message: the text. Tier: **Nano**, `json_schema` via `llm.complete("nano", msgs, ScenarioDraft, role="scenario_designer", run_id=None, max_tokens=900)`; cached like every call.

**Validation (code, in order):**
1. `confidence >= 0.5`, else error "not a scenario request".
2. Per event: kind must be exogenous (not a consequence kind such as `aisle_closure`); clean args with `org.interpreter.validate_parse(EventParse(kind, args, confidence=1))` (drops unknown keys, defaults `days=1`, range checks); a `None` result becomes an error naming the event.
3. Clamp/require window: `61 <= start <= end <= 90`; events outside are errors (not silently moved).
4. Build raw dict: `id = slugify(slug)` + `-` + 4-char hash of the text if the file exists (never overwrite `train_*`, `test_*`, `reslot_default`); `seed = 1000 + crc32(text) % 9000` (outside train/test seeds).
5. `scenarios.loader.validate_scenario(raw)` (A's rules), then `contracts.Scenario.model_validate(raw)`.
6. One repair round: if errors, re-ask Nano with the errors appended (cap 2 LLM calls total).

**Endpoints (A, thin):**
- `POST /scenarios {yaml}`: `yaml.safe_load` -> `validate_scenario` -> on ok write `scenarios/<id>.yaml` (refuse protected ids and path separators in `id`); returns `{ok, id, path, errors}`. This is the endpoint the NemoClaw agent would also use.
- `POST /scenarios/describe {text, save: false}`: `org.scenario_designer.design_scenario(LLM(), text)` -> `{ok, yaml, summary, errors}`; with `save: true` it goes through the same path as `POST /scenarios`. The console calls it twice: preview, then confirm.
- The console box (INBOX drawer neighbour): textarea -> "Draft" -> shows YAML + summary + errors -> "Run it" -> `POST /scenarios` (save) -> `POST /runs {scenario: id, profile, playbook, policy}` -> switches the view to the new run's stream.

**Tests:** cached-LLM test for 3 phrasings (peak week + night absences; rack damage aisle 7 day 66; key customer week + PM zone down) asserting the YAML passes `validate_scenario`; unit tests for the cleaning (consequence kind rejected, out-of-window day rejected, protected id refused) with a hand-built `ScenarioDraft` (no LLM).

**Estimate:** B module + prompt + tests 30 min; A endpoints 15 min; console box 15 min; one end-to-end check 10 min. **~60-70 min total, ~35 min each in parallel** (the spec's 20 minutes assumed `POST /scenarios` already existed).

## 5. What the meta-harness adds to the pitch, and what it must not do

Adds:
- **Recursion one level up:** the org improves its playbook (Version N+1); the designer chooses what the org rehearses next. Agents designing tests for agents, with the harness as the referee.
- **Any disruption, any month, in plain words:** not just one inbox message mid-run but a whole calendar, validated against the same typed schema that the floor twin enforces.
- **Blast-radius story** (NemoClaw design): the agent that writes inputs runs sandboxed with egress only to Crusoe and the Gemba API, can only call a whitelisted set of endpoints, and cannot reach the floor except through Gemba's levers. This is the production argument for agents near real WMS systems.

Must NOT:
- Issue levers, call `/fork` with levers, or post to `/inbox` of a running run (that is the org's and the user's channel).
- Touch `runs.db` or `inputs.db` directly, or import `engine/` / `floor/` internals; console API only.
- Read `FloorState` or any twin internals; it reads only the run store via the console's GET endpoints.
- Overwrite the train/test/default scenarios (would leak into the retro gate) or pick seeds that collide with them.

## 6. Recommendation

- **Plan A (NemoClaw): cut now, per spec 20 cut order item 1.** Keep section 3 as the "Best use of NemoClaw/OpenShell" design slide and Q&A answer; do not install during the event. Say on stage: "the hosted version runs this agent in an OpenShell sandbox; here it runs on Nano behind the same endpoint."
- **Plan B: stretch, only if both owners are on or ahead of plan at 15:00** (spec 5: stretch only if ahead at 15:00). It must be done before the 15:30 gate, because 15:30-16:15 is the freeze. Realistic window: **B starts `org/scenario_designer.py` at ~15:00 if the retro gate (phase 5) is already running in the background; A adds `POST /scenarios` + `/describe` + the box after the console gate (14:50).** Hard stop 15:30; if not green end-to-end by then, drop it and keep the inbox as the 1:50-2:20 demo beat (spec 22 already covers that).
- Owners: **B** = LLM module, prompt, tests; **A** = endpoints, file writes, console box, optional bearer-token check.
- If only one person has slack, do **only `POST /scenarios` + the box with a hand-typed YAML** (A, ~20 min, no LLM): still shows "new scenario -> validated -> run" and is the endpoint either designer would use.
