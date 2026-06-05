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


/* Bump-probability-vs-lead-time chart. One translucent line per directed
   (buyer <- supplier) pair, drawn as P(bump) = sigmoid(a + b * lead_frac + c * stake)
   using each buyer's fitted belief, with the stake term held at the pair's median
   observed stake so the curve stays in the range the data actually covered. Hover
   a line to see which pair it is. */
const BumpView = (() => {
  let chart;
  // Lead fraction sampled across [0, 1].
  const XS = Array.from({ length: 21 }, (_, i) => i / 20);

  const sigmoid = (z) => (z >= 0 ? 1 / (1 + Math.exp(-z)) : Math.exp(z) / (1 + Math.exp(z)));

  // Stable hue per label so a pair keeps its colour across updates.
  function hueFor(label) {
    let h = 0;
    for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) % 360;
    return h;
  }

  function init() {
    const ctx = document.getElementById("bump-chart").getContext("2d");
    chart = new Chart(ctx, {
      type: "line",
      data: { datasets: [] },
      options: {
        responsive: true,
        animation: false,
        parsing: false,
        interaction: { mode: "nearest", intersect: false },
        scales: {
          x: { type: "linear", min: 0, max: 1,
               ticks: { color: "#6b7280" }, grid: { color: "#e5e7eb" },
               title: { display: true, text: "lead fraction", color: "#6b7280" } },
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

  function reset() {
    chart.data.datasets = [];
    chart.update();
  }

  // Rebuild one line per curve = {label, a, b, c, stake}.
  function update(curves) {
    chart.data.datasets = (curves || []).map(c => {
      const hue = hueFor(c.label);
      return {
        label: c.label,
        data: XS.map(x => ({ x, y: sigmoid(c.a + c.b * x + c.c * c.stake) })),
        borderColor: `hsla(${hue}, 70%, 50%, 0.45)`,
        backgroundColor: `hsla(${hue}, 70%, 50%, 0.1)`,
        borderWidth: 1.5,
        tension: 0.2,
        pointRadius: 0,
        pointHoverRadius: 3,
        fill: false,
      };
    });
    chart.update();
  }

  return { init, reset, update };
})();
