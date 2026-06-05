/* Deliveries-vs-failures line chart (Chart.js). One point appended per tick. */

const GraphView = (() => {
  let chart;

  function init() {
    const ctx = document.getElementById("chart").getContext("2d");
    chart = new Chart(ctx, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          { label: "deliveries", data: [], borderColor: "#16a34a",
            backgroundColor: "#16a34a22", tension: 0.2, pointRadius: 0 },
          { label: "failed deliveries", data: [], borderColor: "#dc2626",
            backgroundColor: "#dc262622", tension: 0.2, pointRadius: 0 },
          { label: "bumps (re-routes)", data: [], borderColor: "#d97706",
            backgroundColor: "#d9770622", tension: 0.2, pointRadius: 0 },
        ],
      },
      options: {
        responsive: true,
        animation: false,
        scales: {
          x: { ticks: { color: "#6b7280" }, grid: { color: "#e5e7eb" }, title: { display: true, text: "tick", color: "#6b7280" } },
          y: { ticks: { color: "#6b7280" }, grid: { color: "#e5e7eb" }, beginAtZero: true },
        },
        plugins: { legend: { labels: { color: "#1a1f2b" } } },
      },
    });
  }

  function reset() {
    chart.data.labels = [];
    chart.data.datasets.forEach(d => (d.data = []));
    chart.update();
  }

  function push(t, events) {
    let delivered = 0, failed = 0, bumped = 0;
    (events || []).forEach(ev => {
      if (ev.type === "delivered") delivered++;
      else if (ev.type === "failed") failed++;
      else if (ev.type === "bumped") bumped++;
    });
    chart.data.labels.push(t);
    chart.data.datasets[0].data.push(delivered);
    chart.data.datasets[1].data.push(failed);
    chart.data.datasets[2].data.push(bumped);
    chart.update();
  }

  return { init, reset, push };
})();


/* Shared helpers for the per-pair bump-probability charts below. Each draws one
   translucent line per directed (buyer <- supplier) pair from its fitted logistic
   belief P(bump) = sigmoid(a + b * lead + c * stake); the two charts slice that
   surface along lead or along stake, holding the other input at the pair's median
   observed value (sent as curve.stake / curve.lead) so the line stays where the
   data actually lived. Hover a line to see which pair it is. */

const _sigmoid = (z) => (z >= 0 ? 1 / (1 + Math.exp(-z)) : Math.exp(z) / (1 + Math.exp(z)));

// Stable hue per label so a pair keeps its colour across updates and across charts.
function _hueFor(label) {
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) % 360;
  return h;
}

// One translucent dataset for a pair, given its precomputed [{x,y}] points.
function _pairDataset(label, points) {
  const hue = _hueFor(label);
  return {
    label,
    data: points,
    borderColor: `hsla(${hue}, 70%, 50%, 0.45)`,
    backgroundColor: `hsla(${hue}, 70%, 50%, 0.1)`,
    borderWidth: 1.5,
    tension: 0.2,
    pointRadius: 0,
    pointHoverRadius: 3,
    fill: false,
  };
}

// Base Chart.js config shared by both per-pair charts; caller supplies the x-axis.
function _pairChart(canvasId, xScale) {
  return new Chart(document.getElementById(canvasId).getContext("2d"), {
    type: "line",
    data: { datasets: [] },
    options: {
      responsive: true,
      animation: false,
      parsing: false,
      interaction: { mode: "nearest", intersect: false },
      scales: {
        x: xScale,
        y: { min: 0, max: 1,
             ticks: { color: "#6b7280" }, grid: { color: "#e5e7eb" },
             title: { display: true, text: "P(bump)", color: "#6b7280" } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { title: (items) => items[0]?.dataset.label || "" } },
      },
    },
  });
}


/* P(bump) vs lead fraction (x in [0, 1]), stake held at each pair's median. */
const BumpView = (() => {
  let chart;
  const XS = Array.from({ length: 21 }, (_, i) => i / 20);

  function init() {
    chart = _pairChart("bump-chart", {
      type: "linear", min: 0, max: 1,
      ticks: { color: "#6b7280" }, grid: { color: "#e5e7eb" },
      title: { display: true, text: "lead fraction", color: "#6b7280" },
    });
  }

  function reset() { chart.data.datasets = []; chart.update(); }

  // Rebuild one line per curve = {label, a, b, c, stake}.
  function update(curves) {
    chart.data.datasets = (curves || []).map(c =>
      _pairDataset(c.label, XS.map(x => ({ x, y: _sigmoid(c.a + c.b * x + c.c * c.stake) }))));
    chart.update();
  }

  return { init, reset, update };
})();


/* P(bump) vs price + penalty (x = stake), lead held at each pair's median. The
   x-range comes from the snapshot (a high percentile of observed stakes, so rare
   runaway-stake outliers don't compress the axis). */
const StakeView = (() => {
  let chart;
  const N = 40;

  function init() {
    chart = _pairChart("stake-chart", {
      type: "linear", min: 0,
      ticks: { color: "#6b7280" }, grid: { color: "#e5e7eb" },
      title: { display: true, text: "price + penalty", color: "#6b7280" },
    });
  }

  function reset() { chart.data.datasets = []; chart.update(); }

  // Rebuild one line per curve = {label, a, b, c, lead}; axis = [lo, hi].
  function update(curves, axis) {
    const [lo, hi] = axis && axis[1] > axis[0] ? axis : [0, 1];
    chart.options.scales.x.max = hi;
    const xs = Array.from({ length: N + 1 }, (_, i) => lo + (hi - lo) * (i / N));
    chart.data.datasets = (curves || []).map(c =>
      _pairDataset(c.label, xs.map(x => ({ x, y: _sigmoid(c.a + c.b * c.lead + c.c * x) }))));
    chart.update();
  }

  return { init, reset, update };
})();
