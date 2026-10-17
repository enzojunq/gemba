// Compare view: two runs side by side, synced timelines, KPI deltas, first divergent decision.
import { Timeline } from "./timeline.js";

const TOTAL_LABEL = {
  on_time_pct: ["on-time (mean)", "%", 1, +1], mispick_rate: ["mis-pick (mean)", "%", 2, -1], lph: ["LPH (mean)", "", 1, +1],
  reslot_progress_pct: ["reslot progress (final)", "%", 0, +1], labor_cost_usd: ["labor cost (sum)", "$", 0, -1],
  near_misses: ["near-misses (sum)", "", 0, -1], jam_hours: ["jam hours (sum)", "", 0, -1],
};
const DAY_KPIS = [["on_time_pct", "on-time", " pts", 1], ["mispick_rate", "mis-picks", " pts", 2], ["lph", "lines/labor h", "", 1], ["reslot_progress_pct", "reslot progress", "%", 1], ["staging_occupancy_max", "staging", " pts", 0], ["moves_done", "slot moves", "", 0]];

export class Compare {
  constructor(el, { onCascade } = {}) { this.el = el; this.onCascade = onCascade; }

  async show(a, b) {
    const [cmp, ra, rb] = await Promise.all([
      fetch(`/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`).then((r) => r.json()),
      fetch(`/runs/${encodeURIComponent(a)}`).then((r) => r.json()),
      fetch(`/runs/${encodeURIComponent(b)}`).then((r) => r.json()),
    ]);
    this.render(cmp, ra, rb);
  }

  render(cmp, ra, rb) {
    const fd = cmp.first_divergence;
    const el = this.el;
    const verdict = this.verdict(cmp, ra, rb, fd);
    el.innerHTML = `
      <p class="cmp-verdict">${verdict}</p>
      <div class="cmp-head">
        <h2>Compare</h2>
        <span class="cmp-run a"><i></i>A <code>${esc(ra.run_id)}</code> ${esc(ra.profile || "")}${ra.fork_day != null ? ` · fork @${ra.fork_day}` : ""}</span>
        <span class="cmp-run b"><i></i>B <code>${esc(rb.run_id)}</code> ${esc(rb.profile || "")}${rb.fork_day != null ? ` · fork @${rb.fork_day}` : ""}</span>
        <span class="pane-sub">${rb.parent_run_id === ra.run_id ? `B forks A at day ${rb.fork_day}` : ra.parent_run_id === rb.run_id ? `A forks B at day ${ra.fork_day}` : "independent runs"}</span>
        <button class="btn" id="cmp-close" style="margin-left:auto"><svg class="ic"><use href="#i-x"/></svg>Back to run</button>
      </div>
      <div class="cmp-grid">
        <div class="cmp-card">
          <h3>First divergent decision</h3>
          ${fd ? `<div class="cmp-div">
              <div class="d a"><b>A · day ${fd.day}</b>${esc(fd.a.effect || fd.a.levers.join(" · ") || "no change")}<br><small>${esc(fd.a.rationale || "")}</small></div>
              <div class="d b"><b>B · day ${fd.day}</b>${esc(fd.b.effect || fd.b.levers.join(" · ") || "no change")}<br><small>${esc(fd.b.rationale || "")}</small></div>
            </div>` : `<p class="board-empty">The two runs made the same decisions on every shared day.</p>`}
        </div>
        <div class="cmp-card">
          <h3>Run totals · B minus A</h3>
          <table class="cmp-totals"><thead><tr><th>KPI</th><th>A</th><th>B</th><th>Δ</th></tr></thead><tbody>
          ${Object.entries(cmp.totals || {}).map(([k, v]) => {
            const [lab, unit, dp, good] = TOTAL_LABEL[k] || [k, "", 2, +1];
            const cls = v.delta === 0 ? "" : (v.delta * good > 0 ? "up" : "down");
            return `<tr><td>${esc(lab)}</td><td>${num(v.a, dp, unit)}</td><td>${num(v.b, dp, unit)}</td><td class="${cls}">${v.delta > 0 ? "+" : ""}${num(v.delta, dp, unit)}</td></tr>`;
          }).join("")}
          </tbody></table>
        </div>
      </div>
      <div class="cmp-tl">
        <div class="cmp-scrub">
          <span class="cmp-day" id="cmp-day"></span>
          <input type="range" id="cmp-range" min="${Math.min(ra.first_day ?? 61, rb.first_day ?? 61)}" max="${Math.max(ra.last_day ?? 90, rb.last_day ?? 90)}" value="${fd ? fd.day : (ra.first_day ?? 61)}">
          <span id="cmp-delta" class="pane-sub"></span>
        </div>
        <div class="cmp-tl-label a"><i></i>A · ${esc(ra.run_id)}</div>
        <div class="timeline-body" id="cmp-tl-a"></div>
        <div class="cmp-tl-label b"><i></i>B · ${esc(rb.run_id)}${rb.fork_day != null ? ` · diverges at day ${rb.fork_day}` : ""}</div>
        <div class="timeline-body" id="cmp-tl-b"></div>
      </div>`;
    const tla = new Timeline(el.querySelector("#cmp-tl-a"), { compact: true, onJump: (d) => setDay(d), onCascade: (id, t) => this.onCascade && this.onCascade(ra.run_id, id, t) });
    const tlb = new Timeline(el.querySelector("#cmp-tl-b"), { compact: true, onJump: (d) => setDay(d), onCascade: (id, t) => this.onCascade && this.onCascade(rb.run_id, id, t) });
    tla.setRun(ra); tlb.setRun(rb);
    const range = el.querySelector("#cmp-range");
    const rows = new Map((cmp.days || []).map((r) => [r.day, r]));
    const setDay = (d) => {
      range.value = d;
      tla.setCursor(d, 0.5); tlb.setCursor(d, 0.5);
      const row = rows.get(d);
      el.querySelector("#cmp-day").textContent = `Day ${d}${row && row.differs ? " · decisions differ" : ""}`;
      el.querySelector("#cmp-delta").textContent = row ? "B minus A on this day:  " + DAY_KPIS.map(([k, lab, unit, dp]) => {
        const dv = row.delta && row.delta[k]; return dv == null ? null : `${lab} ${dv > 0 ? "+" : ""}${Number(dv).toFixed(dp)}${unit}`;
      }).filter(Boolean).join("  ·  ") : "";
    };
    range.addEventListener("input", () => setDay(+range.value));
    setDay(+range.value);
    el.querySelector("#cmp-close").addEventListener("click", () => { location.href = `/?run=${encodeURIComponent(ra.run_id)}`; });
  }
}

