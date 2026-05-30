// System Stats frontend — a row per metric with a sparkline, current value, an
// optional temperature badge, and a usage bar. Keeps its own short history.
// The GPU row hides itself when no GPU is detected.

const METRICS = [
  { key: "cpu", label: "CPU", bar: true, pct: "cpu",
    value: (s) => `${s.cpu}%`, temp: (s) => s.cpu_temp },
  { key: "gpu", label: "GPU", bar: true, pct: "gpu",
    value: (s) => (s.gpu == null ? "—" : `${s.gpu}%`), temp: (s) => s.gpu_temp,
    detail: (s) => (s.gpu_mem_pct != null ? `${s.gpu_mem_pct}% vram` : "") },
  { key: "mem_pct", label: "RAM", bar: true, pct: "mem_pct",
    value: (s) => `${s.mem_pct}%`, detail: (s) => `${s.mem_used_gb}/${s.mem_total_gb} GB` },
  { key: "disk_pct", label: "DISK", bar: true, pct: "disk_pct",
    value: (s) => `${s.disk_pct}%`, temp: (s) => s.disk_temp,
    detail: (s) => `${s.disk_free_gb} GB free` },
  { key: "net_rx_kbs", label: "NET", bar: false,
    value: (s) => `↓${s.net_rx_kbs} ↑${s.net_tx_kbs} KB/s` },
];

const HIST = 40;

function spark(history) {
  if (history.length < 2) return "";
  const max = Math.max(...history, 1);
  const pts = history.map((v, i) =>
    `${(i / (HIST - 1) * 100).toFixed(1)},${(100 - (v / max) * 100).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points="${pts}"/></svg>`;
}

export default {
  mount(container, api) {
    const hist = Object.fromEntries(METRICS.map((m) => [m.key, []]));
    container.innerHTML = `<div class="ss-root">${METRICS.map((m) => `
      <div class="ss-metric" data-k="${m.key}">
        <div class="ss-row">
          <div class="ss-label">${m.label}</div>
          <div class="ss-spark"></div>
          <div class="ss-temp"></div>
          <div class="ss-val"></div>
        </div>
        ${m.bar ? `<div class="ss-bar"><div class="ss-bar-fill" data-bar="${m.key}"></div></div>` : ""}
      </div>`).join("")}</div>`;

    const off = api.onMessage((s) => {
      for (const m of METRICS) {
        const metric = container.querySelector(`.ss-metric[data-k="${m.key}"]`);
        if (!metric) continue;

        // Hide the GPU row entirely when there's no GPU.
        if (m.key === "gpu") metric.style.display = s.gpu_present ? "" : "none";

        const v = s[m.key];
        if (v != null) {
          hist[m.key].push(v);
          if (hist[m.key].length > HIST) hist[m.key].shift();
          metric.querySelector(".ss-spark").innerHTML = spark(hist[m.key]);
        }
        const detail = m.detail ? m.detail(s) : "";
        metric.querySelector(".ss-val").textContent =
          m.value(s) + (detail ? `  ${detail}` : "");

        const tempEl = metric.querySelector(".ss-temp");
        const t = m.temp ? m.temp(s) : null;
        if (t != null) {
          tempEl.textContent = `${t}°`;
          tempEl.classList.toggle("hot", t >= 80);
          tempEl.style.display = "";
        } else {
          tempEl.style.display = "none";
        }

        if (m.bar) {
          const fill = metric.querySelector(`[data-bar="${m.key}"]`);
          const pct = s[m.pct] || 0;
          fill.style.width = `${pct}%`;
          fill.classList.toggle("hot", pct > 85);
        }
      }
    });

    return () => off();
  },
};
