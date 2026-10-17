// Floor canvas: plan view of the warehouse (spec 7.1), animated from HOURLY.md payloads.
// Static geometry is hardcoded here; the payload only carries what changes hour to hour.
// Dots are sampled per pool and interpolated within the hour: a faithful picture of pool
// loads and queues, not a per-person simulation.

const N_AISLES = 28;
const N_ZONES = 6;
const N_DOORS = 6;
const PM_AFTER = 14; // PM sits between aisle 14 and 15

export class Floor {
  constructor(canvas) {
    this.c = canvas;
    this.ctx = canvas.getContext("2d");
    this.prev = null;
    this.cur = null;
    this.t0 = performance.now();
    this.dur = 250; // ms per hour tick (set from speed)
    this.day = null;
    this.hour = null;
    this.agents = this._seedAgents();
    this.totes = Array.from({ length: 14 }, (_, i) => ({ p: i / 14, lane: i % 2 }));
    this.moveAnims = [];
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(canvas);
    this.resize();
    this._tick = this._tick.bind(this);
    requestAnimationFrame(this._tick);
    this.pal = {};
    this.readPalette();
  }

  readPalette() {
    const cs = getComputedStyle(document.documentElement);
    const g = (k) => cs.getPropertyValue(k).trim();
    this.pal = {
      floor: g("--fl-floor"), rack: g("--fl-rack"), rackLine: g("--fl-rack-line"), cool: g("--fl-cool"),
      warm: g("--fl-warm"), hot: g("--fl-hot"), ink: g("--fl-ink"), muted: g("--fl-muted"),
      people: g("--fl-people"), truck: g("--fl-truck"), tote: g("--fl-tote"), pm: g("--fl-pm"),
      night: g("--fl-night"), move: g("--fl-move"), teal: g("--teal"), red: g("--red"), amber: g("--amber"),
      surface: g("--surface-2"), text3: g("--text-3"),
    };
  }

  resize() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const r = this.c.getBoundingClientRect();
    this.w = Math.max(10, r.width);
    this.h = Math.max(10, r.height);
    this.c.width = Math.round(this.w * dpr);
    this.c.height = Math.round(this.h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.layout();
  }

  layout() {
    const { w, h } = this;
    const pad = Math.max(10, w * 0.02);
    const top = 26;
    const rackH = h * 0.5;
    const cols = N_AISLES + 2.6; // PM takes 2.6 aisle widths
    const unit = (w - pad * 2) / cols;
    this.unit = unit;
    const aisles = [];
    let x = pad;
    for (let a = 1; a <= N_AISLES; a++) {
      aisles.push({ a, x, w: unit, y: top, h: rackH });
      x += unit;
      if (a === PM_AFTER) {
        this.pm = { x, w: unit * 2.6, y: top, h: rackH };
        x += unit * 2.6;
      }
    }
    this.aisles = aisles;
    const frontY = top + rackH + 14;
    const frontH = h * 0.2;
    this.staging = { x: pad, y: frontY, w: w * 0.2, h: frontH };
    this.cons = { x: w * 0.5 - w * 0.17, y: frontY, w: w * 0.34, h: frontH };
    this.night = { x: w - pad - w * 0.2, y: frontY, w: w * 0.2, h: frontH };
    const dockY = frontY + frontH + 12;
    this.dock = { x: pad, y: dockY, w: w - pad * 2, h: Math.max(28, h - dockY - 8) };
  }

  setSpeed(hoursPerSec) { this.dur = 1000 / hoursPerSec; }

  setHour(day, h) {
    this.prev = this.cur || h;
    this.cur = h;
    this.day = day;
    this.hour = h.hour;
    this.t0 = performance.now();
    if (h.moves && h.moves.sample && h.moves.sample.length) {
      this.moveAnims = h.moves.sample.map((m, i) => ({ ...m, start: this.t0 + i * (this.dur / h.moves.sample.length) * 0.8 }));
    } else {
      this.moveAnims = [];
    }
  }

  clear() { this.prev = this.cur = null; this.moveAnims = []; }

