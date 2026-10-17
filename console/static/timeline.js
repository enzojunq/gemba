// Timeline: days 61–90, event / decision / meeting markers, KPI sparklines with done-check
// threshold lines, a cursor for the current day+hour. Pure SVG, re-rendered on data change.

const NS = "http://www.w3.org/2000/svg";
const SPARKS = [
  { k: "on_time_pct", label: "on-time", unit: "% of orders on time", fmt: (v) => v.toFixed(1) + "%", thr: "on_time_pct", range: [90, 100] },
  { k: "mispick_rate", label: "mis-picks", unit: "% of lines picked wrong", fmt: (v) => v.toFixed(2) + "%", thr: "mispick_rate", range: [0, 1.2] },
  { k: "lph", label: "lines/hour", unit: "lines per labor hour", fmt: (v) => v.toFixed(0), thr: null, range: null },
  { k: "reslot_progress_pct", label: "slot moves", unit: "% of slot moves done", fmt: (v) => v.toFixed(0) + "%", thr: "reslot_progress_pct", range: [0, 100] },
];

export class Timeline {
  constructor(el, { onJump, onCascade, compact = false, accent = null } = {}) {
    this.el = el;
    this.onJump = onJump;
    this.onCascade = onCascade;
    this.compact = compact;
    this.accent = accent;
    this.run = null;
    this.cursor = { day: null, frac: 0 };
    this.svg = document.createElementNS(NS, "svg");
    this.el.appendChild(this.svg);
    this.tip = document.createElement("div");
    this.tip.className = "tl-tip"; this.tip.hidden = true;
    this.el.appendChild(this.tip);
    this.ro = new ResizeObserver(() => this.render());
    this.ro.observe(el);
  }

  setRun(run) { this.run = run; this.render(); }
  setCursor(day, frac = 0) {
    this.cursor = { day, frac };
    this.moveCursor();
  }

  geometry() {
    const r = this.el.getBoundingClientRect();
    const left = this.compact ? 112 : 156, right = this.compact ? 80 : 90, top = 8;
    const W = Math.max(left + right + 30 * 4, r.width), H = Math.max(100, r.height);
    const days = this.run && this.run.days.length ? this.run.days.map((d) => d.day) : [];
    const d0 = this.run && this.run.first_day != null ? Math.min(61, this.run.first_day) : 61;
    const d1 = this.run && this.run.last_day != null ? Math.max(90, this.run.last_day) : 90;
    const n = d1 - d0 + 1;
    const colW = Math.max(0.5, (W - left - right) / n);
    const x = (day) => left + (day - d0) * colW;
    return { W, H, left, right, top, d0, d1, n, colW, x, days };
  }

