// Meeting room. Primary path: who is speaking and what they said → what was decided.
// Vertical budget (top → bottom): pane head (kind + agenda) · "What just happened" · decision
// banner once the meeting has closed (the room's title) · seats (thin band) · TRANSCRIPT (flex 1,
// the tallest region, follows the newest utterance) · dock with the options row.
// Roles are keyed by ROLE ID (B's org); names and titles come from the server's roles map.

const CORE_ORDER = ["ops_manager", "reslotting_engineer", "program_manager", "analytics", "plant_director"];
const ONCALL_ORDER = ["safety", "quality", "shift_lead", "replenishment"];
const KIND_ICON = { veto: "#i-veto", lock: "#i-lock", decision: "#i-check", proposal: "#i-bolt" };
const KIND_WORD = { veto: "VETO", lock: "LOCK", proposal: "PROPOSAL", position: "POSITION", question: "QUESTION", decision: "DECISION" };

export class Room {
  constructor(el, subEl) {
    this.el = el;
    this.sub = subEl;
    this.roles = {};
    this.hired = new Set();
    this.inRoom = new Set();
    this.day = null;
    this.story = [];
    this.notable = [];
    this.lastSpeaker = null;
    this.seatEls = {};
    this.build();
  }

  build() {
    this.el.innerHTML = `
      <div class="narr" id="narr"><span class="narr-k">What just happened</span><span class="narr-t" id="narr-t">Waiting for the first day…</span></div>
      <div class="room-title" id="room-title" hidden></div>
      <div class="room-table" id="room-table"></div>
      <div class="transcript" id="transcript" aria-live="polite"></div>
      <div class="room-dock" id="room-dock" hidden></div>`;
    this.narrEl = this.el.querySelector("#narr-t");
    this.titleEl = this.el.querySelector("#room-title");
    this.tableEl = this.el.querySelector("#room-table");
    this.transcript = this.el.querySelector("#transcript");
    this.dock = this.el.querySelector("#room-dock");
    this.ro = new ResizeObserver(() => this.placeSeats());
    this.ro.observe(this.tableEl);
  }

  // ------------------------------------------------------------------ roles + seats
  setRoles(roles) {
    if (!roles || !Object.keys(roles).length) return;
    const same = JSON.stringify(Object.keys(roles).sort()) === JSON.stringify(Object.keys(this.roles).sort());
    this.roles = roles;
    if (!same || !Object.keys(this.seatEls).length) this.drawSeats();
  }

  roster() {
    const ids = Object.keys(this.roles);
    const core = CORE_ORDER.filter((id) => ids.includes(id)).concat(ids.filter((id) => !CORE_ORDER.includes(id) && !ONCALL_ORDER.includes(id) && this.roles[id].kind !== "oncall"));
    const oncall = ONCALL_ORDER.filter((id) => ids.includes(id)).concat(ids.filter((id) => !CORE_ORDER.includes(id) && !ONCALL_ORDER.includes(id) && this.roles[id].kind === "oncall"));
    return { core, oncall };
  }

  drawSeats() {
    const NS = "http://www.w3.org/2000/svg";
    this.tableEl.innerHTML = "";
    const svg = document.createElementNS(NS, "svg");
    svg.style.cssText = "position:absolute;inset:0;width:100%;height:100%;overflow:visible";
    const label = document.createElementNS(NS, "text");
    label.setAttribute("class", "seat-rowlabel"); label.textContent = "on call";
    svg.appendChild(label);
    this.oncallLabel = label;
    this.seatEls = {};
    const { core, oncall } = this.roster();
    for (const id of [...core, ...oncall]) {
      const r = this.roles[id];
      const g = document.createElementNS(NS, "g");
      g.setAttribute("class", `seat is-away k-${r.kind}`);
      g.dataset.id = id;
      const ini = r.name.slice(0, 2);
      g.innerHTML = `
        <title>${r.name} · ${r.title}</title>
        <g class="seat-pos">
          <circle class="seat-glow" r="18"/>
          <circle class="seat-ring" r="17"/>
          <text class="seat-ini">${ini}</text>
          <g class="seat-badge-g" transform="translate(12,-12)" style="display:none"><title>called in for this meeting</title><svg width="14" height="14" viewBox="0 0 24 24" x="-7" y="-7"><use href="#i-badge" class="seat-badge"/></svg></g>
          <text class="seat-name" y="30">${r.name}</text>
          <text class="seat-title" y="42">${r.title}</text>
          <g class="seat-chip" style="display:none"><rect class="seat-chip-bg" rx="4"/><text class="seat-chip-t" y="0"></text></g>
        </g>`;
      svg.appendChild(g);
      this.seatEls[id] = g;
    }
    this.tableEl.appendChild(svg);
    this.placeSeats();
  }