  _seedAgents() {
    // persistent jitter seeds for dots so they drift rather than teleport
    const rnd = (n) => Array.from({ length: n }, () => ({ s: Math.random() * 1000, v: 0.4 + Math.random() * 0.8 }));
    return { aisle: Array.from({ length: N_AISLES }, () => rnd(6)), pm: Array.from({ length: N_ZONES }, () => rnd(5)),
      cons: rnd(12), load: rnd(8), fork: rnd(12), stage: rnd(6) };
  }

  _tick(now) {
    this.draw(now);
    requestAnimationFrame(this._tick);
  }

  // ------------------------------------------------------------------ helpers
  lerp(a, b, t) { return a + (b - a) * t; }
  frac(now) { return Math.min(1, (now - this.t0) / this.dur); }
  val(key, now, fallback = 0) {
    if (!this.cur) return fallback;
    const a = this.prev ? this.prev[key] : this.cur[key];
    const b = this.cur[key];
    if (typeof b !== "number") return b ?? fallback;
    return this.lerp(typeof a === "number" ? a : b, b, this.frac(now));
  }
  arr(key, i, now) {
    if (!this.cur) return 0;
    const b = (this.cur[key] || [])[i] ?? 0;
    const a = this.prev ? ((this.prev[key] || [])[i] ?? b) : b;
    return this.lerp(a, b, this.frac(now));
  }
  loadColor(v) {
    // 0 → cool, 0.75 → warm, 1.1+ → hot
    const p = this.pal;
    if (v <= 0.02) return p.rack;
    if (v < 0.75) return mix(p.cool, p.warm, Math.pow(v / 0.75, 1.6));
    if (v < 1.1) return mix(p.warm, p.hot, (v - 0.75) / 0.35);
    return p.hot;
  }

  // ------------------------------------------------------------------ draw
  draw(now) {
    const { ctx, w, h, pal } = this;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = pal.floor;
    ctx.fillRect(0, 0, w, h);
    if (!this.cur) { this.drawEmpty(); return; }
    const cur = this.cur;
    const night = cur.phase === "night";
    const t = this.frac(now);

    this.drawAisles(now);
    this.drawPM(now);
    this.drawStaging(now);
    this.drawConsolidator(now);
    this.drawNightPanel(now);
    this.drawDock(now);

    if (night) {
      ctx.fillStyle = pal.night;
      ctx.fillRect(0, 0, w, h);
      this.drawMoves(now);
    }
    this.drawCaption(now);
  }

  drawEmpty() {
    const { ctx, w, h, pal } = this;
    ctx.fillStyle = pal.muted;
    ctx.font = "500 13px Schibsted Grotesk, system-ui";
    ctx.textAlign = "center";
    ctx.fillText("Waiting for the first hour…", w / 2, h / 2);
  }