  render() {
    if (this.el.getBoundingClientRect().width < 10) return;  // not laid out yet; the ResizeObserver re-renders
    const g = this.geometry();
    const { W, H, left, top, d0, d1, colW, x } = g;
    const svg = this.svg;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("width", W); svg.setAttribute("height", H);
    svg.innerHTML = "";
    const run = this.run;
    const markerH = this.compact ? 16 : 20;
    const rowEvt = top + markerH * 0.55;
    const rowDec = top + markerH * 1.5;
    const axisY = H - 14;
    const sparkTop = top + markerH * 2.1;
    const sparkArea = axisY - sparkTop - 6;
    const lanes = SPARKS.length;
    const laneH = sparkArea / lanes;

    // grid + day labels
    for (let d = d0; d <= d1; d++) {
      const cx = x(d) + colW / 2;
      const line = el("line", { class: "tl-grid", x1: x(d), x2: x(d), y1: top, y2: axisY });
      if ((d - d0) % 1 === 0) svg.appendChild(line);
      const t = el("text", { class: "tl-day", x: cx, y: H - 3 }); t.textContent = d; t.dataset.day = d;
      svg.appendChild(t);
      const hit = el("rect", { class: "tl-hit", x: x(d), y: top, width: colW, height: Math.max(0, axisY - top) });
      hit.addEventListener("click", () => this.onJump && this.onJump(d));
      hit.addEventListener("mouseenter", (e) => this.showTip(e, this.dayTip(d)));
      hit.addEventListener("mouseleave", () => this.hideTip());
      svg.appendChild(hit);
    }
    svg.appendChild(el("line", { class: "tl-grid", x1: left, x2: W - g.right, y1: axisY, y2: axisY }));

    if (!run) return;
    const byDay = new Map(run.days.map((d) => [d.day, d]));

    // fork line
    if (run.fork_day != null) {
      const fx = x(run.fork_day);
      svg.appendChild(el("line", { class: "tl-fork", x1: fx, x2: fx, y1: top, y2: axisY }));
      const t = el("text", { class: "tl-fork-l", x: fx + 4, y: top + 9 }); t.textContent = `fork · day ${run.fork_day}`;
      svg.appendChild(t);
    }

    // sparklines
    SPARKS.forEach((sp, i) => {
      const y0 = sparkTop + i * laneH, y1 = y0 + laneH - 6;
      const vals = run.days.map((d) => [d.day, d.kpis ? d.kpis[sp.k] : null]).filter((p) => typeof p[1] === "number");
      if (!vals.length) return;
      let [lo, hi] = sp.range || [Math.min(...vals.map((p) => p[1])), Math.max(...vals.map((p) => p[1]))];
      if (!sp.range) { const pad = (hi - lo) * 0.15 || 1; lo -= pad; hi += pad; }
      const thr = sp.thr && run.thresholds ? run.thresholds[sp.thr] : null;
      if (thr != null) { lo = Math.min(lo, thr); hi = Math.max(hi, thr); }
      const yv = (v) => y1 - ((v - lo) / (hi - lo || 1)) * (y1 - y0);
      const pts = vals.map(([d, v]) => `${(x(d) + colW / 2).toFixed(1)},${yv(v).toFixed(1)}`);
      const fill = el("path", { class: `tl-fill k-${sp.k}`, d: `M${(x(vals[0][0]) + colW / 2).toFixed(1)},${y1} L${pts.join(" L")} L${(x(vals[vals.length - 1][0]) + colW / 2).toFixed(1)},${y1} Z` });
      svg.appendChild(fill);
      svg.appendChild(el("path", { class: `tl-spark k-${sp.k}`, d: `M${pts.join(" L")}` }));
      if (thr != null) {
        svg.appendChild(el("line", { class: "tl-thr", x1: left, x2: W - g.right, y1: yv(thr), y2: yv(thr) }));
        const tl = el("text", { class: "tl-thr-l", x: W - g.right + 3, y: yv(thr) + 3 }); tl.textContent = `target ${sp.k === "mispick_rate" ? "≤" : "≥"} ${thr}`;
        svg.appendChild(tl);
      }
      const lab = el("text", { class: "tl-lane", x: 8, y: y0 + laneH / 2 + 4 }); lab.textContent = sp.label; svg.appendChild(lab);
      const tt = el("title", {}); tt.textContent = sp.unit; lab.appendChild(tt);
      const val = el("text", { class: "tl-val", x: left - 8, y: y0 + laneH / 2 + 4, "data-val": sp.k, style: this.compact ? "display:none" : "" }); svg.appendChild(val);
      // failed checks tick under the lane
      for (const d of run.days) {
        if ((d.checks_failed || []).some((c) => c.endsWith("." + sp.k))) {
          svg.appendChild(el("rect", { class: "tl-fail", x: x(d.day) + colW * 0.25, y: y1 + 1, width: colW * 0.5, height: 2.5, rx: 1 }));
        }
      }
    });

    // markers: events (row 1), decisions + meetings (row 2)
    const evByDay = new Map();
    for (const e of run.events || []) { if (!evByDay.has(e.day)) evByDay.set(e.day, []); evByDay.get(e.day).push(e); }
    for (const [d, evs] of evByDay) {
      evs.forEach((e, j) => {
        const gm = el("g", { class: `tl-mark m-evt s-${e.source}` });
        const cx = x(d) + colW / 2 + (j - (evs.length - 1) / 2) * 7;
        gm.appendChild(el("circle", { cx, cy: rowEvt, r: this.compact ? 3.2 : 4, style: `transform-origin:${cx}px ${rowEvt}px` }));
        gm.addEventListener("click", (ev) => { ev.stopPropagation(); this.onCascade && this.onCascade(`evt:${e.event_id}`, e.label); });
        gm.addEventListener("mouseenter", (ev) => this.showTip(ev, `<b>${esc(e.plain || e.label)}</b><small>${e.source === "consequence" ? "consequence of an earlier decision" : e.source + " event"} · day ${d}, ${String(e.hour).padStart(2, "0")}:00 · click for cascade</small>`));
        gm.addEventListener("mouseleave", () => this.hideTip());
        svg.appendChild(gm);
      });
    }
    for (const d of run.days) {
      const cx = x(d.day) + colW / 2;
      if (d.decision_id) {
        const s = this.compact ? 5.5 : 7;
        const gm = el("g", { class: `tl-mark m-dec ${d.has_meeting ? `m-mtg is-${d.meeting_kind}` : "is-quiet"}` });
        if (d.has_meeting) gm.appendChild(el("circle", { class: "tl-ring", cx, cy: rowDec, r: this.compact ? 7 : 9, style: `transform-origin:${cx}px ${rowDec}px` }));
        gm.appendChild(el("rect", { x: cx - s / 2, y: rowDec - s / 2, width: s, height: s, rx: 1, transform: `rotate(45 ${cx} ${rowDec})`, style: `transform-origin:${cx}px ${rowDec}px` }));
        const head = d.has_meeting ? `${esc(d.meeting_kind)} + decision · day ${d.day}` : `Decision · day ${d.day}`;
        gm.addEventListener("click", (ev) => { ev.stopPropagation(); this.onCascade && this.onCascade(`dec:${d.decision_id}`, d.decision_label); });
        gm.addEventListener("mouseenter", (ev) => this.showTip(ev, `<b>${head}</b><small>${esc(d.decision_effect || d.decision_label || "no change")}${d.deviates ? " · chair overrode the top score" : ""} · click for cascade</small>`));
        gm.addEventListener("mouseleave", () => this.hideTip());
        svg.appendChild(gm);
      }
    }
    // cursor
    this.cursorG = el("g", { class: "tl-cursor-g" });
    this.cursorG.appendChild(el("line", { class: "tl-cursor", x1: 0, x2: 0, y1: top, y2: axisY }));
    this.cursorG.appendChild(el("path", { class: "tl-cursor-head", d: "M-5,0 L5,0 L0,6 Z", transform: `translate(0,${top - 6})` }));
    svg.appendChild(this.cursorG);
    this.geo = { ...g, axisY };
    this.moveCursor();
  }