  placeSeats() {
    if (!Object.keys(this.seatEls).length) return;
    const r = this.tableEl.getBoundingClientRect();
    if (r.width < 10) return;
    const { core, oncall } = this.roster();
    // one row of smaller avatars on laptops (and narrow panes); two rows on a 1080p projector
    const oneRow = r.width < 560 || window.innerHeight < 960;
    this.tableEl.classList.toggle("is-one-row", oneRow);
    const place = (ids, y, x0, x1) => ids.forEach((id, i) => {
      const x = x0 + ((i + 0.5) / ids.length) * (x1 - x0);
      this.seatEls[id].querySelector(".seat-pos").setAttribute("transform", `translate(${x.toFixed(1)},${y})`);
    });
    if (oneRow) {
      place([...core, ...oncall], 20, 8, r.width - 8);
      this.oncallLabel.style.display = "none";
    } else {
      place(core, 18, 0, r.width * 0.98);
      place(oncall, Math.max(72, r.height - 38), r.width * 0.2, r.width * 0.86);
      this.oncallLabel.style.display = "";
      this.oncallLabel.setAttribute("x", (r.width * 0.2 - 10).toFixed(1));
      this.oncallLabel.setAttribute("y", (Math.max(72, r.height - 38) + 4).toFixed(1));
    }
  }

  seat(id) { return this.seatEls[id]; }

  setChip(id, text, cls) {
    const g = this.seat(id); if (!g) return;
    const chip = g.querySelector(".seat-chip");
    if (!text) { chip.style.display = "none"; g.classList.remove("is-veto", "is-lock"); return; }
    chip.style.display = "";
    chip.setAttribute("class", `seat-chip ${cls}`);
    const t = chip.querySelector(".seat-chip-t"); t.textContent = text;
    const w = text.length * 7 + 10;
    chip.setAttribute("transform", `translate(${(-w / 2).toFixed(1)},${this.tableEl.classList.contains("is-one-row") ? 44 : 47})`);
    const bg = chip.querySelector(".seat-chip-bg"); bg.setAttribute("width", w); bg.setAttribute("height", 15); bg.setAttribute("y", -11);
    t.setAttribute("x", w / 2);
    g.classList.add(cls === "c-veto" ? "is-veto" : "is-lock");
  }

  setActiveHires(ids) {
    this.hired = new Set(ids || []);
    for (const id in this.seatEls) {
      const g = this.seatEls[id];
      g.querySelector(".seat-badge-g").style.display = (this.roles[id].kind !== "core" && this.hired.has(id)) ? "" : "none";
    }
  }

  resetSeats(present) {
    for (const id in this.seatEls) {
      const g = this.seatEls[id];
      g.classList.toggle("is-away", !present.has(id));
      g.classList.remove("is-speaking", "is-muted");
      this.setChip(id, null);
    }
  }

  // ------------------------------------------------------------------ narration
  narrate(clauses, reset = false) {
    if (reset) this.story = [];
    for (const c of clauses) if (c && !this.story.includes(c)) this.story.push(c);
    const last = this.story.slice(window.innerWidth < 1300 ? -3 : -4);  // two full lines, no cut
    this.narrEl.innerHTML = last.map((c, i) => `${i ? '<span class="arrow">→</span>' : ""}${esc(cap(c))}`).join("");
  }

  // ------------------------------------------------------------------ states
  setHead(kind, agenda) {
    this.sub.innerHTML = `<b>${esc(kind)}</b>${agenda ? ` · ${esc(agenda)}` : ""}`;
  }

  // Spec 10.5: no meeting; the standing plan continues and the chair writes one line.
  quietDay(bundle) {
    const day = bundle.day, decision = bundle.decision, m = bundle.meeting;
    this.day = day;
    this.inRoom = new Set();
    const core = this.roster().core.filter((id) => this.roles[id].kind === "core" || this.hired.has(id));
    this.resetSeats(new Set(core));
    for (const id of core) this.seat(id).classList.add("is-muted");
    const why = quietWhy(bundle);
    this.setHead("No meeting today", `plan holds · ${why}`);
    const note = (m && m.utterances && m.utterances.length ? m.utterances[0].text : null) || (decision && decision.rationale) || "The plan holds.";
    this.transcript.innerHTML = "";
    this.transcript.appendChild(this.row({ role: "Megan", role_id: "program_manager", title: (this.roles.program_manager || {}).title || "Program Manager", kind: "decision", text: note, kind_word: "CHAIR'S NOTE" }));
    this.seat("program_manager") && this.seat("program_manager").classList.add("is-speaking");
    this.showTitle(decision, "Standing plan", null);
    this.dock.hidden = true; this.dock.innerHTML = "";
    this.narrate([`no meeting today: ${why}`, `Megan keeps the plan: ${shortEffect((decision && decision.effect) || "no change")}`], true);
  }

