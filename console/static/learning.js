// Learning (Version N+1): did the org get better on scenarios it never trained on, and which
// lessons did it keep. Chart = crew v0 vs crew vN per unseen test scenario, static rules as the
// reference tick; rules list = each lesson with the fork that proved it.

const NS = "http://www.w3.org/2000/svg";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
const sgn = (v) => (v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(3)}`);
const SC_LABEL = (id) => id.replace(/^test_(\d+)$/, "Test $1").replace(/_/g, " ");

export class Learning {
  constructor(dlg) {
    this.dlg = dlg;
    dlg.querySelector("#learn-close").addEventListener("click", () => dlg.close());
  }

  async open() {
    this.dlg.showModal();
    const body = this.dlg.querySelector("#learn-body");
    body.innerHTML = `<p class="learn-empty">Loading…</p>`;
    let j;
    try { j = await fetch("/learning").then((r) => r.json()); } catch { j = null; }
    if (!j || (!j.rules.length && !j.scenarios.length)) {
      body.innerHTML = `<p class="learn-empty">No learning cycle in this run store yet. After a retro and a gate, the lessons the org kept and the v0 vs v1 test results appear here.</p>`;
      return;
    }
    body.innerHTML = `<div class="learn-verdict">${this.verdict(j)}</div>
      <div class="learn-grid">
        <section class="learn-chart"><h4>Score on unseen test scenarios <span>(vs doing nothing; higher is better)</span></h4>${this.chart(j.scenarios)}
          <div class="learn-legend"><span><i class="lg-v0"></i>Version 0 · empty playbook</span><span><i class="lg-v1"></i>Version 1 · with the lessons</span><span><i class="lg-st"></i>Static rules</span></div></section>
        <section class="learn-rules"><h4>Lessons from the retro <span>(each proved by forking its worst day)</span></h4>${this.rules(j.rules)}</section>
      </div>`;
    for (const b of body.querySelectorAll("[data-compare]")) b.addEventListener("click", () => { location.href = `/?compare=${encodeURIComponent(b.dataset.compare)}`; });
  }

  verdict(j) {
    const g = j.gate;
    if (g && g.reason) return `<b>No verdict.</b> ${esc(g.reason)}`;
    let s0 = g && g.mean_score_v0, s1 = g && g.mean_score_v1;
    if (s0 == null || s1 == null) {
      const m = (k) => { const v = j.scenarios.map((s) => s.scores[k]).filter((x) => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
      s0 = m("crew_v0"); s1 = m(Object.keys((j.scenarios[0] || {}).scores || {}).find((k) => /^crew_v[1-9]/.test(k)));
    }
    if (s0 == null || s1 == null) {
      const n = j.rules.filter((r) => r.status !== "rejected").length;
      return `<b>${n} lesson${n === 1 ? "" : "s"} waiting for the test.</b> Each was proved by forking its day; the gate now checks whether the new playbook wins on scenarios the org has never seen.`;
    }
    const won = g ? g.improved : s1 > s0;
    const viol = g && g.violations_v0 != null ? ` · rule breaches per run ${g.violations_v0.toFixed(1)} → ${g.violations_v1.toFixed(1)}` : "";
    return won
      ? `<b>Version 1 beats version 0 on ${j.scenarios.length} scenarios it never trained on.</b> Mean score ${sgn(s0)} → ${sgn(s1)}${viol}. The lessons were promoted.`
      : `<b>Version 1 did not beat version 0 on the unseen scenarios</b> (mean ${sgn(s0)} → ${sgn(s1)}${viol}), so the lessons were not promoted.`;
  }

  chart(scen) {
    if (!scen.length) return `<p class="learn-empty">The test on unseen scenarios has not run yet. Its result (version 0 vs version 1, static rules as the reference) appears here when the gate finishes.</p>`;
    const W = 620, H = 300, padL = 44, padB = 34, padT = 26, gw = (W - padL - 10) / scen.length;
    const vals = scen.flatMap((s) => Object.values(s.scores));
    const lo = Math.min(0, ...vals), hi = Math.max(0.01, ...vals) * 1.18;
    const y = (v) => padT + (H - padT - padB) * (1 - (v - lo) / (hi - lo));
    const svg = [`<svg viewBox="0 0 ${W} ${H}" class="learn-svg" role="img" aria-label="Version 0 vs version 1 score per test scenario">`];
    for (const t of [lo, 0, (hi / 1.18) / 2, hi / 1.18]) {
      if (t < lo || t > hi) continue;
      svg.push(`<line x1="${padL}" x2="${W - 6}" y1="${y(t)}" y2="${y(t)}" class="${t === 0 ? "ax-zero" : "ax-grid"}"/><text x="${padL - 6}" y="${y(t) + 4}" class="ax-t" text-anchor="end">${t === 0 ? "0" : sgn(t)}</text>`);
    }
    scen.forEach((s, i) => {
      const x0 = padL + i * gw, bw = Math.min(64, gw * 0.28), cx = x0 + gw / 2;
      const v1k = Object.keys(s.scores).find((k) => /^crew_v[1-9]/.test(k));
      const bars = [["crew_v0", cx - bw - 4, "b-v0"], [v1k, cx + 4, "b-v1"]];
      if (s.scores.static != null) {  // drawn first so the bar labels sit on top of it
        const ys = y(s.scores.static);
        svg.push(`<line x1="${cx - bw - 14}" x2="${cx + bw + 14}" y1="${ys}" y2="${ys}" class="ref-st"/>`);
      }
      for (const [k, x, cls] of bars) {
        const v = k ? s.scores[k] : null;
        if (v == null) continue;
        const top = Math.min(y(v), y(0)), h = Math.max(2, Math.abs(y(v) - y(0)));
        svg.push(`<rect x="${x}" y="${top}" width="${bw}" height="${h}" rx="3" class="${cls}"/><text x="${x + bw / 2}" y="${(v >= 0 ? top : top + h) + (v >= 0 ? -7 : 16)}" class="bar-t ${cls}-t" text-anchor="middle">${sgn(v)}</text>`);
      }
      svg.push(`<text x="${cx}" y="${H - 10}" class="ax-sc" text-anchor="middle">${esc(SC_LABEL(s.scenario))}</text>`);
    });
    svg.push("</svg>");
    return svg.join("");
  }

  rules(rules) {
    if (!rules.length) return `<p class="learn-empty">No lessons yet.</p>`;
    const order = { active: 0, candidate: 1, rejected: 2 };
    return `<div class="learn-list">${[...rules].sort((a, b) => (order[a.status] ?? 1) - (order[b.status] ?? 1)).map((r) => {
      const st = r.status === "rejected" ? "rejected" : r.status === "active" ? "kept" : "candidate";
      const proof = r.score_delta != null ? `fork on day ${r.day ?? "?"} scored ${sgn(r.score_delta)} vs the original` : "";
      return `<article class="lesson is-${st}">
        <header><span class="lesson-who">${esc(r.role)}</span><span class="lesson-title">${esc(r.title)}</span><span class="lesson-st s-${st}">${st} · ${esc(r.version)}</span></header>
        <p><span class="lesson-when">When ${esc(r.trigger || (r.tags || []).join(", ").replace(/_/g, " "))}:</span> ${esc(r.advice)}</p>
        <footer><span>${esc(proof)}</span>${r.compare ? `<button type="button" class="btn btn-sm" data-compare="${esc(r.compare)}">Open the fork</button>` : ""}</footer>
      </article>`;
    }).join("")}</div>`;
  }
}