  drawAisles(now) {
    const { ctx, pal } = this;
    const frozen = new Set(this.cur.frozen_aisles || []);
    const nm = this.cur.near_miss_aisle;
    for (const al of this.aisles) {
      const i = al.a - 1;
      const load = this.arr("aisles", i, now);
      const people = Math.round(this.arr("aisle_people", i, now));
      const risk = this.arr("safety_risk", i, now);
      const x = al.x + 1.5, wd = al.w - 3;
      // rack faces (two sides), lane between
      ctx.fillStyle = pal.rack;
      roundRect(ctx, x, al.y, wd, al.h, 3);
      ctx.fill();
      const laneW = Math.max(3, wd * 0.46);
      const lx = x + (wd - laneW) / 2;
      ctx.fillStyle = frozen.has(al.a) ? pal.rack : this.loadColor(load);
      roundRect(ctx, lx, al.y + 3, laneW, al.h - 6, 2);
      ctx.fill();
      if (load > 0.95 && !frozen.has(al.a)) {
        ctx.save();
        ctx.shadowColor = pal.hot; ctx.shadowBlur = 10;
        ctx.fillStyle = "transparent";
        roundRect(ctx, lx, al.y + 3, laneW, al.h - 6, 2);
        ctx.fillStyle = rgba(pal.hot, 0.25);
        ctx.fill();
        ctx.restore();
      }
      // bay ticks
      ctx.strokeStyle = pal.rackLine; ctx.lineWidth = 1;
      ctx.beginPath();
      for (let b = 1; b < 6; b++) {
        const yy = al.y + (al.h * b) / 6;
        ctx.moveTo(x, yy); ctx.lineTo(x + 2.5, yy);
        ctx.moveTo(x + wd - 2.5, yy); ctx.lineTo(x + wd, yy);
      }
      ctx.stroke();
      if (frozen.has(al.a)) {
        hatch(ctx, x, al.y, wd, al.h, pal.muted);
        ctx.strokeStyle = pal.red; ctx.lineWidth = 1.5;
        roundRect(ctx, x, al.y, wd, al.h, 3); ctx.stroke();
      }
      // people + trucks in the lane
      const seeds = this.agents.aisle[i];
      for (let p = 0; p < Math.min(people, 6); p++) {
        const s = seeds[p];
        const ph = (now / 1000) * s.v * 0.25 + s.s;
        const py = al.y + 8 + ((Math.sin(ph) + 1) / 2) * (al.h - 16);
        const px = lx + laneW / 2 + Math.cos(ph * 1.7) * laneW * 0.22;
        if (p < 2 && load > 0.35) {
          // forklift: small rect with a lamp
          ctx.fillStyle = pal.truck;
          ctx.fillRect(px - 2.5, py - 4, 5, 8);
        } else {
          ctx.fillStyle = pal.people;
          ctx.beginPath(); ctx.arc(px, py, 1.9, 0, Math.PI * 2); ctx.fill();
        }
      }
      if (risk > 1.0 || nm === al.a) {
        ctx.strokeStyle = pal.red; ctx.lineWidth = 2;
        const pulse = 0.5 + 0.5 * Math.sin(now / 160);
        ctx.globalAlpha = 0.4 + 0.6 * pulse;
        roundRect(ctx, x - 1, al.y - 1, wd + 2, al.h + 2, 4); ctx.stroke();
        ctx.globalAlpha = 1;
      }
      // number
      ctx.fillStyle = frozen.has(al.a) ? pal.red : pal.muted;
      ctx.font = `500 ${Math.max(10, Math.min(12, al.w * 0.6))}px JetBrains Mono, monospace`;
      ctx.textAlign = "center";
      ctx.fillText(String(al.a), al.x + al.w / 2, al.y + al.h + 11);
    }
  }