  open(day, kind, attendeeIds, agenda) {
    this.day = day;
    this.inRoom = new Set(attendeeIds || []);
    this.resetSeats(this.inRoom);
    this.notable = [];
    this.lastSpeaker = null;
    const esc_ = kind === "escalation";
    this.kindLabel = esc_ ? "Escalation · Dana in the room" : "Standup";
    const items = (agenda || []).map(humanAgenda);
    const first = items[0] || "no agenda";
    this.setHead(this.kindLabel, items.length > 1 ? `${first} (+${items.length - 1} more)` : first);
    this.sub.title = items.join("\n");
    this.transcript.innerHTML = "";
    this.titleEl.hidden = true; this.titleEl.innerHTML = "";
    this.dock.hidden = true; this.dock.innerHTML = "";
    const guests = [...this.inRoom].filter((id) => this.roles[id] && this.roles[id].kind !== "core");
    this.narrate(guests.map((id) => `${this.roles[id].name} is called in`));
  }

  utterance(u) {
    const id = u.role_id || u.role;
    for (const k in this.seatEls) this.seatEls[k].classList.remove("is-speaking");
    const g = this.seat(id);
    if (g) {
      g.classList.remove("is-away");
      g.classList.add("is-speaking");
      if (u.kind === "veto") this.setChip(id, "VETOED", "c-veto");
      if (u.kind === "lock") this.setChip(id, "LOCKED", "c-lock");
    }
    if (u.kind === "veto") { this.notable.push(`${u.role} vetoes option ${u.option_label || ""}`.trim()); this.narrate([this.notable[this.notable.length - 1]]); }
    if (u.kind === "lock") { this.notable.push(`${u.role} locks ${lockTarget(u.text)}`); this.narrate([this.notable[this.notable.length - 1]]); }
    for (const r of this.transcript.querySelectorAll(".utt.is-latest")) r.classList.remove("is-latest");
    const row = this.row(u);
    row.classList.add("is-latest");
    this.transcript.appendChild(row);
    this.transcript.scrollTop = this.transcript.scrollHeight;
  }

  row(u) {
    const row = document.createElement("div");
    row.className = `utt k-${u.kind}`;
    const icon = KIND_ICON[u.kind] ? `<svg class="ic"><use href="${KIND_ICON[u.kind]}"/></svg>` : "";
    const word = u.kind_word || KIND_WORD[u.kind] || String(u.kind || "").toUpperCase();
    const opt = u.option_label ? ` · option ${u.option_label}` : "";
    row.innerHTML = `<div class="utt-av">${esc(String(u.role || "").slice(0, 2))}</div>
      <div class="utt-body"><div class="utt-head"><span class="utt-name">${esc(u.role)}</span><span class="utt-role">${esc(u.title || "")}</span><span class="utt-kind k-${u.kind}">${icon}${esc(word)}${esc(opt)}</span></div>
      <div class="utt-text">${esc(u.text)}</div></div>`;
    return row;
  }

  close(options, decision) {
    for (const k in this.seatEls) this.seatEls[k].classList.remove("is-speaking");
    this.showTitle(decision, "Decision", options);
    this.renderDock(options, decision);
    const opt = decision && decision.option_id && options ? (options.find((o) => o.option_id === decision.option_id) || {}).label : null;
    const dec = decision ? (opt ? `option ${opt} chosen` : "decided") : "no decision";
    this.setHead(this.kindLabel, (this.notable.length ? this.notable.join(" · ") + " · " : "") + dec);
    this.narrate([`decided: ${shortEffect(decision ? decision.effect : "no change")}`]);
  }

  // The decision becomes the room's title once the meeting has closed (read order: clock → decision).
  showTitle(decision, kicker, options) {
    if (!decision) { this.titleEl.hidden = true; return; }
    const letters = options ? Object.fromEntries(options.map((o) => [o.option_id, o])) : {};
    const chosen = decision.option_id ? letters[decision.option_id] : null;
    const opt = chosen ? `Option ${chosen.label}: ` : "";
    const effect = decision.effect || (decision.lever_labels || []).join(" · ") || "no change";
    let why = decision.rationale || "";
    if (kicker !== "Decision") why = `${decision.decided_by || "Megan"} keeps yesterday's plan`;
    let override = "";
    if (options && options.length && chosen) {
      const top = [...options].sort((a, b) => (b.score || 0) - (a.score || 0))[0];
      if (top && top.option_id !== chosen.option_id) {
        override = top.vetoed_by ? `${top.label} vetoed by ${top.vetoed_by}, so ${chosen.label}` : `${top.label} scored higher; ${decision.decided_by || "the chair"} chose ${chosen.label}`;
      }
    }
    this.titleEl.hidden = false;
    this.titleEl.innerHTML = `<span class="decision-k">${esc(kicker)}</span><div class="decision-body"><div class="decision-t">${esc(cap(opt + effect))}</div>
      <div class="decision-r">${override ? `<span class="decision-why">${esc(override)}.</span> ` : ""}<span class="decision-why-t">${esc(why)}</span></div></div>`;
    const r = this.titleEl.querySelector(".decision-r");
    requestAnimationFrame(() => { if (r.scrollHeight > r.clientHeight + 2) { const b = document.createElement("button"); b.className = "decision-more"; b.textContent = "show why"; b.addEventListener("click", () => { r.classList.add("is-open"); b.remove(); }); r.after(b); } });
  }

