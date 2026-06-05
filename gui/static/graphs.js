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
