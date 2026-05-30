// Window Inventory frontend (state_intents).
//
// Groups windows by monitor (each with its user-defined label). Drag a window
// from one monitor's group onto another to move it; click focuses; right-click
// opens a menu to move / resize (tile) / fullscreen / close.
//
// Rows and sections are kept across updates and patched in place (keyed by
// window id / monitor index), so the list doesn't flicker and a drag or open
// menu isn't interrupted by the 1.5s refresh.

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function closeMenu() { document.querySelectorAll(".wi-menu").forEach((m) => m.remove()); }

const REGIONS = [
  ["maximize", "Maximize"], ["left", "Left half"], ["right", "Right half"],
  ["top", "Top half"], ["bottom", "Bottom half"], ["center", "Center"],
];

export default {
  mount(container, api) {
    container.innerHTML = `<div class="wi-root"></div><div class="wi-empty" style="display:none"></div>`;
    const root = container.querySelector(".wi-root");
    const emptyEl = container.querySelector(".wi-empty");

    const sections = new Map();   // monitor index (string) -> { sectionEl, bodyEl, countEl }
    const rows = new Map();       // window id -> row element
    let state = { windows: [], monitors: [] };
    let draggingId = null;
    let pending = null;           // latest state withheld during interaction

    const interacting = () => draggingId !== null || document.querySelector(".wi-menu");

    // --- a window row --------------------------------------------------------
    function makeRow(id) {
      const row = document.createElement("div");
      row.className = "wi-row";
      row.dataset.id = id;
      row.draggable = true;
      row.innerHTML = `<span class="wi-grip">⠿</span>
        <span class="wi-ico"></span>
        <span class="wi-title"></span><span class="wi-class"></span>`;
      row.addEventListener("click", (e) => {
        if (e.target.closest(".wi-grip")) return;
        api.intent("focus", { id });
      });
      row.addEventListener("dragstart", (e) => {
        draggingId = id;
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", id);
        row.classList.add("wi-dragging");
      });
      row.addEventListener("dragend", () => {
        draggingId = null;
        row.classList.remove("wi-dragging");
        flushPending();
      });
      row.addEventListener("contextmenu", (e) => { e.preventDefault(); openMenu(e, id); });
      return row;
    }

    // --- a monitor section (also a drop target) ------------------------------
    function makeSection(idx) {
      const sectionEl = document.createElement("div");
      sectionEl.className = "wi-mon";
      sectionEl.dataset.monitor = idx;
      sectionEl.innerHTML = `
        <div class="wi-mon-head"><span class="wi-mon-name"></span><span class="wi-mon-count"></span></div>
        <div class="wi-mon-body"></div>`;
      const bodyEl = sectionEl.querySelector(".wi-mon-body");
      const countEl = sectionEl.querySelector(".wi-mon-count");
      const onOver = (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; sectionEl.classList.add("wi-drop"); };
      sectionEl.addEventListener("dragover", onOver);
      sectionEl.addEventListener("dragenter", onOver);
      sectionEl.addEventListener("dragleave", (e) => {
        if (!sectionEl.contains(e.relatedTarget)) sectionEl.classList.remove("wi-drop");
      });
      sectionEl.addEventListener("drop", (e) => {
        e.preventDefault();
        sectionEl.classList.remove("wi-drop");
        const id = draggingId || e.dataTransfer.getData("text/plain");
        draggingId = null;
        if (!id) return;
        const win = state.windows.find((w) => String(w.id) === String(id));
        const target = Number(sectionEl.dataset.monitor);
        if (win && win.monitor !== target) api.intent("move", { id, monitor: target });
      });
      return { sectionEl, bodyEl, countEl };
    }

    // --- right-click menu ----------------------------------------------------
    function openMenu(e, id) {
      closeMenu();
      const win = state.windows.find((w) => String(w.id) === String(id));
      const menu = document.createElement("div");
      menu.className = "wi-menu";
      const others = (state.monitors || []).filter((m) => m.index !== (win && win.monitor));
      const chk = (on) => (on ? "✓ " : "");
      menu.innerHTML = `
        <button data-act="focus">Focus</button>
        ${others.length ? `<div class="wi-menu-label">Move to</div>` : ""}
        ${others.map((m) => `<button data-move="${m.index}">${esc(m.label)}</button>`).join("")}
        <div class="wi-menu-label">Resize</div>
        ${REGIONS.map(([r, t]) => `<button data-region="${r}">${t}</button>`).join("")}
        <div class="wi-menu-label">Keep</div>
        <button data-act="above">${chk(win && win.above)}Always on top</button>
        <button data-act="sticky">${chk(win && win.sticky)}On all workspaces</button>
        <button data-act="fullscreen">${chk(win && win.fullscreen)}Fullscreen</button>
        <div class="wi-menu-sep"></div>
        <button data-act="close" class="wi-danger">Close window</button>`;
      menu.style.left = `${Math.min(e.clientX, innerWidth - 190)}px`;
      menu.style.top = `${Math.min(e.clientY, innerHeight - 320)}px`;
      document.body.appendChild(menu);
      const act = (fn) => { closeMenu(); fn(); flushPending(); };
      menu.querySelector('[data-act="focus"]').onclick = () => act(() => api.intent("focus", { id }));
      menu.querySelector('[data-act="above"]').onclick = () => act(() => api.intent("above", { id }));
      menu.querySelector('[data-act="sticky"]').onclick = () => act(() => api.intent("sticky", { id }));
      menu.querySelector('[data-act="fullscreen"]').onclick = () => act(() => api.intent("fullscreen", { id }));
      menu.querySelector('[data-act="close"]').onclick = () => act(() => api.intent("close", { id }));
      menu.querySelectorAll("[data-move]").forEach((b) =>
        b.onclick = () => act(() => api.intent("move", { id, monitor: Number(b.dataset.move) })));
      menu.querySelectorAll("[data-region]").forEach((b) =>
        b.onclick = () => act(() => api.intent("place", { id, region: b.dataset.region })));
    }

    // --- render (keyed diff) -------------------------------------------------
    function flushPending() { if (pending && !interacting()) { const s = pending; pending = null; render(s); } }

    function render(s) {
      if (interacting()) { pending = s; return; }   // don't disrupt a drag/menu
      state = s;
      if (s.error) {
        root.style.display = "none";
        emptyEl.style.display = ""; emptyEl.className = "wi-error"; emptyEl.textContent = s.error;
        return;
      }
      root.style.display = ""; emptyEl.style.display = "none";

      const mons = s.monitors || [];
      const monIds = new Set(mons.map((m) => String(m.index)));

      // sections: one per monitor, in order; remove gone ones.
      for (const [idx, sec] of sections) {
        if (!monIds.has(idx)) { sec.sectionEl.remove(); sections.delete(idx); }
      }
      mons.forEach((m) => {
        const idx = String(m.index);
        let sec = sections.get(idx);
        if (!sec) { sec = makeSection(idx); sections.set(idx, sec); }
        sec.sectionEl.querySelector(".wi-mon-name").textContent = m.label;
        root.appendChild(sec.sectionEl);          // keep monitor order
      });

      // rows: place each window in its monitor's section (fallback: first).
      const seen = new Set();
      const counts = {};
      for (const w of s.windows || []) {
        const id = String(w.id);
        seen.add(id);
        let row = rows.get(id);
        if (!row) { row = makeRow(id); rows.set(id, row); }
        row.title = w.title || "";
        row.querySelector(".wi-title").textContent = w.title || "(untitled)";
        row.querySelector(".wi-class").textContent = w.wm_class || "";
        // Icon (the window's own _NET_WM_ICON), with a letter fallback. Only
        // touch the DOM when it actually changes.
        const iconKey = w.icon || `letter:${w.wm_class || "?"}`;
        if (row._iconKey !== iconKey) {
          row._iconKey = iconKey;
          const ico = row.querySelector(".wi-ico");
          if (w.icon) {
            ico.className = "wi-ico";
            ico.innerHTML = `<img src="${w.icon}" alt="" />`;
          } else {
            ico.className = "wi-ico wi-ico-letter";
            ico.textContent = ((w.wm_class || "?").trim()[0] || "?").toUpperCase();
          }
        }
        const targetIdx = sections.has(String(w.monitor)) ? String(w.monitor)
                          : (mons[0] ? String(mons[0].index) : null);
        if (targetIdx != null) {
          const body = sections.get(targetIdx).bodyEl;
          if (row.parentElement !== body) body.appendChild(row);
          counts[targetIdx] = (counts[targetIdx] || 0) + 1;
        }
      }
      for (const [id, row] of rows) if (!seen.has(id)) { row.remove(); rows.delete(id); }

      // per-section counts + empty hint
      for (const [idx, sec] of sections) {
        const n = counts[idx] || 0;
        sec.countEl.textContent = n ? `${n}` : "";
        sec.bodyEl.classList.toggle("wi-mon-empty", n === 0);
      }
    }

    const off = api.onState(render);
    document.addEventListener("click", closeMenu);
    return () => { off(); document.removeEventListener("click", closeMenu); closeMenu(); };
  },
};
