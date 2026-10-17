# Scenario Designer on NemoClaw / OpenShell

Files the sandbox needs (design in `META_HARNESS.md`):
- `skills/gemba/SKILL.md` + `scenario_schema.md`: uploaded to `/sandbox/.openclaw/workspace/skills/gemba/`.
- `skills/gemba/token`: the bearer token, one line, gitignored; uploaded on its own (the uploader skips ignored files).
- `gemba-console-preset.yaml`: NemoClaw custom network preset. The sandbox reaches the Mac through OpenShell's
  bridge `host.openshell.internal:8000`, for the designer routes only (no `/fork`, no `/inbox`; curl only).
  No `/etc/hosts` edit and no LAN IP needed. (`openshell-policy.yaml` is the earlier raw-OpenShell draft.)

Setup (done once on A's Mac, sandbox `gemba`, OpenClaw agent, Nemotron Super via Crusoe):
1. `export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"` (NemoClaw shells out to `docker`).
2. `[ -f ~/.gemba_token ] || openssl rand -hex 16 > ~/.gemba_token`
3. `nemoclaw gemba policy add --from-file console/designer/gemba-console-preset.yaml --yes`
4. `nemoclaw gemba upload console/designer/skills/gemba /sandbox/.openclaw/workspace/skills/`
   then `nemoclaw gemba upload ~/.gemba_token /sandbox/.openclaw/workspace/skills/gemba/token`
5. Console reachable from the bridge, token on for non-local writes (the browser on this Mac needs none):
   `GEMBA_API_TOKEN=$(cat ~/.gemba_token) RUN_STORE_URL=sqlite:///runs.db uv run uvicorn console.server:app --host 0.0.0.0 --port 8000`
6. Check from inside: `nemoclaw gemba exec -- curl -s http://host.openshell.internal:8000/health` (200; a fork POST gets 403).
7. `nemoclaw launch gemba` (or the dashboard at http://127.0.0.1:18789/) and ask:
   "Using the gemba skill, rehearse a peak week with 20% absences on nights and rack damage in aisle 9 on day 64, static policy."

Fallback with no sandbox: the console's Scenario dialog "Draft from words" calls `POST /scenarios/describe`
(B's `org/scenario_designer.py`) and fills the same form.
