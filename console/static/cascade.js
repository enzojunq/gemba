// Cascade trace: a small left-to-right graph from cascade_edges. Nodes ordered by day, one lane per
// node type, edges as curves with arrowheads. Click a node to re-centre on it.

const NS = "http://www.w3.org/2000/svg";
const LANES = ["evt", "mtg", "dec", "con", "kpi"];
const LANE_NAME = { evt: "event", mtg: "meeting", dec: "decision", con: "lock / constraint", kpi: "KPI" };

export class Cascade {
  constructor(drawer, body, sub, { onPick, onJump } = {}) {
    this.drawer = drawer; this.body = body; this.sub = sub;
    this.onPick = onPick; this.onJump = onJump;
  }

  show(data, title) {
    this.drawer.hidden = false;
    this.render(data, title);
  }
  hide() { this.drawer.hidden = true; }

  render(data, title) {
    const nodes = data.nodes || [], edges = data.edges || [];
    this.sub.textContent = title ? `${title} · ${nodes.length} linked steps` : `${nodes.length} linked steps`;
    const sent = document.getElementById("cascade-sentence");
    if (sent) sent.innerHTML = data.sentence ? data.sentence.split(" → ").map((c, i) => `${i ? '<span class="arrow">→</span>' : ""}${escH(c)}`).join("") : "";
    if (nodes.length <= 1 && !edges.length) {
      this.body.innerHTML = `<div class="cascade-empty">No causal links recorded for this node yet. Root causes (scripted events) have no upstream; downstream links appear as the run writes them.</div>`;
      return;
    }
    // columns: order nodes by day then type; same-day nodes get successive columns
    const sorted = [...nodes].sort((a, b) => (a.day ?? 9e9) - (b.day ?? 9e9) || LANES.indexOf(a.type) - LANES.indexOf(b.type));
    // one column per day; a second node of the same type on the same day gets the next column
    const colOf = new Map();
    let col = -1, lastDay = null, used = new Set();
    for (const n of sorted) {
      const key = `${n.day}:${n.type}`;
      if (n.day !== lastDay || used.has(key)) { col += 1; used = new Set(); lastDay = n.day; }
      used.add(key);
      colOf.set(n.id, col);
    }
    const nCols = col + 1;
    const NW = 214, NH = 46, GX = 36, GY = 14, PADX = 16, PADY = 14;
    const lanesUsed = LANES.filter((l) => sorted.some((n) => n.type === l));
    const laneIdx = new Map(lanesUsed.map((l, i) => [l, i]));
    const W = PADX * 2 + nCols * (NW + GX) - GX + 92;
    const H = PADY * 2 + lanesUsed.length * (NH + GY) - GY;
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("width", W); svg.setAttribute("height", H);
    svg.style.minWidth = "100%";
    const pos = (n) => ({ x: PADX + 92 + colOf.get(n.id) * (NW + GX), y: PADY + laneIdx.get(n.type) * (NH + GY) });
    const byId = new Map(sorted.map((n) => [n.id, n]));
    // lane labels
    lanesUsed.forEach((l, i) => {
      const t = el("text", { class: "cn-t", x: PADX, y: PADY + i * (NH + GY) + NH / 2 + 3, style: "fill:var(--text-3);font-size:11px;letter-spacing:.08em;text-transform:uppercase;font-weight:600" });
      t.textContent = LANE_NAME[l]; svg.appendChild(t);
    });
    // edges
    const defs = el("defs", {});
    defs.innerHTML = `<marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="cedge-arrow"/></marker>`;
    svg.appendChild(defs);
    for (const e of edges) {
      const a = byId.get(e.from), b = byId.get(e.to);
      if (!a || !b) continue;
      const pa = pos(a), pb = pos(b);
      const x1 = pa.x + NW, y1 = pa.y + NH / 2, x2 = pb.x, y2 = pb.y + NH / 2;
      const back = x2 < x1;
      const d = back
        ? `M${x1},${y1} C${x1 + 40},${y1} ${x2 - 40},${y2} ${x2},${y2}`
        : `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;
      const p = el("path", { class: `cedge k-${e.kind}`, d, "marker-end": "url(#arr)" });
      svg.appendChild(p);
    }
    // nodes
    for (const n of sorted) {
      const p = pos(n);
      const g = el("g", { class: `cnode t-${n.type} ${n.id === data.root ? "is-root" : ""}`, transform: `translate(${p.x},${p.y})` });
      g.appendChild(el("rect", { width: NW, height: NH, rx: 8 }));
      g.appendChild(el("circle", { class: "cn-mark", cx: 13, cy: NH / 2, r: 4.5 }));
      const l = el("text", { class: "cn-l", x: 24, y: 18 }); l.textContent = trunc(n.label, 30); g.appendChild(l);
      const d = el("text", { class: "cn-d", x: 24, y: 33 }); d.textContent = trunc(n.phrase || n.detail || "", 36); g.appendChild(d);
      const title = el("title", {}); title.textContent = `${n.label}\n${n.detail || ""}${n.rationale ? "\n" + n.rationale : ""}\nclick: centre · double-click: jump to day ${n.day ?? ""}`;
      g.appendChild(title);
      g.addEventListener("click", () => this.onPick && this.onPick(n.id, n.label));
      g.addEventListener("dblclick", () => n.day != null && this.onJump && this.onJump(n.day));
      svg.appendChild(g);
    }
    this.body.innerHTML = "";
    this.body.appendChild(svg);
    const root = sorted.find((n) => n.id === data.root);
    if (root && W > this.body.clientWidth) { const p = pos(root); this.body.scrollLeft = Math.max(0, p.x - this.body.clientWidth / 2); }
  }
}

function el(tag, attrs) { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; }
function escH(s) { return String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])); }
function trunc(s, n) { s = String(s ?? ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