  drawPM(now) {
    const { ctx, pal, pm } = this;
    const x = pm.x + 3, wd = pm.w - 6;
    ctx.fillStyle = pal.pm;
    roundRect(ctx, x, pm.y, wd, pm.h, 5); ctx.fill();
    ctx.strokeStyle = pal.rackLine; ctx.lineWidth = 1;
    roundRect(ctx, x, pm.y, wd, pm.h, 5); ctx.stroke();
    const zh = (pm.h - 10) / N_ZONES;
    const convX = x + wd / 2;
    const pools = this.cur.pools || {};
    const pmBusy = (pools.pm && pools.pm.busy) || 0;
    const night = this.cur.phase === "night";
    for (let z = 0; z < N_ZONES; z++) {
      const zi = N_ZONES - 1 - z; // zone 1 nearest the consolidator (bottom)
      const zy = pm.y + 5 + z * zh;
      const load = this.arr("pm_zones", zi, now);
      const col = this.loadColor(load);
      // shelving both sides of the conveyor
      const shelfW = wd * 0.34;
      ctx.fillStyle = col;
      roundRect(ctx, x + 4, zy + 2, shelfW, zh - 4, 2); ctx.fill();
      roundRect(ctx, x + wd - 4 - shelfW, zy + 2, shelfW, zh - 4, 2); ctx.fill();
      if (load > 1.0) {
        ctx.save(); ctx.shadowColor = pal.hot; ctx.shadowBlur = 14; ctx.fillStyle = rgba(pal.hot, 0.35);
        roundRect(ctx, x + 2, zy, wd - 4, zh, 3); ctx.fill(); ctx.restore();
      }
      if (load <= 0.02 && !night) hatch(ctx, x + 4, zy + 2, wd - 8, zh - 4, pal.muted);
      // pickers
      const nz = Math.round((pmBusy / Math.max(1, N_ZONES)) * (0.6 + load));
      const seeds = this.agents.pm[zi];
      for (let p = 0; p < Math.min(5, nz); p++) {
        const s = seeds[p];
        const ph = (now / 1000) * s.v * 0.3 + s.s;
        const side = p % 2 ? 1 : -1;
        const px = convX + side * (wd * 0.17 + Math.cos(ph) * 3);
        const py = zy + 6 + ((Math.sin(ph * 1.3) + 1) / 2) * (zh - 12);
        ctx.fillStyle = pal.people;
        ctx.beginPath(); ctx.arc(px, py, 1.9, 0, Math.PI * 2); ctx.fill();
      }
    }
    // conveyor: runs from the top zone down to the consolidator
    const convTop = pm.y + 6;
    const convBot = this.cons.y;
    ctx.strokeStyle = pal.rackLine; ctx.lineWidth = Math.max(4, wd * 0.14);
    ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(convX, convTop); ctx.lineTo(convX, convBot - 2); ctx.stroke();
    ctx.strokeStyle = rgba(pal.ink, 0.15); ctx.lineWidth = 1;
    ctx.setLineDash([3, 4]); ctx.lineDashOffset = -(now / 40) % 7;
    ctx.beginPath(); ctx.moveTo(convX, convTop); ctx.lineTo(convX, convBot - 2); ctx.stroke();
    ctx.setLineDash([]);
    // zone labels, drawn after the conveyor so nothing covers them: "Z6" left, load % right.
    // Measured against the cell; the zone id is dropped first, then the whole label, when too narrow.
    // zone load as "% of capacity", 11px, drawn after the conveyor, dropped only if the cell is too narrow
    ctx.font = "700 11px JetBrains Mono, monospace";
    if (wd - 8 >= ctx.measureText("100%").width) {
      for (let z = 0; z < N_ZONES; z++) {
        const zi = N_ZONES - 1 - z;
        const zy = pm.y + 5 + z * zh;
        const load = this.arr("pm_zones", zi, now);
        const ty = zy + Math.min(13, zh / 2 + 4);
        ctx.lineWidth = 3; ctx.strokeStyle = pal.floor; ctx.lineJoin = "round";
        const pct = `${Math.round(load * 100)}%`;
        ctx.textAlign = "right"; ctx.strokeText(pct, x + wd - 5, ty);
        ctx.fillStyle = load > 1 ? pal.hot : pal.ink; ctx.fillText(pct, x + wd - 5, ty);
      }
    }
    // totes riding down
    const queue = (pools.consolidation && pools.consolidation.queue) || 0;
    const nT = night ? 0 : Math.min(14, 3 + Math.round(queue / 20));
    const speed = 0.00018 * (this.dur < 200 ? 2 : 1);
    for (let i = 0; i < nT; i++) {
      const tt = this.totes[i];
      const p = (tt.p + now * speed) % 1;
      const y = convTop + p * (convBot - 2 - convTop);
      ctx.fillStyle = pal.tote;
      ctx.fillRect(convX - 3, y - 2.5, 6, 5);
    }
    ctx.fillStyle = pal.muted;
    ctx.font = "700 8.5px Schibsted Grotesk, system-ui"; ctx.textAlign = "center";
    ctx.fillText("PICK MODULE", pm.x + pm.w / 2, pm.y + pm.h + 11);
  }