Compare.prototype.verdict = function (cmp, ra, rb, fd) {
  const t = cmp.totals || {};
  const what = (rb.fork_decision && rb.fork_decision.label) ? rb.fork_decision.label : (fd ? `Choosing "${fd.b.effect || fd.b.levers.join(", ")}" on day ${fd.day}` : "Run B");
  const sgn = (v, dp) => (v > 0 ? "+" : "") + v.toFixed(dp);
  const span = (v, good, text) => `<span class="${v * good > 0 ? "up" : v * good < 0 ? "down" : ""}">${text}</span>`;
  const gains = [], costs = [];
  const push = (k, good, dp, label, unit) => {
    const v = t[k] && t[k].delta; if (v == null || v === 0) return;
    const txt = `${label} ${sgn(v, dp)}${unit}`;
    (v * good > 0 ? gains : costs).push(span(v, good, txt));
  };
  push("on_time_pct", +1, 1, "on-time", " pts"); push("mispick_rate", -1, 2, "mis-picks", " pts");
  push("reslot_progress_pct", +1, 0, "reslot progress", "%"); push("lph", +1, 1, "lines per labor hour", "");
  if (t.labor_cost_usd && t.labor_cost_usd.delta) { const v = t.labor_cost_usd.delta; (v < 0 ? gains : costs).push(span(v, -1, `${v > 0 ? "+" : "−"}$${Math.abs(Math.round(v / 1000))}k labor`)); }
  push("near_misses", -1, 0, "near-misses", "");
  if (!gains.length && !costs.length) return `${esc(what)} made no measurable difference against run A.`;
  return `${esc(what)} ${gains.length ? "gave " + gains.join(", ") : "gave nothing"}${costs.length ? `, at the cost of ${costs.join(", ")}` : ""}.`;
};

function num(v, dp, unit) { if (typeof v !== "number") return "—"; const s = unit === "$" ? "$" + Math.round(v).toLocaleString() : v.toFixed(dp) + unit; return s; }
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])); }
