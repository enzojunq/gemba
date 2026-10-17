# Scenario Designer on NemoClaw / OpenShell

Files the sandbox needs (design in `META_HARNESS.md`):
- `skills/gemba/SKILL.md` + `scenario_schema.md`: copy into the sandbox workspace at `<workspace>/skills/gemba/`.
- `openshell-policy.yaml`: network + filesystem policy; apply with `openshell policy set`.

Host side, in this order:
1. `echo "$(ipconfig getifaddr en0) gemba-console.lan" | sudo tee -a /etc/hosts` (the Mac's LAN IP changes with the network; redo this after switching Wi-Fi).
2. `GEMBA_API_TOKEN=<token> uv run uvicorn console.server:app --host 0.0.0.0 --port 8000`
3. In the sandbox env: `GEMBA_URL=http://gemba-console.lan:8000`, `GEMBA_TOKEN=<token>`.
4. `nemoclaw launch gemba-designer`, then ask: "rehearse a peak week with 20% absences on nights, static policy".

Fallback with no sandbox: the console's Scenario dialog has a "Describe in plain words" box that calls
`POST /scenarios/describe` (Nano, B's `org/scenario_designer.py`) and fills the same form.
