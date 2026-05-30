// Shell Command widget frontend (docs/widgets.md 4.5, state_intents pattern).
//
// Renders backend state into one of five display types. Builds a stable
// label/body/foot scaffold once and patches it in place; the body's inner
// structure is only rebuilt when the display type (or error state) changes, so
// a steady interval refresh just updates a text node or a sparkline's points.
// In on-click mode the whole widget is a button that sends a `run` intent.

function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function pickColor(value, thresholds) {
  const num = parseFloat(value);
  for (const t of thresholds || []) {
    const tv = parseFloat(t.value);
    const ok = ({ ">": num > tv, ">=": num >= tv, "<": num < tv, "<=": num <= tv, "==": num === tv })[t.op];
    if (ok) return t.color;
  }
  return null;
}

function sparkPoints(history) {
  return history.map((v, i) => {
    const min = Math.min(...history), max = Math.max(...history), span = (max - min) || 1;
    const x = (i / Math.max(1, history.length - 1)) * 100;
    const y = 100 - ((v - min) / span) * 100;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

export default {
  mount(container, api) {
    container.innerHTML = `
      <div class="sc-root">
        <div class="sc-label"></div>
        <div class="sc-body"></div>
        <div class="sc-foot"></div>
      </div>`;
    const root = container.querySelector(".sc-root");
    const labelEl = container.querySelector(".sc-label");
    const bodyEl = container.querySelector(".sc-body");
    const footEl = container.querySelector(".sc-foot");
    let bodyKind = null;   // which structure is currently in bodyEl
    let last = {};

    // Click runs on-click commands and launches terminal-mode commands.
    container.addEventListener("click", () => {
      if (last.mode === "on-click" || last.terminal) api.intent("run");
    });

    // Ensure bodyEl holds the structure for `kind`; (re)build only on change.
    function ensure(kind, html) {
      if (bodyKind !== kind) { bodyEl.innerHTML = html; bodyKind = kind; }
    }

    const off = api.onState((state) => {
      last = state;
      labelEl.textContent = state.label || "";
      labelEl.style.display = state.label ? "" : "none";

      // Terminal-launch mode: render a launch button, not command output.
      if (state.terminal) {
        root.classList.add("sc-clickable");
        ensure("launcher", `<button class="sc-launch" type="button">▶ <span></span></button>`);
        bodyEl.querySelector("span").textContent = state.label || state.command || "Launch";
        bodyEl.querySelector(".sc-launch").title = state.command || "";
        footEl.textContent = state.error
          ? state.error
          : (state.ts ? `launched ${state.ts} · click to launch again` : "click to launch in a new terminal");
        return;
      }

      root.classList.toggle("sc-clickable", state.mode === "on-click");
      footEl.textContent = (state.ts || "") + (state.mode === "on-click" ? " · click to run" : "");

      if (state.error) {
        ensure("error", `<div class="sc-error"></div>`);
        bodyEl.firstChild.textContent = state.error;
        return;
      }
      if (state.value === null) { ensure("empty", `<div class="sc-empty">…</div>`); return; }

      switch (state.display) {
        case "number":
          ensure("number", `<div class="sc-number"></div>`);
          bodyEl.firstChild.textContent = state.value;
          break;
        case "sparkline": {
          ensure("sparkline",
            `<div class="sc-sparkwrap"><svg class="sc-spark" viewBox="0 0 100 100" preserveAspectRatio="none"><polyline/></svg><div class="sc-spark-val"></div></div>`);
          const hist = state.history || [];
          const poly = bodyEl.querySelector("polyline");
          if (hist.length) poly.setAttribute("points", sparkPoints(hist));
          bodyEl.querySelector(".sc-spark-val").textContent = hist.length ? hist[hist.length - 1] : "";
          break;
        }
        case "pill": {
          ensure("pill", `<div class="sc-pill"></div>`);
          const pill = bodyEl.firstChild;
          pill.textContent = state.value;
          const color = pickColor(state.value, state.thresholds);
          pill.style.setProperty("--pill", color || "var(--wd-accent)");
          break;
        }
        case "table": {
          ensure("table", `<table class="sc-table"><tbody></tbody></table>`);
          const rows = String(state.raw).split("\n").filter(Boolean).map((line) =>
            `<tr>${line.split(/\s{1,}/).map((c) => `<td>${escapeHtml(c)}</td>`).join("")}</tr>`).join("");
          bodyEl.querySelector("tbody").innerHTML = rows;
          break;
        }
        default:
          ensure("text", `<pre class="sc-text"></pre>`);
          bodyEl.firstChild.textContent = state.raw || state.value;
      }
    });

    return () => off();
  },
};
