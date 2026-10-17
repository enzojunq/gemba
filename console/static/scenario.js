// Scenario builder dialog: tweak scenarios inside the platform. A tool, not a wizard.
// Fields are generated from the contract (GET /scenarios/meta); the plain-language preview of each
// event comes from the server so it matches the console's own wording.

const $ = (s, r = document) => r.querySelector(s);

export class ScenarioDialog {
  constructor(dlg, { onRun, toast }) {
    this.dlg = dlg;
    this.onRun = onRun;
    this.toast = toast;
    this.meta = null;
    this.events = [];
    this.headcounts = {};
    this.built = false;
  }

  async open(defaults = {}) {
    if (!this.meta) this.meta = await fetch("/scenarios/meta").then((r) => r.json());
    if (!this.built) { this.build(); this.built = true; }
    if (!this.events.length) this.reset(defaults);
    await this.refreshList();
    this.dlg.showModal();
  }

  reset(defaults = {}) {
    $("#sc-id", this.dlg).value = "";
    $("#sc-seed", this.dlg).value = 1000 + Math.floor(Math.random() * 9000);
    $("#sc-d0", this.dlg).value = 61; $("#sc-d1", this.dlg).value = 90;
    $("#sc-p-scale", this.dlg).value = ""; $("#sc-p-seed", this.dlg).value = ""; $("#sc-p-shift", this.dlg).value = "";
    this.headcounts = JSON.parse(JSON.stringify(this.meta.headcounts));
    this.events = [];
    this.renderHeadcounts();
    this.renderEvents();
    this.setErrors([]);
    $("#sc-profile", this.dlg).value = defaults.profile && this.meta.profiles.includes(defaults.profile) ? defaults.profile : this.meta.profiles[0];
  }

