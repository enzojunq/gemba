// Meeting room: avatars around a table, streamed speech bubbles, options and the decision.

const ROSTER = [
  // name, title, kind (core | oncall | director), seat angle (deg, 0 = top, clockwise)
  { name: "Megan", title: "program manager", kind: "core", chair: true },
  { name: "Ryan", title: "ops manager", kind: "core" },
  { name: "Luke", title: "reslot engineer", kind: "core" },
  { name: "Paige", title: "analytics", kind: "core" },
  { name: "Dana", title: "plant director", kind: "director" },
  { name: "Sam", title: "safety", kind: "oncall" },
  { name: "Ellen", title: "quality", kind: "oncall" },
  { name: "Marcus", title: "shift lead", kind: "oncall" },
  { name: "Tom", title: "replenishment", kind: "oncall" },
];
// seat layout: two tidy rows. Row 1 = chair centred with the always-attending roles beside her
// (Dana at the end, lit only on escalations); row 2 = on-call roles. Normalized x; row index.
const SEATS = {
  Ryan: [0.16, 0], Luke: [0.33, 0], Megan: [0.5, 0], Paige: [0.67, 0], Dana: [0.84, 0],
  Sam: [0.3, 1], Ellen: [0.44, 1], Marcus: [0.58, 1], Tom: [0.72, 1],
};
// seat centre y in px for a seat area of height H (two rows, name + title under each ring)
const seatY = (row, H) => (row === 0 ? 22 : Math.max(86, H - 44));
const KIND_ICON = { veto: "#i-veto", lock: "#i-lock", decision: "#i-check", proposal: "#i-bolt" };

export class Room {
  constructor(el, subEl) {
    this.el = el;
    this.sub = subEl;
    this.hired = new Set();
    this.inRoom = new Set();
    this.day = null;
    this.build();
  }

  build() {
    this.el.innerHTML = `
      <div class="narr" id="narr"><span class="narr-k">What just happened</span><span class="narr-t" id="narr-t">Waiting for the first day…</span></div>
      <div class="mtg-head" id="mtg-head"><span class="mtg-kind" id="mtg-kind">—</span><span class="mtg-agenda" id="mtg-agenda"></span></div>
      <div class="room-table" id="room-table"></div>
      <div class="bubble-row" id="bubble-row" aria-live="polite"></div>
      <div class="transcript" id="transcript"></div>
      <div class="room-foot" id="room-foot"></div>`;
    this.tableEl = this.el.querySelector("#room-table");
    this.bubbleRow = this.el.querySelector("#bubble-row");
    this.kindEl = this.el.querySelector("#mtg-kind");
    this.agendaEl = this.el.querySelector("#mtg-agenda");
    this.transcript = this.el.querySelector("#transcript");
    this.foot = this.el.querySelector("#room-foot");
    this.narrEl = this.el.querySelector("#narr-t");
    this.story = [];
    this.notable = [];
    this.drawTable();
  }

  drawTable() {
    // seats live in an SVG sized to the area (viewBox = px) so circles stay round
    const seats = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const oncall = document.createElementNS("http://www.w3.org/2000/svg", "text");
    oncall.setAttribute("class", "seat-rowlabel"); oncall.textContent = "on call";
    seats.appendChild(oncall);
    this.oncallLabel = oncall;
    seats.setAttribute("class", "seats-svg");
    seats.style.cssText = "position:absolute;inset:0;width:100%;height:100%;overflow:visible";
    this.seatEls = {};
    for (const r of ROSTER) {
      const [nx, ny] = SEATS[r.name];
      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.setAttribute("class", `seat is-away k-${r.kind}`);
      g.dataset.name = r.name;
      g.innerHTML = `
        <g class="seat-pos">
          <circle class="seat-glow" r="18"/>
          <circle class="seat-ring" r="17"/>
          <text class="seat-ini">${r.name.slice(0, 2)}</text>
          <g class="seat-badge-g" transform="translate(11,-13)" style="display:none"><svg width="14" height="14" viewBox="0 0 24 24" x="-7" y="-7"><use href="#i-badge" class="${r.kind === "core" ? "seat-badge-core" : "seat-badge"}"/></svg></g>
          <g class="seat-veto" transform="translate(0,-2) rotate(-12)"><rect x="-26" y="-9" width="52" height="18" rx="3" fill="none" stroke="var(--red)" stroke-width="2.5"/><text x="0" y="5" text-anchor="middle" font-family="var(--font-display)" font-weight="700" font-size="12" fill="var(--red)" letter-spacing="2">VETO</text></g>
          <g class="seat-lock" transform="translate(13,10)"><circle r="9" fill="var(--teal)"/><svg width="12" height="12" viewBox="0 0 24 24" x="-6" y="-6"><use href="#i-lock" style="color:var(--bg)"/></svg></g>
          <text class="seat-name" y="30">${r.name}</text>
          <text class="seat-title" y="41">${r.title}</text>
        </g>`;
      seats.appendChild(g);
      this.seatEls[r.name] = g;
    }
    this.tableEl.appendChild(seats);
    this.seatsSvg = seats;
    this.ro = new ResizeObserver(() => this.placeSeats());
    this.ro.observe(this.tableEl);
    this.placeSeats();
  }

