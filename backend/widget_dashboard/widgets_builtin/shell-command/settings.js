// Shell Command settings (docs/widgets.md 4.4 "settings.js").
//
// Same shape as a frontend module, but api has save(newSettings) and cancel()
// instead of send/onMessage. The shell opens this in the settings side panel.

export default {
  mount(container, api) {
    const s = api.settings || {};
    const thresholds = Array.isArray(s.thresholds) ? s.thresholds : [];

    container.innerHTML = `
      <form class="wd-form">
        <label>Command
          <input name="command" type="text" value="${attr(s.command)}"
                 placeholder="e.g. nproc" />
        </label>
        <label>Working directory (optional)
          <input name="cwd" type="text" value="${attr(s.cwd)}"
                 placeholder="~ (defaults to home); e.g. ~/projects/app" />
        </label>
        <label class="wd-check">
          <input name="terminal" type="checkbox" ${s.terminal ? "checked" : ""} />
          Launch in a new terminal window (detached from the dashboard)
        </label>
        <label class="wd-when-output">Refresh
          <select name="mode">
            <option value="interval" ${sel(s.mode, "interval", true)}>Every N seconds</option>
            <option value="on-click" ${sel(s.mode, "on-click")}>On click</option>
          </select>
        </label>
        <label class="wd-when-interval wd-when-output">Interval (seconds)
          <input name="interval" type="number" min="1" value="${attr(s.interval ?? 5)}" />
        </label>
        <label class="wd-when-output">Display as
          <select name="display">
            ${["text", "number", "sparkline", "pill", "table"].map((d) =>
              `<option value="${d}" ${sel(s.display, d, d === "text")}>${d}</option>`).join("")}
          </select>
        </label>
        <label>Label (optional)
          <input name="label" type="text" value="${attr(s.label)}" />
        </label>
        <label class="wd-when-output">Parse regex (optional, first group = value)
          <input name="regex" type="text" value="${attr(s.regex)}"
                 placeholder="e.g. (\\d+)%" />
        </label>

        <div class="wd-thresholds wd-when-pill wd-when-output">
          <div class="wd-form-head">Pill thresholds</div>
          <div class="wd-thresh-rows"></div>
          <button type="button" class="wd-mini" data-add>+ add threshold</button>
        </div>

        <div class="wd-form-actions">
          <button type="button" data-cancel class="wd-btn">Cancel</button>
          <button type="submit" class="wd-btn wd-btn-primary">Save</button>
        </div>
      </form>`;

    const form = container.querySelector("form");
    const rowsEl = container.querySelector(".wd-thresh-rows");

    function addRow(t = { op: ">", value: "", color: "#5fa86a" }) {
      const row = document.createElement("div");
      row.className = "wd-thresh-row";
      row.innerHTML = `
        <select class="t-op">
          ${[">", ">=", "<", "<=", "=="].map((o) =>
            `<option ${o === t.op ? "selected" : ""}>${o}</option>`).join("")}
        </select>
        <input class="t-val" type="number" value="${attr(t.value)}" />
        <input class="t-color" type="color" value="${attr(t.color || "#5fa86a")}" />
        <button type="button" class="wd-mini" data-del>✕</button>`;
      row.querySelector("[data-del]").onclick = () => row.remove();
      rowsEl.appendChild(row);
    }
    thresholds.forEach(addRow);
    container.querySelector("[data-add]").onclick = () => addRow();

    function syncVisibility() {
      const terminal = form.terminal.checked;
      const mode = form.mode.value, display = form.display.value;
      // Output-only fields don't apply when launching in a terminal.
      container.querySelectorAll(".wd-when-output").forEach((el) => {
        el.style.display = terminal ? "none" : "";
      });
      container.querySelector(".wd-when-interval").style.display =
        (!terminal && mode === "interval") ? "" : "none";
      container.querySelector(".wd-when-pill").style.display =
        (!terminal && display === "pill") ? "" : "none";
    }
    form.mode.onchange = syncVisibility;
    form.display.onchange = syncVisibility;
    form.terminal.onchange = syncVisibility;
    syncVisibility();

    form.onsubmit = (e) => {
      e.preventDefault();
      const next = {
        command: form.command.value.trim(),
        cwd: form.cwd.value.trim(),
        terminal: form.terminal.checked,
        mode: form.mode.value,
        interval: Number(form.interval.value) || 5,
        display: form.display.value,
        label: form.label.value.trim(),
        regex: form.regex.value.trim(),
        thresholds: [...rowsEl.querySelectorAll(".wd-thresh-row")].map((r) => ({
          op: r.querySelector(".t-op").value,
          value: r.querySelector(".t-val").value,
          color: r.querySelector(".t-color").value,
        })),
      };
      api.save(next);
    };
    container.querySelector("[data-cancel]").onclick = () => api.cancel();
  },
};

function attr(v) { return v == null ? "" : String(v).replace(/"/g, "&quot;"); }
function sel(cur, val, dflt) { return (cur === val || (cur == null && dflt)) ? "selected" : ""; }
