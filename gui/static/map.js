/* SVG supply-chain map: tiers as left->right columns, agents as resizable dots,
   contracts as edges. Delivery events send a token gliding supplier->customer;
   failure / bump events flash the edge red. Plain SVG, no framework. */

const SVG_NS = "http://www.w3.org/2000/svg";
const R_MIN = 8, R_MAX = 34;
// Fixed radius for the synthetic raw-materials / end-customer framing nodes
// (they have no metric to scale by).
const R_FIXED = 14;

const MapView = (() => {
  let svg, width = 1000, height = 360;
  let metric = "inventory";
  let positions = {};   // agent name -> {x, y}
  let lastTiers = null;

  function init() {
    svg = document.getElementById("map");
    resize();
    window.addEventListener("resize", refresh);
  }

  // Re-measure the SVG and redraw at the current size. Call after anything that
  // changes the map's container width without firing a window resize (e.g. the
  // readout sidebar collapsing/expanding).
  function refresh() {
    resize();
    if (lastTiers) render(lastTiers, []);
  }

  function resize() {
    const rect = svg.getBoundingClientRect();
    width = Math.max(rect.width, 400);
    height = Math.max(rect.height, 320);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  }

  function setMetric(m) { metric = m; if (lastTiers) render(lastTiers, []); }

  function metricValue(a) {
    const v = a[metric];
    return (typeof v === "number") ? v : 0;
  }

  // Map a value to a radius, scaled relative to the max across all agents.
  function radiusScale(allValues) {
    const max = Math.max(1, ...allValues.map(v => Math.abs(v)));
    return (v) => R_MIN + (R_MAX - R_MIN) * (Math.abs(v) / max);
  }

  function clear() {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
  }

  function el(tag, attrs, parent) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const k in attrs) node.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(node);
    return node;
  }

  function render(tiers, events) {
    lastTiers = tiers;
    clear();
    positions = {};

    const nTiers = tiers.length;
    // Two synthetic columns frame the chain: raw materials on the left, end
    // customer on the right. Lay out (nTiers + 2) evenly spaced columns.
    const colGap = width / (nTiers + 3);
    const allValues = [];
    tiers.forEach(t => t.agents.forEach(a => allValues.push(metricValue(a))));
    const rOf = radiusScale(allValues);

    // Layer groups so edges render under nodes.
    const edgeLayer = el("g", {}, svg);
    const nodeLayer = el("g", {}, svg);
    const fxLayer = el("g", {}, svg);

    // Position nodes. Real tiers occupy columns 2..nTiers+1; the framing nodes
    // take column 1 (raw) and column nTiers+2 (end). The end node is keyed "end"
    // to match the customer field on retailer->consumer delivery/failure events.
    const midY = height / 2 + 18;
    const rawX = colGap;
    const endX = colGap * (nTiers + 2);
    positions["__raw"] = { x: rawX, y: midY };
    positions["end"] = { x: endX, y: midY };

    tiers.forEach((tier, ti) => {
      const x = colGap * (ti + 2);
      const rowGap = height / (tier.agents.length + 1);
      el("text", { x, y: 22, class: "tier-label" }, nodeLayer).textContent = tier.name;
      tier.agents.forEach((a, ai) => {
        const y = rowGap * (ai + 1) + 18;
        positions[a.name] = { x, y };
      });
    });

    // Edge registry (keyed "supplier->customer") so events can flash an existing
    // edge that sits under the nodes, rather than drawing a fresh line on top.
    MapView._edgeEls = {};

    // Static framing edges: raw materials -> each top-tier producer, and each
    // bottom-tier retailer -> end customer. Registered so an "end" failure flashes
    // these underneath lines instead of a transient one over the nodes.
    if (nTiers) {
      const raw = positions["__raw"], end = positions["end"];
      tiers[0].agents.forEach(a => {
        const p = positions[a.name];
        MapView._edgeEls["__raw->" + a.name] =
          el("line", { x1: raw.x, y1: raw.y, x2: p.x, y2: p.y, class: "edge" }, edgeLayer);
      });
      tiers[nTiers - 1].agents.forEach(a => {
        const p = positions[a.name];
        MapView._edgeEls[a.name + "->end"] =
          el("line", { x1: p.x, y1: p.y, x2: end.x, y2: end.y, class: "edge" }, edgeLayer);
      });
    }

    // Inter-tier links. Once contracts exist, draw one edge per active
    // supplier->customer pair. Before any do (e.g. a freshly loaded sim), draw the
    // full set of potential buyer<-supplier relationships -- every agent links to
    // each agent in the tier directly above it -- so the network is clear up front.
    const active = window._lastEdges || [];
    if (active.length) {
      active.forEach(e => {
        const s = positions[e.supplier], c = positions[e.customer];
        if (!s || !c) return;
        MapView._edgeEls[e.supplier + "->" + e.customer] =
          el("line", { x1: s.x, y1: s.y, x2: c.x, y2: c.y, class: "edge" }, edgeLayer);
      });
    } else {
      for (let i = 1; i < nTiers; i++) {
        tiers[i].agents.forEach(buyer => {
          const c = positions[buyer.name];
          tiers[i - 1].agents.forEach(sup => {
            const s = positions[sup.name];
            el("line", { x1: s.x, y1: s.y, x2: c.x, y2: c.y, class: "edge" }, edgeLayer);
          });
        });
      }
    }

    // Nodes.
    tiers.forEach((tier) => {
      tier.agents.forEach((a) => {
        const p = positions[a.name];
        const r = rOf(metricValue(a));
        el("circle", { cx: p.x, cy: p.y, r, class: "node" }, nodeLayer);
        const label = el("text", { x: p.x, y: p.y + r + 13, class: "node-label" }, nodeLayer);
        label.textContent = a.name;
      });
    });

    // Framing nodes (raw materials / end customer), same style as agent nodes.
    [[rawX, "raw materials"], [endX, "end customer"]].forEach(([x, label]) => {
      el("text", { x, y: 22, class: "tier-label" }, nodeLayer).textContent = label;
      el("circle", { cx: x, cy: midY, r: R_FIXED, class: "node" }, nodeLayer);
    });

    MapView._fxLayer = fxLayer;
    playEvents(events);
  }

  function setEdges(edges) { window._lastEdges = edges; }

  // Glide a delivery token from the raw-materials node into each top-tier
  // producer -- the exogenous raw supply feeding the chain each tick. Called once
  // per applied snapshot (not on resize, so it doesn't fire on redraw).
  function flowRaw() {
    if (!lastTiers || !lastTiers.length) return;
    lastTiers[0].agents.forEach(a => animateToken("__raw", a.name));
  }

  // Animate this tick's events on top of the current layout.
  function playEvents(events) {
    if (!events) return;
    events.forEach(ev => {
      if (ev.type === "delivered") {
        animateToken(ev.supplier, ev.customer);
      } else if (ev.type === "failed") {
        // A genuine missed delivery: edge flashes red.
        flashEdge(ev.supplier, ev.customer, "flash-fail");
      } else if (ev.type === "bumped") {
        // A supplier reassigning PLANNED future capacity away from this customer
        // -- a disruption, but not a missed delivery. Amber, not red.
        flashEdge(ev.seller, ev.dropped, "flash-bump");
      }
    });
  }

  function animateToken(from, to) {
    const a = positions[from], b = positions[to];
    if (!a) return;
    // "end" customer (retailer -> consumers) glides off to the right.
    const target = b || { x: Math.min(a.x + 120, width - 10), y: a.y };
    const token = el("circle", { cx: a.x, cy: a.y, r: 5, class: "token" }, MapView._fxLayer);
    const t0 = performance.now(), dur = 600;
    function frame(now) {
      const k = Math.min((now - t0) / dur, 1);
      token.setAttribute("cx", a.x + (target.x - a.x) * k);
      token.setAttribute("cy", a.y + (target.y - a.y) * k);
      token.setAttribute("opacity", 1 - k * 0.3);
      if (k < 1) requestAnimationFrame(frame);
      else token.remove();
    }
    requestAnimationFrame(frame);
  }

  function flashEdge(supplier, customer, cls) {
    const key = supplier + "->" + customer;
    const line = MapView._edgeEls && MapView._edgeEls[key];
    if (line) {
      line.classList.add(cls);
      setTimeout(() => line.classList.remove(cls), 500);
      return;
    }
    // No persistent edge (e.g. a just-cleared contract): draw a transient line.
    const a = positions[supplier], b = positions[customer];
    if (!a) return;
    const target = b || { x: Math.min(a.x + 120, width - 10), y: a.y };
    const tmp = el("line", {
      x1: a.x, y1: a.y, x2: target.x, y2: target.y, class: "edge " + cls,
    }, MapView._fxLayer);
    setTimeout(() => tmp.remove(), 500);
  }

  return { init, render, setMetric, setEdges, refresh, flowRaw };
})();