  placeSeats() {
    const r = this.tableEl.getBoundingClientRect();
    this.seatsSvg.setAttribute("viewBox", `0 0 ${r.width} ${r.height}`);
    for (const name in this.seatEls) {
      const [nx, row] = SEATS[name];
      const g = this.seatEls[name].querySelector(".seat-pos");
      g.setAttribute("transform", `translate(${(nx * r.width).toFixed(1)},${seatY(row, r.height).toFixed(1)})`);
    }
    this.oncallLabel.setAttribute("x", (0.3 * r.width - 40).toFixed(1));
    this.oncallLabel.setAttribute("y", (seatY(1, r.height) + 4).toFixed(1));
  }

  setHead(kind, agenda) {
    this.kindEl.textContent = kind;
    this.agendaEl.textContent = agenda || "";
  }

  // ------------------------------------------------------------------ narration ("what just happened")
  narrate(clauses, reset = false) {
    if (reset) this.story = [];
    for (const c of clauses) if (c && !this.story.includes(c)) this.story.push(c);
    const last = this.story.slice(-4);
    this.narrEl.innerHTML = last.map((c, i) => `${i ? '<span class="arrow">→</span>' : ""}${esc(cap(c))}`).join("");
    this.narrEl.classList.remove("narr-t"); void this.narrEl.offsetWidth; this.narrEl.classList.add("narr-t");
  }

  // ------------------------------------------------------------------ state
  setActiveHires(names) {
    this.hired = new Set(names || []);
    for (const r of ROSTER) {
      const g = this.seatEls[r.name];
      g.querySelector(".seat-badge-g").style.display = (r.kind !== "core" && this.hired.has(r.name)) ? "" : "none";
    }
  }

  // Spec 10.5: a quiet day means code found no new events, consequences, drift or inbox, and KPIs
  // inside their bands. The standing plan continues and the chair writes one line.
  quietDay(bundle) {
    const day = bundle.day, decision = bundle.decision, m = bundle.meeting;
    this.day = day;
    this.inRoom = new Set();
    this.clearBubble();
    const present = new Set(["Megan", "Ryan", "Luke", "Paige", ...(this.hired.has("Dana") ? ["Dana"] : [])]);
    for (const r of ROSTER) {
      const g = this.seatEls[r.name];
      g.classList.toggle("is-away", !present.has(r.name));
      g.classList.toggle("is-muted", present.has(r.name));
      g.classList.remove("is-speaking", "is-veto", "is-lock");
    }
    this.setHead("Quiet day", "standing plan continues, no agenda");
    const why = quietWhy(bundle);
    const note = (m && m.utterances && m.utterances.length ? m.utterances[0].text : null) || (decision && decision.rationale) || "No agenda. The plan holds.";
    this.transcript.innerHTML = `<div class="quiet"><div><span class="quiet-k">Quiet day · standing plan continues</span><b>Day ${day}</b><small>${esc(why)}</small></div></div>`;
    // the chair's one line, as a single bubble from her avatar (ring lit, no floating bubble over the table)
    this.seatEls.Megan.classList.add("is-speaking");
    const row = document.createElement("div");
    row.className = "utt k-decision utt-quiet";
    row.innerHTML = `<div class="utt-av">Me</div><div class="utt-body"><div class="utt-head"><span class="utt-name">Megan</span><span class="utt-role">program manager</span><span class="utt-kind k-decision"><svg class="ic"><use href="#i-check"/></svg>chair's note</span></div><div class="utt-text">${esc(note)}</div></div>`;
    this.transcript.appendChild(row);
    this.foot.innerHTML = "";
    if (decision) this.foot.appendChild(this.decisionBanner(decision, null, "Standing plan"));
    this.sub.textContent = `Quiet day · standing plan continues · ${why}`;
    this.narrate([`quiet day: ${why}`, `Megan keeps the plan: ${(decision && decision.effect) || "no change"}`], true);
  }