  drawStaging(now) {
    const { ctx, pal, staging: s } = this;
    const occ = this.val("staging_occupancy", now) / 100;
    panel(ctx, s, pal);
    label(ctx, "STAGING · buffer for slot moves", s.x + 8, s.y + 13, pal.muted);
    const barY = s.y + 22, barH = Math.max(10, s.h - 44);
    ctx.fillStyle = pal.rack;
    roundRect(ctx, s.x + 8, barY, s.w - 16, barH, 3); ctx.fill();
    const col = occ > 0.9 ? pal.hot : occ > 0.7 ? pal.warm : pal.cool;
    ctx.fillStyle = col;
    roundRect(ctx, s.x + 8, barY, (s.w - 16) * Math.min(1, occ), barH, 3); ctx.fill();
    // slot grid overlay: 150 slots in 10 columns
    ctx.strokeStyle = rgba(pal.floor, 0.5); ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 1; i < 10; i++) { const xx = s.x + 8 + ((s.w - 16) * i) / 10; ctx.moveTo(xx, barY); ctx.lineTo(xx, barY + barH); }
    ctx.stroke();
    ctx.fillStyle = occ > 0.9 ? pal.hot : pal.ink; ctx.font = "700 15px Bricolage Grotesque, Schibsted Grotesk, system-ui"; ctx.textAlign = "right";
    ctx.fillText(`${Math.round(occ * 100)}% full`, s.x + s.w - 8, s.y + s.h - 7);
    ctx.fillStyle = pal.muted; ctx.font = "500 11px Schibsted Grotesk, system-ui"; ctx.textAlign = "left";
    ctx.fillText("150 slots", s.x + 8, s.y + s.h - 7);
  }

  drawConsolidator(now) {
    const { ctx, pal, cons: k } = this;
    const open = this.val("consolidator_open", now);
    const jam = !!this.cur.jam;
    const pools = this.cur.pools || {};
    if (jam) {
      ctx.save(); ctx.shadowColor = pal.hot; ctx.shadowBlur = 24 + 10 * Math.sin(now / 150);
      ctx.fillStyle = rgba(pal.hot, 0.25); roundRect(ctx, k.x, k.y, k.w, k.h, 6); ctx.fill(); ctx.restore();
    }
    panel(ctx, k, pal, jam ? pal.hot : null);
    label(ctx, "CONSOLIDATOR · where an order's totes and pallets meet", k.x + 8, k.y + 13, pal.muted);
    // open-orders meter: 0..400 capacity, red past it
    const mx = k.x + 8, my = k.y + 22, mw = k.w - 16, mh = Math.max(8, Math.min(14, k.h * 0.2));
    ctx.fillStyle = pal.rack; roundRect(ctx, mx, my, mw, mh, 3); ctx.fill();
    const frac = Math.min(1, open / 480);
    ctx.fillStyle = open > 400 ? pal.hot : open > 300 ? pal.warm : pal.cool;
    roundRect(ctx, mx, my, mw * frac, mh, 3); ctx.fill();
    const capX = mx + mw * (400 / 480);
    ctx.strokeStyle = pal.ink; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(capX, my - 2); ctx.lineTo(capX, my + mh + 2); ctx.stroke();
    ctx.fillStyle = jam ? pal.hot : pal.ink; ctx.font = "700 18px Bricolage Grotesque, Schibsted Grotesk, system-ui"; ctx.textAlign = "left";
    ctx.fillText(`${Math.round(open)}`, mx, my + mh + 19);
    const wOpen = ctx.measureText(`${Math.round(open)}`).width;
    ctx.font = "500 11px Schibsted Grotesk, system-ui"; ctx.fillStyle = pal.muted;
    ctx.fillText("open orders waiting", mx + wOpen + 6, my + mh + 19);
    ctx.textAlign = "right";
    ctx.fillStyle = jam ? pal.hot : pal.muted;
    ctx.font = jam ? "700 12px Schibsted Grotesk, system-ui" : "500 11px Schibsted Grotesk, system-ui";
    ctx.fillText(jam ? "JAMMED · over the 400 limit" : "jams above 400", mx + mw, my + mh + 19);
    // people: consolidation + loading pools busy
    const busy = ((pools.consolidation && pools.consolidation.busy) || 0);
    const seeds = this.agents.cons;
    const areaY = my + mh + 24, areaH = Math.max(6, k.y + k.h - areaY - 6);
    for (let p = 0; p < Math.min(12, busy); p++) {
      const s = seeds[p];
      const ph = (now / 1000) * s.v * 0.3 + s.s;
      const px = mx + 6 + ((Math.cos(ph) + 1) / 2) * (mw - 12);
      const py = areaY + ((Math.sin(ph * 1.4) + 1) / 2) * areaH;
      ctx.fillStyle = pal.people;
      ctx.beginPath(); ctx.arc(px, py, 1.9, 0, Math.PI * 2); ctx.fill();
    }
  }

  drawNightPanel(now) {
    const { ctx, pal, night: n } = this;
    const cur = this.cur;
    panel(ctx, n, pal);
    const isNight = cur.phase === "night";
    label(ctx, isNight ? "SLOT MOVES TONIGHT" : "STOCK · REPLENISHMENT", n.x + 8, n.y + 13, pal.muted);
    ctx.textAlign = "left";
    if (isNight) {
      const done = (cur.moves && cur.moves.done) || 0;
      const movers = (cur.pools && cur.pools.night_move && cur.pools.night_move.busy) || 0;
      ctx.fillStyle = pal.move; ctx.font = "700 20px Bricolage Grotesque, Schibsted Grotesk, system-ui";
      ctx.fillText(`${done}`, n.x + 8, n.y + 40);
      ctx.fillStyle = pal.muted; ctx.font = "500 11px Schibsted Grotesk, system-ui";
      ctx.fillText(`slot moves this hour · ${movers} movers`, n.x + 8, n.y + 55);
    } else {
      const so = Math.round(this.val("stockouts", now));
      const mp = Math.round(this.val("mispicks", now));
      const line = (v, txt, y, hot) => {
        ctx.textAlign = "left";
        ctx.fillStyle = hot ? pal.hot : pal.ink; ctx.font = "700 17px Bricolage Grotesque, Schibsted Grotesk, system-ui";
        ctx.fillText(`${v}`, n.x + 8, y);
        const wv = ctx.measureText(`${v}`).width;
        ctx.fillStyle = pal.muted; ctx.font = "500 11px Schibsted Grotesk, system-ui";
        ctx.fillText(txt, n.x + 8 + wv + 6, y);
      };
      line(so, "lines waiting for stock", n.y + 36, so > 20);
      line(mp, "mis-picks this hour", n.y + 58, mp > 12);
    }
  }

  drawDock(now) {
    const { ctx, pal, dock: d } = this;
    const tr = this.cur.trucks || {};
    panel(ctx, d, pal);
    label(ctx, "DOCK · 6 doors, lit = truck loading", d.x + 8, d.y + 13, pal.muted);
    const doorW = Math.min(64, (d.w * 0.5) / N_DOORS);
    const dx0 = d.x + d.w * 0.5 - (doorW * N_DOORS) / 2;
    const doorY = d.y + 6, doorH = Math.max(10, d.h - 12);
    const loadBusy = (this.cur.pools && this.cur.pools.loading && this.cur.pools.loading.busy) || 0;
    for (let i = 0; i < N_DOORS; i++) {
      const x = dx0 + i * doorW + 4;
      const active = i < loadBusy;
      ctx.fillStyle = active ? rgba(pal.truck, 0.25) : pal.rack;
      roundRect(ctx, x, doorY, doorW - 8, doorH, 3); ctx.fill();
      ctx.strokeStyle = active ? pal.truck : pal.rackLine; ctx.lineWidth = 1;
      roundRect(ctx, x, doorY, doorW - 8, doorH, 3); ctx.stroke();
      if (active) {
        // truck silhouette
        ctx.fillStyle = pal.truck;
        const tw = Math.min(doorW - 18, 30), th = Math.max(5, doorH * 0.4);
        ctx.fillRect(x + (doorW - 8 - tw) / 2, doorY + (doorH - th) / 2, tw, th);
        ctx.fillRect(x + (doorW - 8 - tw) / 2 + tw, doorY + (doorH - th) / 2 + th * 0.3, 5, th * 0.7);
      }
      ctx.fillStyle = pal.muted; ctx.font = "500 9px JetBrains Mono, monospace"; ctx.textAlign = "center";
      ctx.fillText(`D${i + 1}`, x + (doorW - 8) / 2, doorY + doorH - 3);
    }
    // left: loaded + late; right: next cutoff clock
    ctx.textAlign = "left";
    ctx.fillStyle = pal.ink; ctx.font = "700 17px Bricolage Grotesque, Schibsted Grotesk, system-ui";
    ctx.fillText(`${tr.loaded ?? 0}`, d.x + 8, d.y + 36);
    const wl = ctx.measureText(`${tr.loaded ?? 0}`).width;
    ctx.fillStyle = pal.muted; ctx.font = "500 11px Schibsted Grotesk, system-ui";
    ctx.fillText("trucks loaded today", d.x + 8 + wl + 6, d.y + 36);
    if (tr.late) {
      ctx.fillStyle = pal.hot; ctx.font = "700 12px Schibsted Grotesk, system-ui";
      ctx.fillText(`${tr.late} missed a cutoff`, d.x + 8, d.y + 52);
    }
    ctx.textAlign = "right";
    if (tr.next_cutoff != null) {
      const hrs = this.cur.hour;
      const mins = tr.next_cutoff === 21 ? 30 : 0;
      const left = Math.max(0, (tr.next_cutoff * 60 + mins) - (hrs * 60 + this.frac(now) * 60));
      const urgent = left <= 60;
      ctx.fillStyle = urgent ? pal.hot : pal.ink;
      ctx.font = "700 15px Bricolage Grotesque, Schibsted Grotesk, system-ui";
      ctx.fillText(`${String(tr.next_cutoff).padStart(2, "0")}:${mins ? "30" : "00"}`, d.x + d.w - 8, d.y + 34);
      ctx.fillStyle = pal.muted; ctx.font = "500 11px Schibsted Grotesk, system-ui";
      ctx.fillText(`next truck cutoff in ${Math.floor(left / 60)}h ${String(Math.round(left % 60)).padStart(2, "0")}m`, d.x + d.w - 8, d.y + 50);
    } else {
      ctx.fillStyle = pal.muted; ctx.font = "500 11px Schibsted Grotesk, system-ui";
      ctx.fillText("no more truck cutoffs today", d.x + d.w - 8, d.y + 34);
    }
  }

  drawMoves(now) {
    const { ctx, pal } = this;
    for (const m of this.moveAnims) {
      const p = Math.min(1, Math.max(0, (now - m.start) / (this.dur * 0.9)));
      if (p <= 0) continue;
      const from = this.locPoint(m.from), to = this.locPoint(m.to);
      if (!from || !to) continue;
      const e = 1 - Math.pow(1 - p, 3);
      const x = this.lerp(from.x, to.x, e), y = this.lerp(from.y, to.y, e) - Math.sin(p * Math.PI) * 18;
      ctx.strokeStyle = rgba(pal.move, 0.35); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(from.x, from.y);
      ctx.quadraticCurveTo((from.x + to.x) / 2, Math.min(from.y, to.y) - 24, to.x, to.y); ctx.stroke();
      ctx.fillStyle = pal.move;
      ctx.save(); ctx.shadowColor = pal.move; ctx.shadowBlur = 8;
      roundRect(ctx, x - 5, y - 3.5, 10, 7, 2); ctx.fill(); ctx.restore();
      if (p < 0.98) {
        ctx.fillStyle = pal.ink; ctx.font = "500 9px JetBrains Mono, monospace"; ctx.textAlign = "center";
        ctx.fillText(m.sku, x, y - 7);
      }
    }
  }

  locPoint(loc) {
    if (!loc) return null;
    let m;
    if ((m = /^A(\d\d)-(\d\d)-(\d)$/.exec(loc))) {
      const a = this.aisles[+m[1] - 1]; if (!a) return null;
      return { x: a.x + a.w / 2, y: a.y + (a.h * (+m[2] - 0.5)) / 30 };
    }
    if ((m = /^PM(\d)-(\d\d\d)$/.exec(loc))) {
      const z = +m[1] - 1; const zh = (this.pm.h - 10) / N_ZONES;
      const zy = this.pm.y + 5 + (N_ZONES - 1 - z) * zh;
      const side = +m[2] % 2 ? -1 : 1;
      return { x: this.pm.x + this.pm.w / 2 + side * this.pm.w * 0.25, y: zy + zh / 2 };
    }
    if ((m = /^ST-(\d\d\d)$/.exec(loc))) {
      const s = this.staging; return { x: s.x + 8 + ((+m[1] % 10) + 0.5) * ((s.w - 16) / 10), y: s.y + s.h / 2 };
    }
    return null;
  }

  drawCaption(now) {
    const { ctx, pal, w } = this;
    const cur = this.cur;
    const phase = cur.phase === "day" ? "DAY SHIFT" : cur.phase === "swing" ? "SWING SHIFT" : "NIGHT · SLOT MOVES";
    ctx.textAlign = "left";
    ctx.fillStyle = cur.phase === "night" ? pal.move : pal.muted;
    ctx.font = "700 11px Schibsted Grotesk, system-ui";
    const pools = cur.pools || {};
    const build = (names) => {
      const parts = [];
      for (const [k, lbl] of names) { const p = pools[k]; if (p && p.available) parts.push(`${lbl} ${p.busy} of ${p.available} busy`); }
      return parts.join("  ·  ");
    };
    const long = build([["pm", "Pick Module pickers"], ["forklift", "forklifts"], ["consolidation", "consolidator"], ["loading", "loaders"], ["night_move", "movers"], ["temp", "temps"]]);
    const short = build([["pm", "Pick Module"], ["forklift", "forklifts"], ["consolidation", "consolidator"], ["night_move", "movers"], ["temp", "temps"]]);
    ctx.font = "500 11px JetBrains Mono, monospace";
    const poolsW = ctx.measureText(long).width;
    ctx.font = "700 11px Schibsted Grotesk, system-ui";
    const phaseW = ctx.measureText(phase).width;
    const poolsTxt = phaseW + poolsW + 40 > w ? short : long;
    ctx.fillText(phase, 12, 15);
    ctx.textAlign = "right"; ctx.fillStyle = pal.muted; ctx.font = "500 11px JetBrains Mono, monospace";
    ctx.fillText(poolsTxt, w - 12, 15);
  }
}

