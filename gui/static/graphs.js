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
          { label: "deliveries", data: [], borderColor: "#36d399",
            backgroundColor: "#36d39933", tension: 0.2, pointRadius: 0 },
          { label: "failed deliveries", data: [], borderColor: "#ff5d5d",
            backgroundColor: "#ff5d5d33", tension: 0.2, pointRadius: 0 },
          { label: "bumps (re-routes)", data: [], borderColor: "#f5b942",
            backgroundColor: "#f5b94233", tension: 0.2, pointRadius: 0 },
        ],
      },
      options: {
        responsive: true,
        animation: false,
        scales: {
          x: { ticks: { color: "#8a93a6" }, grid: { color: "#2a3245" }, title: { display: true, text: "tick", color: "#8a93a6" } },
          y: { ticks: { color: "#8a93a6" }, grid: { color: "#2a3245" }, beginAtZero: true },
        },
        plugins: { legend: { labels: { color: "#e6ebf5" } } },
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