  open(day, kind, attendees, agenda) {
    this.day = day;
    this.inRoom = new Set(attendees || []);
    this.clearBubble();
    for (const r of ROSTER) {
      const g = this.seatEls[r.name];
      g.classList.toggle("is-away", !this.inRoom.has(r.name));
      g.classList.remove("is-speaking", "is-veto", "is-lock", "is-muted");
    }
    const esc_ = kind === "escalation";
    const ag = (agenda || []).map(humanAgenda).join(" · ");
    this.setHead(esc_ ? "Escalation · Dana in the room" : "Standup", ag ? `agenda: ${ag}` : "no agenda");
    this.transcript.innerHTML = "";
    this.foot.innerHTML = "";
    this.notable = [];
    this.kindLabel = esc_ ? "Escalation · Dana in the room" : "Standup";
    const guests = [...this.inRoom].filter((n) => ROSTER.some((r) => r.name === n && r.kind !== "core"));
    this.sub.textContent = `${this.kindLabel} · ${this.inRoom.size} in the room` + (guests.length ? ` · ${guests.join(", ")} called in` : "");
    this.narrate(guests.map((n) => `${n} (${(ROSTER.find((r) => r.name === n) || {}).title}) joined the meeting`));
  }

  utterance(u) {
    const name = u.role;
    for (const n in this.seatEls) this.seatEls[n].classList.remove("is-speaking");
    const g = this.seatEls[name];
    if (g) {
      g.classList.remove("is-away");
      g.classList.add("is-speaking");
      if (u.kind === "veto") g.classList.add("is-veto");
      if (u.kind === "lock") g.classList.add("is-lock");
    }
    this.showBubble(name, u);
    if (u.kind === "veto") { this.notable.push(`${name} vetoed option ${u.option_label || ""}`.trim()); this.narrate([this.notable[this.notable.length - 1]]); }
    if (u.kind === "lock") { this.notable.push(`${name} locked ${lockTarget(u.text)}`); this.narrate([this.notable[this.notable.length - 1]]); }
    this.sub.textContent = `${this.kindLabel} · ${name} is speaking` + (this.notable.length ? ` · ${this.notable[this.notable.length - 1]}` : "");
    const row = document.createElement("div");
    row.className = `utt k-${u.kind}`;
    const icon = KIND_ICON[u.kind] ? `<svg class="ic"><use href="${KIND_ICON[u.kind]}"/></svg>` : "";
    const opt = u.option_label ? ` · option ${u.option_label}` : "";
    row.innerHTML = `<div class="utt-av">${esc(name.slice(0, 2))}</div>
      <div class="utt-body"><div class="utt-head"><span class="utt-name">${esc(name)}</span><span class="utt-role">${esc(u.title || "")}</span><span class="utt-kind k-${u.kind}">${icon}${esc(u.kind)}${opt}</span></div>
      <div class="utt-text">${esc(u.text)}</div></div>`;
    this.transcript.appendChild(row);
    this.transcript.scrollTop = this.transcript.scrollHeight;
  }

  // The live bubble lives in its own row under the seats (never over a name); its pointer
  // sits under the speaker's seat.
  showBubble(name, u) {
    this.clearBubble();
    const [nx] = SEATS[name] || [0.5, 0.5];
    const b = document.createElement("div");
    b.className = `bubble-live k-${u.kind}`;
    const text = u.text.length > 220 ? u.text.slice(0, 218) + "…" : u.text;
    const r = this.tableEl.getBoundingClientRect();
    b.style.setProperty("--ax", `${Math.round(Math.max(28, Math.min(r.width - 28, nx * r.width)))}px`);
    b.innerHTML = `<span class="bubble-who">${esc(name)}</span><span class="bubble-text">${esc(text)}</span>`;
    this.bubbleRow.appendChild(b);
    this.bubble = b;
  }

  clearBubble() { if (this.bubble) { this.bubble.remove(); this.bubble = null; } }