  moveCursor() {
    if (!this.cursorG || !this.geo || this.cursor.day == null) return;
    const { x, colW } = this.geo;
    const cx = x(this.cursor.day) + colW * Math.min(1, Math.max(0, this.cursor.frac));
    this.cursorG.setAttribute("transform", `translate(${cx.toFixed(1)},0)`);
    this.cursorG.style.display = "";
    for (const t of this.svg.querySelectorAll(".tl-day")) t.classList.toggle("is-now", +t.dataset.day === this.cursor.day);
    const d = this.run && this.run.days.find((q) => q.day === this.cursor.day);
    for (const sp of SPARKS) {
      const t = this.svg.querySelector(`[data-val="${sp.k}"]`);
      if (t) t.textContent = d && d.kpis && typeof d.kpis[sp.k] === "number" ? sp.fmt(d.kpis[sp.k]) : "";
    }
  }

  dayTip(d) {
    const row = this.run && this.run.days.find((q) => q.day === d);
    if (!row) return `<b>Day ${d}</b><small>not simulated yet</small>`;
    const k = row.kpis || {};
    return `<b>Day ${d}${row.has_meeting ? " · " + row.meeting_kind : ""}</b><small>on-time ${fmt(k.on_time_pct, 1)}% · mis-pick ${fmt(k.mispick_rate, 2)}% · LPH ${fmt(k.lph, 0)} · progress ${fmt(k.reslot_progress_pct, 0)}%<br>${esc(row.decision_label || "")}<br>click to jump</small>`;
  }

  showTip(ev, html) {
    const r = this.el.getBoundingClientRect();
    this.tip.innerHTML = html; this.tip.hidden = false;
    const x = ev.clientX - r.left + 12, y = ev.clientY - r.top + 12;
    this.tip.style.left = `${Math.min(x, r.width - 330)}px`;
    this.tip.style.top = `${Math.min(y, r.height - 70)}px`;
  }
  hideTip() { this.tip.hidden = true; }
}

function el(tag, attrs) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}
function fmt(v, n) { return typeof v === "number" ? v.toFixed(n) : "—"; }
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])); }
