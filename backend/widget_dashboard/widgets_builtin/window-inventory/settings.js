// Window Inventory settings — name each monitor (e.g. "Top" / "Bottom").
// Fetches the live monitor list so you label the real outputs.

function esc(s) { return s == null ? "" : String(s).replace(/"/g, "&quot;"); }

export default {
  mount(container, api) {
    const labels = (api.settings && api.settings.labels) || {};
    const hidden = ((api.settings && api.settings.hidden) || []).map(String);
    container.innerHTML = `
      <p class="drawer-note">Name each monitor (e.g. Top / Middle / Bottom) and
        optionally hide one from the list.</p>
      <form class="wd-form"><div class="wi-set-rows">loading monitors…</div>
        <div class="wd-form-actions">
          <button type="button" data-cancel class="pill-btn">Cancel</button>
          <button type="submit" class="pill-btn primary">Save</button>
        </div>
      </form>`;
    const form = container.querySelector("form");
    const rowsEl = container.querySelector(".wi-set-rows");

    fetch("/api/system/monitors")
      .then((r) => r.json())
      .then((mons) => {
        if (!mons.length) { rowsEl.textContent = "No monitors detected."; return; }
        rowsEl.innerHTML = mons.map((m) => `
          <div class="wi-set-mon">
            <label>${esc(m.name)}${m.primary ? " · primary" : ""}
              <input class="wi-set-label" data-idx="${m.index}" type="text"
                     value="${esc(labels[m.index] || m.name)}" placeholder="e.g. Top" />
            </label>
            <label class="wd-check">
              <input type="checkbox" data-hide="${m.index}"
                     ${hidden.includes(String(m.index)) ? "checked" : ""} />
              Hide this monitor from the list
            </label>
          </div>`).join("");
      })
      .catch(() => { rowsEl.textContent = "Could not read monitors."; });

    form.onsubmit = (e) => {
      e.preventDefault();
      const next = {};
      const hide = [];
      rowsEl.querySelectorAll("input[data-idx]").forEach((inp) => {
        const v = inp.value.trim();
        if (v) next[inp.dataset.idx] = v;
      });
      rowsEl.querySelectorAll("input[data-hide]").forEach((cb) => {
        if (cb.checked) hide.push(Number(cb.dataset.hide));
      });
      api.save({ ...(api.settings || {}), labels: next, hidden: hide });
    };
    container.querySelector("[data-cancel]").onclick = () => api.cancel();
  },
};