// ---------------------------------------------------------------------- primitives
function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y); ctx.lineTo(x + w - rr, y); ctx.quadraticCurveTo(x + w, y, x + w, y + rr);
  ctx.lineTo(x + w, y + h - rr); ctx.quadraticCurveTo(x + w, y + h, x + w - rr, y + h);
  ctx.lineTo(x + rr, y + h); ctx.quadraticCurveTo(x, y + h, x, y + h - rr);
  ctx.lineTo(x, y + rr); ctx.quadraticCurveTo(x, y, x + rr, y); ctx.closePath();
}
function panel(ctx, r, pal, stroke) {
  ctx.fillStyle = pal.surface; roundRect(ctx, r.x, r.y, r.w, r.h, 6); ctx.fill();
  ctx.strokeStyle = stroke || pal.rackLine; ctx.lineWidth = stroke ? 1.5 : 1; roundRect(ctx, r.x, r.y, r.w, r.h, 6); ctx.stroke();
}
function label(ctx, text, x, y, col) {
  ctx.fillStyle = col; ctx.font = "700 10.5px Schibsted Grotesk, system-ui"; ctx.textAlign = "left"; ctx.fillText(text, x, y);
}
function hatch(ctx, x, y, w, h, col) {
  ctx.save(); ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip();
  ctx.strokeStyle = col; ctx.lineWidth = 1; ctx.globalAlpha = 0.7;
  ctx.beginPath();
  for (let i = -h; i < w + h; i += 6) { ctx.moveTo(x + i, y); ctx.lineTo(x + i + h, y + h); }
  ctx.stroke(); ctx.restore();
}
function hexToRgb(hex) {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex.trim());
  if (!m) return [128, 128, 128];
  return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
}
function mix(a, b, t) {
  const A = hexToRgb(a), B = hexToRgb(b);
  return `rgb(${Math.round(A[0] + (B[0] - A[0]) * t)},${Math.round(A[1] + (B[1] - A[1]) * t)},${Math.round(A[2] + (B[2] - A[2]) * t)})`;
}
function rgba(hex, a) { const c = hexToRgb(hex); return `rgba(${c[0]},${c[1]},${c[2]},${a})`; }