  renderDock(options, decision) {
    this.dock.innerHTML = "";
    if (!options || !options.length) { this.dock.hidden = true; return; }
    this.dock.hidden = false;
    const wrap = document.createElement("div");
    wrap.className = "options";
    const pick = decision && decision.option_id;
    for (const o of options) {
      const d = document.createElement("div");
      const isPick = pick === o.option_id;
      d.className = `opt ${isPick ? "is-pick" : ""} ${o.vetoed_by ? "is-veto" : ""} ${o.feasible === false ? "is-infeasible" : ""}`;
      const status = o.vetoed_by ? "vetoed" : o.feasible === false ? "infeasible" : isPick ? "chosen" : "feasible";
      const lock = o.locks ? `<svg class="ic opt-lock" title="locks SKUs in place"><use href="#i-lock"/></svg>` : "";
      d.innerHTML = `<span class="opt-head"><span class="opt-l">${esc(o.label)}</span><span class="opt-by">${esc(o.proposed_by)}</span>
        <span class="opt-score" title="scorecard score out of 1"><i class="opt-bar"><b style="transform:scaleX(${Math.min(1, Math.max(0, o.score || 0)).toFixed(3)})"></b></i>${o.score != null ? o.score.toFixed(2) : "—"} / 1</span>
        <span class="opt-status s-${status}">${esc(status)}</span>${lock}</span>
        <span class="opt-levers">${esc(o.effect || (o.lever_labels || []).join(" · "))}</span>
        ${o.vetoed_by ? `<span class="opt-stamp" aria-hidden="true">VETOED by ${esc(o.vetoed_by)}</span>` : ""}
        <span class="opt-detail" hidden>${esc((o.lever_labels || []).join(" · "))}${o.evidence_id ? ` · evidence ${esc(o.evidence_id)}` : ""}</span>`;
      d.title = `${o.label} · ${o.proposed_by} · ${(o.lever_labels || []).join("; ")} · click for details`;
      d.addEventListener("click", () => { d.classList.toggle("is-open"); const det = d.querySelector(".opt-detail"); det.hidden = !d.classList.contains("is-open"); });
      wrap.appendChild(d);
    }
    this.dock.appendChild(wrap);
  }

  // Render a whole day at once (jump / resume without streaming the meeting).
  renderDay(bundle) {
    if (bundle.roles) this.setRoles(bundle.roles);
    this.setActiveHires(bundle.active_hire_ids);
    const m = bundle.meeting;
    if (!m || m.kind === "quiet") { this.quietDay(bundle); return; }
    this.narrate((bundle.events || []).map((e) => e.plain), true);
    this.open(bundle.day, m.kind, m.attendee_ids, m.agenda);
    for (const u of m.utterances) this.utterance(u);
    this.close(m.options, bundle.decision);
    this.transcript.scrollTop = this.transcript.scrollHeight;
  }
}

// Agenda strings come from the org as "kind: detail" or prose; make the kind readable.
function humanAgenda(s) {
  return String(s ?? "").replace(/^([a-z_]+):/, (m, k) => k.replace(/_/g, " ") + ":");
}
// First effect clause only, for the narration chain ("aisle 7 closed for 5 days")
function shortEffect(e) {
  const first = String(e || "").split(";")[0].trim();
  const n = String(e || "").split(";").length;
  return n > 1 ? `${first} (+${n - 1} more)` : first;
}
function cap(s) { s = String(s ?? ""); return s.charAt(0).toUpperCase() + s.slice(1); }
function lockTarget(text) {
  const m = /\b([A-Z]\d{3})\b/.exec(text || "");
  return m ? `${m[1]}'s SKUs in place` : "SKUs in place";
}
// One short reason line for a day without a meeting, derived from the day bundle (not hardcoded).
function quietWhy(b) {
  const parts = [];
  parts.push((b.events || []).length ? `${b.events.length} event${b.events.length > 1 ? "s" : ""} logged` : "no new events");
  const failed = (b.checks || []).filter((c) => c.scope === "daily" && c.passed === false);
  parts.push(failed.length ? `${failed.length} KPI${failed.length > 1 ? "s" : ""} off target` : "KPIs on target");
  if ((b.drift || []).length) parts.push("forecast drift flagged");
  return parts.join(" · ");
}
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])); }
