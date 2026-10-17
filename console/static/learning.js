// Learning (Version N+1): both outcomes the data supports (COMMS B-13).
// Left: the retro's lessons, each verified by forking its regret day (one bar per lesson = fork
// minus parent). Right: the gate on held-out test scenarios, crew v0 vs crew vN with static rules
// as the reference tick, and the verdict under the rule it used. Headline = the gate row when
// there is one, else what the test rows say, else "waiting for the test".

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
const sgn = (v, d = 3) => (v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}`);
const SC_LABEL = (id) => id.replace(/^test_(\d+)$/, "Test $1").replace(/_/g, " ");
// The retro writes triggers as full sentences ("When a pm zone is down for >=4 hours, ..."); the
// card supplies its own "When", so strip the lead word and the final period.
const whenText = (r) => {
  let t = (r.trigger || (r.tags || []).join(", ").replace(/_/g, " ")).trim().replace(/^when\s+/i, "").replace(/[.]+$/, "");
  return t ? t[0].toLowerCase() + t.slice(1) : "";
};
const V1 = (scores) => Object.keys(scores || {}).find((k) => /^crew_v[1-9]/.test(k));

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
      body.innerHTML = `<p class="learn-empty">No learning cycle in this run store yet. After a retro and a gate, the lessons and the test on unseen scenarios appear here.</p>`;
      return;
    }
    const v = this.verdict(j);
    body.innerHTML = `<div class="learn-verdict is-${v.kind}">${v.html}</div>
      <div class="learn-grid">
        <section class="learn-rules"><h4>1 · Lessons verified by fork <span>(retro rule: the fork of the regret day must beat the parent, delta &gt; 0)</span></h4>${this.rules(j.rules)}</section>
        <section class="learn-chart"><h4>2 · Gate on unseen test scenarios <span>(rule: ${esc(j.gate ? j.gate.rule : "spec 13.2, mean test score up and violations not up")})</span></h4>${this.chart(j)}</section>
      </div>`;
    for (const b of body.querySelectorAll("[data-compare]")) b.addEventListener("click", () => { location.href = `/?compare=${encodeURIComponent(b.dataset.compare)}`; });
  }

  // Scores the gate compares: from the gate row, else the mean over the test rows.
  means(j) {
    const g = j.gate || {};
    let s0 = g.mean_score_v0, s1 = g.mean_score_v1;
    if (s0 == null || s1 == null) {
      const m = (k) => { const v = j.scenarios.map((s) => k && s.scores[k]).filter((x) => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
      s0 = m("crew_v0"); s1 = m(V1((j.scenarios[0] || {}).scores));
    }
    return { s0, s1 };
  }

  verdict(j) {
    const g = j.gate;
    const n = j.rules.filter((r) => r.status !== "rejected").length;
    const { s0, s1 } = this.means(j);
    const fb = g ? (g.fallbacks_from || 0) + (g.fallbacks_to || 0) : 0;
    if (g && g.no_verdict) {
      return { kind: "none", html: `<b>No verdict from the gate.</b> ${esc(g.reason || `${fb} LLM fallbacks in the test runs`)}: the rule gives no verdict when either side fell back, so the ${n} lesson${n === 1 ? "" : "s"} stay candidates.` };
    }
    if (s0 == null || s1 == null) {
      return { kind: "wait", html: `<b>${n} lesson${n === 1 ? "" : "s"} verified by fork, waiting for the gate.</b> Each beat its parent run when the org forked that day; the gate now checks whether the new playbook also wins on scenarios the org has never seen.` };
    }
    const won = g ? (g.promoted ?? g.improved) : s1 > s0;
    const sc = j.scenarios.length;
    const viol = g && g.violations_v0 != null ? ` · rule breaches per run ${(+g.violations_v0).toFixed(1)} → ${(+g.violations_v1).toFixed(1)}` : "";
    const earlier = (j.earlier_gates || []).map((e) => `<span class="learn-note">Earlier gate: ${e.promoted ? "promoted" : "not promoted"}${e.reason ? ` (${esc(e.reason)})` : ""}${e.mean_score_v0 != null ? `: ${sgn(e.mean_score_v0)} → ${sgn(e.mean_score_v1)}` : ""}.</span>`).join("");
    const fbNote = g && g.fallbacks_from != null ? `, ${(g.fallbacks_from || 0) + (g.fallbacks_to || 0)} LLM fallbacks` : "";
    return won
      ? { kind: "won", html: `<b>Promoted: version 1 beats version 0 on ${sc} scenario${sc === 1 ? "" : "s"} it never trained on.</b> Mean score ${sgn(s0)} → ${sgn(s1)} (margin ${sgn(s1 - s0, 4)})${viol}${fbNote}. The ${n} lesson${n === 1 ? "" : "s"} enter${n === 1 ? "s" : ""} the playbook. ${sc} scenario${sc === 1 ? "" : "s"} × 1 seed: evidence, not proof.${earlier}` }
      : { kind: "lost", html: `<b>Not promoted: version 1 scored below version 0 on the held-out scenario${sc === 1 ? "" : "s"}</b> (${sgn(s0)} → ${sgn(s1)}${viol}). The lessons helped on the days they came from, not on a month the org had never seen, so they stay out of the playbook. That is the gate doing its job.${g && g.reason ? ` <span class="learn-note">Gate note: ${esc(g.reason)}</span>` : ""}${earlier}` };
  }

  chart(j) {
    const scen = j.scenarios;
    if (!scen.length) return `<p class="learn-empty">The gate has not run on the test scenarios yet. Version 0 vs version 1 bars, with static rules as the reference line, appear here when it finishes.</p>`;
    const W = 560, H = 280, padL = 48, padB = 34, padT = 26, gw = (W - padL - 10) / scen.length;
    const vals = scen.flatMap((s) => Object.values(s.scores));
    const lo = Math.min(0, ...vals), hi = Math.max(0.01, ...vals) * 1.18;
    const y = (v) => padT + (H - padT - padB) * (1 - (v - lo) / (hi - lo));
    const svg = [`<svg viewBox="0 0 ${W} ${H}" class="learn-svg" role="img" aria-label="Version 0 vs version 1 score per test scenario">`];
    for (const t of [lo, 0, (hi / 1.18) / 2, hi / 1.18]) {
      if (t < lo || t > hi) continue;
      svg.push(`<line x1="${padL}" x2="${W - 6}" y1="${y(t)}" y2="${y(t)}" class="${t === 0 ? "ax-zero" : "ax-grid"}"/><text x="${padL - 6}" y="${y(t) + 4}" class="ax-t" text-anchor="end">${t === 0 ? "0" : sgn(t, 2)}</text>`);
    }
    scen.forEach((s, i) => {
      const x0 = padL + i * gw, bw = Math.min(64, gw * 0.28), cx = x0 + gw / 2;
      const v1k = V1(s.scores);
      if (s.scores.static != null) {  // drawn first so the bar labels sit on top of it
        const ys = y(s.scores.static);
        svg.push(`<line x1="${cx - bw - 14}" x2="${cx + bw + 14}" y1="${ys}" y2="${ys}" class="ref-st"/>`);
      }
      for (const [k, x, cls] of [["crew_v0", cx - bw - 4, "b-v0"], [v1k, cx + 4, "b-v1"]]) {
        const v = k ? s.scores[k] : null;
        if (v == null) continue;
        const top = Math.min(y(v), y(0)), h = Math.max(2, Math.abs(y(v) - y(0)));
        svg.push(`<rect x="${x}" y="${top}" width="${bw}" height="${h}" rx="3" class="${cls}"/><text x="${x + bw / 2}" y="${(v >= 0 ? top : top + h) + (v >= 0 ? -7 : 16)}" class="bar-t ${cls}-t" text-anchor="middle">${sgn(v)}</text>`);
      }
      svg.push(`<text x="${cx}" y="${H - 10}" class="ax-sc" text-anchor="middle">${esc(SC_LABEL(s.scenario))}</text>`);
    });
    svg.push("</svg>");
    const derived = scen.some((s) => s.static_derived);
    const rows = scen.map((s) => {
      const v1k = V1(s.scores), a = s.runs.crew_v0, b = v1k && s.runs[v1k];
      const d = v1k && s.scores[v1k] != null && s.scores.crew_v0 != null ? s.scores[v1k] - s.scores.crew_v0 : null;
      return `<div class="gate-row"><span>${esc(SC_LABEL(s.scenario))}: v1 − v0 = <b class="${d != null && d < 0 ? "is-neg" : ""}">${sgn(d)}</b></span>${a && b ? `<button type="button" class="btn btn-sm" data-compare="${esc(a)},${esc(b)}">Open v0 vs v1</button>` : ""}</div>`;
    }).join("");
    return svg.join("") + `<div class="learn-legend"><span><i class="lg-v0"></i>Version 0 · empty playbook</span><span><i class="lg-v1"></i>Version 1 · with the lessons</span><span><i class="lg-st"></i>Static rules${derived ? " (from the eval's score-vs-static)" : ""}</span></div>${rows}`;
  }

  rules(rules) {
    if (!rules.length) return `<p class="learn-empty">No lessons yet.</p>`;
    const order = { active: 0, candidate: 1, rejected: 2 };
    const max = Math.max(0.01, ...rules.map((r) => Math.abs(r.score_delta || 0)));
    return `<div class="learn-list">${[...rules].sort((a, b) => (order[a.status] ?? 1) - (order[b.status] ?? 1)).map((r) => {
      const st = r.status === "rejected" ? "rejected" : r.status === "active" ? "kept" : "candidate";
      const d = r.score_delta;
      const w = d == null ? 0 : Math.round(Math.abs(d) / max * 100);
      const proof = d == null ? "" : `fork on day ${r.day ?? "?"} beat the original by ${sgn(d)}${r.parent_score != null && r.fork_score != null ? ` (${sgn(r.parent_score)} → ${sgn(r.fork_score)})` : ""}`;
      return `<article class="lesson is-${st}">
        <header><span class="lesson-who">${esc(r.role)}</span><span class="lesson-title">${esc(r.title)}</span><span class="lesson-st s-${st}">${st === "candidate" ? "verified by fork" : st} · ${esc(r.version)}</span></header>
        <p><span class="lesson-when">When ${esc(whenText(r))}:</span> ${esc(r.advice)}</p>
        <div class="lesson-bar" title="fork score minus parent score over the window"><i style="width:${w}%" class="${d != null && d < 0 ? "is-neg" : ""}"></i><b>${sgn(d)}</b></div>
        <footer><span>${esc(proof)}</span>${r.compare ? `<button type="button" class="btn btn-sm" data-compare="${esc(r.compare)}">Open the fork</button>` : ""}</footer>
      </article>`;
    }).join("")}</div>`;
  }
}