  build() {
    const m = this.meta;
    $("#sc-profile", this.dlg).innerHTML = m.profiles.map((p) => `<option value="${esc(p)}">${esc(p.replace(/_/g, "-"))}</option>`).join("");
    $("#sc-add", this.dlg).addEventListener("click", () => { this.events.push({ kind: m.kinds[0], day: +$("#sc-d0", this.dlg).value || 61, hour: 6, args: {} }); this.renderEvents(); });
    $("#sc-load", this.dlg).addEventListener("change", async (e) => { if (e.target.value) await this.load(e.target.value); });
    $("#sc-new", this.dlg).addEventListener("click", () => this.reset());
    $("#sc-cancel", this.dlg).addEventListener("click", () => this.dlg.close());
    $("#sc-save", this.dlg).addEventListener("click", () => this.save(false));
    $("#sc-run", this.dlg).addEventListener("click", () => this.save(true));
    $("#sc-describe-go", this.dlg).addEventListener("click", () => this.describe());
    for (const id of ["sc-id", "sc-seed", "sc-d0", "sc-d1", "sc-p-scale", "sc-p-seed", "sc-p-shift"]) $(`#${id}`, this.dlg).addEventListener("input", () => this.renderYaml());
    $("#sc-id", this.dlg).addEventListener("input", (e) => { e.target.value = e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "_"); this.renderYaml(); });
    if (m.auth_required) $("#sc-auth", this.dlg).hidden = false;
  }

  async refreshList() {
    const j = await fetch("/scenarios").then((r) => r.json());
    const sel = $("#sc-load", this.dlg);
    sel.innerHTML = `<option value="">Load an existing scenario…</option>` + j.scenarios.map((s) =>
      `<option value="${esc(s.id)}" title="${esc(s.summary || "")}">${esc(s.id)} · days ${(s.days || []).join("–")} · ${s.n_events} events${s.protected ? " · protected" : ""}</option>`).join("");
  }

  async load(id) {
    const j = await fetch(`/scenarios/${encodeURIComponent(id)}`).then((r) => r.json());
    const raw = j.scenario || {};
    $("#sc-id", this.dlg).value = j.protected ? `${id}_tweak` : id;
    $("#sc-seed", this.dlg).value = raw.seed ?? 1;
    $("#sc-d0", this.dlg).value = (raw.days || [61, 90])[0]; $("#sc-d1", this.dlg).value = (raw.days || [61, 90])[1];
    const p = raw.perturbation || {};
    $("#sc-p-scale", this.dlg).value = p.family_scale_pct ?? ""; $("#sc-p-seed", this.dlg).value = p.seasonal_group_seed ?? ""; $("#sc-p-shift", this.dlg).value = p.surge_shift_days ?? "";
    this.headcounts = JSON.parse(JSON.stringify(this.meta.headcounts));
    for (const [shift, pools] of Object.entries(((raw.overrides || {}).headcounts) || {})) for (const [pool, n] of Object.entries(pools || {})) if (this.headcounts[shift]) this.headcounts[shift][pool] = n;
    this.events = (raw.events || []).map((e) => ({ kind: e.kind, day: e.day, hour: e.hour ?? 6, args: { ...(e.args || {}) } }));
    this.renderHeadcounts(); this.renderEvents(); this.setErrors([]);
    if (j.protected) this.toast(`${id} is protected; saved as a copy under a new name`);
  }

  // ---------------------------------------------------------------- events
  renderEvents() {
    const m = this.meta;
    const list = $("#sc-events", this.dlg);
    list.innerHTML = "";
    if (!this.events.length) list.innerHTML = `<p class="sc-empty">No scripted events yet. Add one, load a scenario, or describe one in words above.</p>`;
    this.events.forEach((ev, i) => {
      const row = document.createElement("div");
      row.className = "sc-ev"; row.dataset.i = i;
      const argFields = (m.args[ev.kind] || []).map((a) => this.argField(ev, a)).join("");
      row.innerHTML = `<div class="sc-ev-head">
          <select class="sc-kind" aria-label="event kind">${m.kinds.map((k) => `<option value="${k}" ${k === ev.kind ? "selected" : ""}>${esc(m.labels[k] || k)}</option>`).join("")}</select>
          <label class="sc-f sc-f-s"><span>day</span><input type="number" class="sc-day" min="1" max="90" value="${ev.day}"></label>
          <label class="sc-f sc-f-s"><span>hour</span><input type="number" class="sc-hour" min="0" max="23" value="${ev.hour}"></label>
          <button type="button" class="btn btn-icon btn-ghost sc-del" aria-label="remove event"><svg class="ic"><use href="#i-x"/></svg></button>
        </div>
        <div class="sc-ev-args">${argFields}</div>
        <div class="sc-ev-phrase" id="sc-phrase-${i}">…</div>
        <div class="sc-ev-err" hidden></div>`;
      row.querySelector(".sc-kind").addEventListener("change", (e) => { ev.kind = e.target.value; ev.args = {}; this.renderEvents(); });
      row.querySelector(".sc-day").addEventListener("input", (e) => { ev.day = +e.target.value; this.renderYaml(); });
      row.querySelector(".sc-hour").addEventListener("input", (e) => { ev.hour = +e.target.value; this.renderYaml(); });
      row.querySelector(".sc-del").addEventListener("click", () => { this.events.splice(i, 1); this.renderEvents(); });
      for (const inp of row.querySelectorAll("[data-arg]")) inp.addEventListener("input", () => { this.readArg(ev, inp); this.renderYaml(); this.phrases(); });
      list.appendChild(row);
    });
    this.renderYaml();
    this.phrases();
  }

  argField(ev, a) {
    const [type, ph] = this.meta.hints[a] || ["text", ""];
    const v = ev.args[a];
    const val = v == null ? "" : Array.isArray(v) ? v.join(", ") : v;
    if (type === "pool") return `<label class="sc-f"><span>${a}</span><select data-arg="${a}" data-type="pool"><option value="">—</option>${this.meta.pools.map((p) => `<option ${p === v ? "selected" : ""}>${p}</option>`).join("")}</select></label>`;
    const inputType = type === "int" || type === "float" ? "number" : "text";
    const step = type === "float" ? ' step="0.1"' : "";
    return `<label class="sc-f"><span>${a.replace(/_/g, " ")}</span><input type="${inputType}"${step} data-arg="${a}" data-type="${type}" placeholder="${esc(ph)}" value="${esc(val)}"></label>`;
  }

  readArg(ev, inp) {
    const a = inp.dataset.arg, t = inp.dataset.type, raw = inp.value.trim();
    if (raw === "") { delete ev.args[a]; return; }
    if (t === "int") ev.args[a] = parseInt(raw, 10);
    else if (t === "float") ev.args[a] = parseFloat(raw);
    else if (t === "csv") ev.args[a] = raw.split(/[,\s]+/).filter(Boolean).map((x) => (/^\d+$/.test(x) ? parseInt(x, 10) : x));
    else ev.args[a] = raw;
  }

  async phrases() {
    if (!this.events.length) return;
    clearTimeout(this._pt);
    this._pt = setTimeout(async () => {
      const j = await fetch("/scenarios/phrases", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ events: this.events }) }).then((r) => r.json()).catch(() => null);
      if (!j) return;
      j.phrases.forEach((p, i) => { const el = $(`#sc-phrase-${i}`, this.dlg); if (el) el.textContent = `${cap(p)} · announced ${this.meta.lead_days[this.events[i].kind] || 0} day(s) ahead`; });
    }, 150);
  }

  // ---------------------------------------------------------------- headcounts
  renderHeadcounts() {
    const m = this.meta;
    const t = $("#sc-headcounts", this.dlg);
    t.innerHTML = `<tr><th>pool</th>${m.shifts.map((s) => `<th>${s}</th>`).join("")}</tr>` + m.pools.map((p) =>
      `<tr><td>${p.replace(/_/g, " ")}</td>${m.shifts.map((s) => `<td><input type="number" min="0" data-shift="${s}" data-pool="${p}" value="${this.headcounts[s]?.[p] ?? 0}" class="${(this.headcounts[s]?.[p] ?? 0) !== (m.headcounts[s]?.[p] ?? 0) ? "is-changed" : ""}"></td>`).join("")}</tr>`).join("");
    for (const inp of t.querySelectorAll("input")) inp.addEventListener("input", (e) => {
      const { shift, pool } = e.target.dataset; this.headcounts[shift][pool] = parseInt(e.target.value || "0", 10);
      e.target.classList.toggle("is-changed", this.headcounts[shift][pool] !== (m.headcounts[shift]?.[pool] ?? 0));
      this.renderYaml();
    });
  }

  // ---------------------------------------------------------------- yaml
  scenario() {
    const num = (id) => { const v = $(`#${id}`, this.dlg).value; return v === "" ? null : Number(v); };
    const overrides = {};
    const hc = {};
    for (const s of this.meta.shifts) for (const p of this.meta.pools) {
      if ((this.headcounts[s]?.[p] ?? 0) !== (this.meta.headcounts[s]?.[p] ?? 0)) { hc[s] = hc[s] || {}; hc[s][p] = this.headcounts[s][p]; }
    }
    if (Object.keys(hc).length) overrides.headcounts = hc;
    const perturbation = {};
    if (num("sc-p-scale") != null) perturbation.family_scale_pct = num("sc-p-scale");
    if (num("sc-p-seed") != null) perturbation.seasonal_group_seed = num("sc-p-seed");
    if (num("sc-p-shift") != null) perturbation.surge_shift_days = num("sc-p-shift");
    return {
      id: $("#sc-id", this.dlg).value.trim(), seed: num("sc-seed") ?? 1,
      days: [num("sc-d0") ?? 61, num("sc-d1") ?? 90],
      events: this.events.map((e) => ({ kind: e.kind, day: e.day, ...(e.hour !== 6 ? { hour: e.hour } : {}), args: e.args })),
      perturbation, overrides,
    };
  }

  renderYaml() {
    $("#sc-yaml", this.dlg).value = toYaml(this.scenario());
  }

  // ---------------------------------------------------------------- save / run / describe
  async save(run) {
    this.setErrors([]);
    const yamlText = $("#sc-yaml", this.dlg).value;
    const r = await fetch("/scenarios", { method: "POST", headers: this.headers(), body: JSON.stringify({ yaml: yamlText }) });
    if (r.status === 401) { this.setErrors(["This server needs an API token (paste it above)."]); return; }
    const j = await r.json();
    if (!j.ok) { this.setErrors(j.errors || ["could not save"]); return; }
    this.toast(`Saved scenarios/${j.id}.yaml`);
    await this.refreshList();
    if (!run) return;
    const rr = await fetch("/runs", { method: "POST", headers: this.headers(), body: JSON.stringify({ scenario: j.id, profile: $("#sc-profile", this.dlg).value, playbook: "v0", policy: $("#sc-policy", this.dlg).value, seed: this.scenario().seed }) });
    const rj = await rr.json();
    if (!rr.ok) { const d = rj.detail; this.setErrors([typeof d === "string" ? d : `${d.error}${d.hint ? ` (${d.hint})` : ""}`]); return; }
    this.dlg.close();
    this.onRun(rj.run_id);
  }

  async describe() {
    const text = $("#sc-describe", this.dlg).value.trim();
    if (!text) return;
    const btn = $("#sc-describe-go", this.dlg); btn.disabled = true; btn.textContent = "Drafting…";
    try {
      const r = await fetch("/scenarios/describe", { method: "POST", headers: this.headers(), body: JSON.stringify({ text }) });
      const j = await r.json();
      if (!r.ok) { const d = j.detail; this.setErrors([typeof d === "string" ? d : `${d.error}${d.hint ? ` (${d.hint})` : ""}`]); return; }
      const raw = j.scenario || {};
      if (raw.id) $("#sc-id", this.dlg).value = raw.id;
      if (raw.seed != null) $("#sc-seed", this.dlg).value = raw.seed;
      if (raw.days) { $("#sc-d0", this.dlg).value = raw.days[0]; $("#sc-d1", this.dlg).value = raw.days[1]; }
      this.events = (raw.events || []).map((e) => ({ kind: e.kind, day: e.day, hour: e.hour ?? 6, args: { ...(e.args || {}) } }));
      const p = raw.perturbation || {};
      $("#sc-p-scale", this.dlg).value = p.family_scale_pct ?? ""; $("#sc-p-seed", this.dlg).value = p.seasonal_group_seed ?? ""; $("#sc-p-shift", this.dlg).value = p.surge_shift_days ?? "";
      for (const [shift, pools] of Object.entries(((raw.overrides || {}).headcounts) || {})) for (const [pool, n] of Object.entries(pools || {})) if (this.headcounts[shift]) this.headcounts[shift][pool] = n;
      this.renderHeadcounts(); this.renderEvents();
      this.setErrors(j.errors || []);
      if (j.summary) this.toast(`${j.summary}${j.confidence != null ? ` (confidence ${Math.round(j.confidence * 100)}%)` : ""}`);
    } finally { btn.disabled = false; btn.textContent = "Draft from words"; }
  }

  headers() {
    const h = { "Content-Type": "application/json" };
    const tok = $("#sc-token", this.dlg).value.trim();
    if (tok) h.Authorization = `Bearer ${tok}`;
    return h;
  }

  // Errors land next to the field they name; anything else goes to the summary list.
  setErrors(errors) {
    for (const el of this.dlg.querySelectorAll(".sc-ev-err")) { el.hidden = true; el.textContent = ""; }
    for (const el of this.dlg.querySelectorAll(".sc-field-err")) el.remove();
    for (const el of this.dlg.querySelectorAll(".is-invalid")) el.classList.remove("is-invalid");
    const rest = [];
    for (const e of errors) {
      let m;
      if ((m = /^event (\d+)/.exec(e))) {
        const row = this.dlg.querySelector(`.sc-ev[data-i="${m[1]}"]`);
        if (row) { const el = row.querySelector(".sc-ev-err"); el.hidden = false; el.textContent = (el.textContent ? el.textContent + " · " : "") + e.replace(/^event \d+ ?(\([^)]*\))?: ?/, ""); row.classList.add("is-invalid"); continue; }
      }
      if (/`id`/.test(e)) { this.fieldErr("sc-id", e.replace("`id` ", "")); continue; }
      if (/`days`/.test(e)) { this.fieldErr("sc-d0", e.replace("`days` ", "")); continue; }
      if (/^overrides\.headcounts/.test(e)) { this.fieldErr("sc-headcounts-wrap", e.replace(/^overrides\.headcounts\.?/, "")); continue; }
      rest.push(e);
    }
    const box = $("#sc-errors", this.dlg);
    box.hidden = !rest.length;
    box.innerHTML = rest.map((e) => `<li>${esc(e)}</li>`).join("");
  }

  fieldErr(id, text) {
    const el = $(`#${id}`, this.dlg); if (!el) return;
    el.classList.add("is-invalid");
    const p = document.createElement("p"); p.className = "sc-field-err"; p.textContent = text;
    (el.closest("label") || el).after(p);
  }
}

// Small YAML writer for the scenario shape (flow style for events, like the shipped files).
function toYaml(sc) {
  const flow = (v) => Array.isArray(v) ? `[${v.map(flow).join(", ")}]` : v && typeof v === "object" ? `{${Object.entries(v).map(([k, x]) => `${k}: ${flow(x)}`).join(", ")}}` : typeof v === "string" ? (/^[A-Za-z0-9_\-]+$/.test(v) ? v : JSON.stringify(v)) : String(v);
  const lines = [`id: ${sc.id || "new_scenario"}`, `seed: ${sc.seed}`, `days: [${sc.days[0]}, ${sc.days[1]}]`];
  lines.push(sc.events.length ? "events:" : "events: []");
  for (const e of sc.events) lines.push(`  - ${flow(e)}`);
  lines.push(`perturbation: ${flow(sc.perturbation)}`);
  lines.push(`overrides: ${flow(sc.overrides)}`);
  return lines.join("\n") + "\n";
}
function cap(s) { s = String(s ?? ""); return s.charAt(0).toUpperCase() + s.slice(1); }
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])); }