  close(options, decision, outcome) {
    for (const n in this.seatEls) this.seatEls[n].classList.remove("is-speaking");
    this.foot.innerHTML = "";
    if (options && options.length) {
      const wrap = document.createElement("div");
      wrap.className = "options";
      const pick = decision && decision.option_id;
      for (const o of options) {
        const d = document.createElement("div");
        const isPick = pick === o.option_id;
        d.className = `opt ${isPick ? "is-pick" : ""} ${o.vetoed_by ? "is-veto" : ""} ${o.feasible === false ? "is-infeasible" : ""}`;
        const flag = o.vetoed_by ? `<span class="opt-flag f-veto"><svg class="ic"><use href="#i-veto"/></svg>vetoed by ${esc(o.vetoed_by)}</span>`
          : o.feasible === false ? `<span class="opt-flag f-no"><svg class="ic"><use href="#i-x"/></svg>infeasible</span>`
          : `<span class="opt-flag f-ok"><svg class="ic"><use href="#i-check"/></svg>feasible</span>`;
        d.innerHTML = `<div class="opt-head"><span class="opt-l">${esc(o.label)}</span><span class="opt-by">${esc(o.proposed_by)}</span><span class="opt-score" title="scorecard score, higher is better">${o.score != null ? o.score.toFixed(2) : "—"}</span></div>
          <div class="opt-bar"><i></i></div>
          <div class="opt-levers">${esc(o.effect || (o.lever_labels || []).join(" · "))}</div>
          <div class="opt-raw" title="${esc((o.lever_labels || []).join("; "))}">${esc((o.lever_labels || []).join(" · "))}</div>${flag}`;
        wrap.appendChild(d);
        requestAnimationFrame(() => { d.querySelector(".opt-bar i").style.transform = `scaleX(${Math.min(1, o.score || 0)})`; });
      }
      this.foot.appendChild(wrap);
    }
    if (decision) this.foot.appendChild(this.decisionBanner(decision, options));
    const eff = decision ? (decision.effect || (decision.lever_labels || []).join(" · ")) : "no decision";
    this.sub.textContent = `${this.kindLabel}` + (this.notable.length ? ` · ${this.notable.join(" · ")}` : "") + ` · decided: ${eff}`;
    this.narrate([`decided: ${eff}`]);
  }

  decisionBanner(decision, options, kicker = "Decision") {
    const d = document.createElement("div");
    d.className = "decision";
    const levers = (decision.lever_labels || []).join(" · ") || "no change";
    const effect = decision.effect || levers;
    const opt = decision.option_id ? `Option ${decision.option_id.split("-").pop()}: ` : "";
    const dev = decision.deviates_from_top_score ? " · chair overrode the top score" : "";
    const rat = kicker === "Decision" ? esc(decision.rationale || "") + esc(dev) : `${esc(decision.decided_by || "Megan")} keeps yesterday's plan${esc(dev)}`;
    d.innerHTML = `<span class="decision-k">${esc(kicker)}</span><div><div class="decision-t">${esc(cap(opt + effect))}</div><div class="decision-r">${rat}</div><div class="decision-l">${esc(levers)}</div></div>`;
    return d;
  }

  // Render a whole day at once (jump / resume without streaming the meeting).
  renderDay(bundle) {
    this.setActiveHires(bundle.active_hires);
    const m = bundle.meeting;
    if (!m || m.kind === "quiet") { this.quietDay(bundle); return; }
    this.narrate((bundle.events || []).map((e) => e.plain), true);
    this.open(bundle.day, m.kind, m.attendees, m.agenda);
    for (const u of m.utterances) this.utterance(u);
    this.clearBubble();
    this.close(m.options, bundle.decision, m.outcome);
  }
}

// Agenda strings come from the org as "kind: detail"; make the kind readable.
function humanAgenda(s) {
  return String(s ?? "").replace(/^([a-z_]+):/, (m, k) => k.replace(/_/g, " ") + ":");
}
function cap(s) { s = String(s ?? ""); return s.charAt(0).toUpperCase() + s.slice(1); }
function lockTarget(text) {
  const m = /\b([A-Z]\d{3})\b/.exec(text || "");
  return m ? `${m[1]}'s SKUs in place` : "SKUs in place";
}

// One short reason line for a quiet day, derived from the day bundle (not hardcoded).
function quietWhy(b) {
  const parts = [];
  parts.push((b.events || []).length ? `${b.events.length} event(s) logged but no agenda` : "no new events");
  const failed = (b.checks || []).filter((c) => c.scope === "daily" && c.passed === false);
  parts.push(failed.length ? `${failed.length} check(s) outside target` : "KPIs within target");
  if ((b.drift || []).length) parts.push("drift flagged");
  return parts.join(", ");
}

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])); }
