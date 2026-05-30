// ===========================================================================
// Widget Dashboard — frontend shell.
//
// Hosts widgets (lazy-loads each frontend.js, gives it a websocket-backed api),
// renders the tab bar, the always-editable gridstack grid, the picker, the
// settings drawer, the per-instance response editor, the install dialog, and
// the system-event channel that drives toasts / badges / flash / reveal /
// switch-to-tab / lockdown notices.
//
// Editing is always on (no edit mode): widgets drag from a grip handle and
// resize from their edges, and their controls appear on hover. Layout changes
// are held in memory by the backend and only written to disk on an explicit
// Save (document model); a Load▾ menu applies reusable presets.
// ===========================================================================

const api = {
  async get(p) { return (await fetch(p)).json(); },
  async post(p, b) {
    return (await fetch(p, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: b ? JSON.stringify(b) : undefined,
    })).json();
  },
  async put(p, b) {
    return (await fetch(p, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(b),
    })).json();
  },
  async del(p) { return (await fetch(p, { method: "DELETE" })).json(); },
};

const $ = (sel) => document.querySelector(sel);

// --- shared state ---------------------------------------------------------

let grid = null;
let currentTab = null;
let tabsState = [];                  // [{name, state, dirty}]
let widgetMeta = {};                 // widget_id -> manifest summary
const mounted = new Map();           // instance_id -> { cleanup, ws }
const recById = new Map();           // instance_id -> layout record
const loadedModules = new Map();
const loadedStyles = new Set();

// --- widget hosting -------------------------------------------------------

// One token per page load: appended to widget asset URLs so a dynamic import()
// or stylesheet can never be served a stale cached copy after a backend update.
// (The HTTP module cache is keyed by URL; a fresh token = guaranteed refetch.)
const ASSET_V = Date.now();

function loadWidgetModule(widgetId, file = "frontend.js") {
  // Cache the in-flight import promise so concurrent mounts of the same widget
  // (parallel boot) share one fetch instead of racing duplicate requests.
  const key = `${widgetId}:${file}`;
  if (!loadedModules.has(key)) {
    loadedModules.set(key, import(`/api/widgets/${widgetId}/${file}?v=${ASSET_V}`).then((m) => m.default));
  }
  return loadedModules.get(key);
}

function injectWidgetStyle(widgetId) {
  if (loadedStyles.has(widgetId)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = `/api/widgets/${widgetId}/style.css?v=${ASSET_V}`;
  document.head.appendChild(link);
  loadedStyles.add(widgetId);
}

// The per-instance api: supports free_form (send/onMessage) and state_intents
// (intent/onState), and intercepts shell control messages.
function makeWidgetApi(instanceId, settings) {
  const msgHandlers = new Set();
  const stateHandlers = new Set();
  // Buffer the latest state/message so a handler that registers AFTER the first
  // frame arrives still gets it (the websocket can deliver the initial state
  // before the widget's mount() subscribes — otherwise it would be lost).
  let lastState;            // undefined until a __state__ arrives
  let lastMsg;              // undefined until a free-form msg arrives
  const ws = new WebSocket(`ws://${location.host}/api/instances/${instanceId}/ws`);

  ws.addEventListener("message", (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg && msg.__control__) { handleControl(instanceId, msg.__control__); return; }
    if (msg && msg.__state__ !== undefined) {
      lastState = msg.__state__;
      stateHandlers.forEach((h) => h(lastState));
      return;
    }
    lastMsg = msg;
    msgHandlers.forEach((h) => h(msg));
  });

  const send = (m) => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m)); };
  return {
    ws,
    api: {
      settings,
      send,
      onMessage: (h) => {
        msgHandlers.add(h);
        if (lastMsg !== undefined) h(lastMsg);
        return () => msgHandlers.delete(h);
      },
      onState: (h) => {
        stateHandlers.add(h);
        if (lastState !== undefined) h(lastState);
        return () => stateHandlers.delete(h);
      },
      intent: (type, payload) => send({ __intent__: { type, payload: payload || {} } }),
    },
  };
}

async function mountWidget(rec) {
  recById.set(rec.id, rec);
  const mod = await loadWidgetModule(rec.widget_id);
  injectWidgetStyle(rec.widget_id);
  const body = document.querySelector(`[gs-id="${rec.id}"] .widget-body`);
  if (!body) return;
  const { ws, api: widgetApi } = makeWidgetApi(rec.id, rec.settings || {});
  let cleanup = () => {};
  try { cleanup = mod.mount(body, widgetApi) || (() => {}); }
  catch (e) { body.innerHTML = `<div class="widget-err">widget error: ${e.message}</div>`; }
  mounted.set(rec.id, { cleanup, ws });
}

function unmountWidget(instanceId) {
  const m = mounted.get(instanceId);
  if (!m) return;
  try { m.cleanup(); } catch {}
  try { m.ws.close(); } catch {}
  mounted.delete(instanceId);
}

function handleControl(instanceId, ctrl) {
  const node = document.querySelector(`[gs-id="${instanceId}"]`);
  if (!node) return;
  if (ctrl.action === "reveal") node.classList.remove("hidden-widget", "collapsed");
  else if (ctrl.action === "hide") node.classList.add("hidden-widget");
  else if (ctrl.action === "collapse") node.classList.add("collapsed");
}

// --- grid -----------------------------------------------------------------

function gridItemHtml(rec) {
  const meta = widgetMeta[rec.widget_id] || {};
  const hidden = meta.visibility === "hidden_until_triggered" ? "hidden-widget" : "";
  const name = meta.name || rec.widget_id;
  const tint = rec.color ? ` style="background-color:${escapeHtml(rec.color)}"` : "";
  return `
    <div class="grid-stack-item ${hidden}" gs-id="${rec.id}"
         gs-x="${rec.x}" gs-y="${rec.y}" gs-w="${rec.w}" gs-h="${rec.h}"
         gs-min-w="${meta.min_size?.w || 1}" gs-min-h="${meta.min_size?.h || 1}">
      <div class="grid-stack-item-content"${tint}>
        <span class="widget-badge"></span>
        <div class="widget-chrome">
          <span class="widget-grip" title="Drag to move">⠿</span>
          <span class="chrome-title">${escapeHtml(name)}</span>
          <span class="chrome-actions">
            <button class="mini-btn" data-act="color" data-id="${rec.id}" title="Card color">🎨</button>
            <button class="mini-btn" data-act="settings" data-id="${rec.id}" title="Settings">⚙</button>
            <button class="mini-btn" data-act="response" data-id="${rec.id}" title="Trigger response">⚡</button>
            <button class="mini-btn" data-act="remove" data-id="${rec.id}" title="Remove">✕</button>
          </span>
        </div>
        <div class="widget-body"></div>
      </div>
    </div>`;
}

async function renderTab(layout) {
  for (const id of [...mounted.keys()]) unmountWidget(id);
  recById.clear();

  const host = $("#grid");
  host.innerHTML = "";
  if (grid) { grid.destroy(false); grid = null; }

  grid = GridStack.init({
    column: layout.grid.columns,
    cellHeight: layout.grid.row_height,
    margin: 8,
    float: true,
    // Always editable: drag only from the grip so widget interaction (sliders,
    // rows) isn't hijacked; resize from edges.
    draggable: { handle: ".widget-grip" },
  }, host);

  for (const rec of layout.instances) grid.addWidget(gridItemHtml(rec));
  // Mount widget frontends in parallel so a tab with several widgets doesn't
  // pay one round-trip per widget sequentially.
  await Promise.all(layout.instances.map(mountWidget));

  grid.on("change", (_ev, items) => {
    const positions = items.map((i) => ({
      id: i.el.getAttribute("gs-id"), x: i.x, y: i.y, w: i.w, h: i.h,
    }));
    if (positions.length) queueLayoutSave(positions);
  });

  $("#empty-hint").classList.toggle("hidden", layout.instances.length > 0);
}

// Coalesce the burst of change events a drag/resize emits into one save.
let layoutSaveTimer = null;
let pendingPositions = null;
function queueLayoutSave(positions) {
  pendingPositions = positions;
  markDirty(currentTab);                 // local + cheap; reflects immediately
  clearTimeout(layoutSaveTimer);
  layoutSaveTimer = setTimeout(() => {
    if (pendingPositions) api.put("/api/layout", { positions: pendingPositions });
    pendingPositions = null;
  }, 400);
}

// --- tab bar --------------------------------------------------------------

function renderTabs(tabs) {
  if (tabs) tabsState = tabs;
  const el = $("#tabs");
  el.innerHTML = "";
  for (const t of tabsState) {
    const tab = document.createElement("div");
    tab.className = "tab " + t.state + (t.dirty ? " dirty" : "");
    tab.draggable = true;
    tab.dataset.name = t.name;
    tab.innerHTML = `<span class="dot" title="toggle run-state"></span>
                     <span class="tab-name">${escapeHtml(t.name)}</span>
                     <span class="unsaved" title="unsaved changes">●</span>`;

    tab.querySelector(".dot").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (t.state === "selected") return;
      const action = t.state === "enabled" ? "disable" : "enable";
      const res = await api.post(`/api/tabs/${enc(t.name)}/${action}`);
      renderTabs(res.tabs);
    });
    tab.addEventListener("click", () => selectTab(t.name));
    tab.addEventListener("contextmenu", (e) => { e.preventDefault(); tabContextMenu(e, t.name); });
    wireTabDrag(tab);
    el.appendChild(tab);
  }
  updateToolbar();
}

function updateToolbar() {
  const cur = tabsState.find((t) => t.name === currentTab);
  const dirty = !!(cur && cur.dirty);
  $("#save-btn").classList.toggle("disabled", !dirty);
  $("#revert-btn").classList.toggle("disabled", !dirty);
}

function markDirty(name) {
  const t = tabsState.find((x) => x.name === name);
  if (!t) return;
  if (!t.dirty) {
    t.dirty = true;
    // Toggle the one tab's class instead of rebuilding the whole tab bar.
    const tabEl = document.querySelector(`#tabs .tab[data-name="${CSS.escape(name)}"]`);
    if (tabEl) tabEl.classList.add("dirty"); else renderTabs();
  }
  updateToolbar();
}

let dragSrc = null;
function wireTabDrag(tab) {
  tab.addEventListener("dragstart", () => { dragSrc = tab.dataset.name; tab.classList.add("dragging"); });
  tab.addEventListener("dragend", () => tab.classList.remove("dragging"));
  tab.addEventListener("dragover", (e) => e.preventDefault());
  tab.addEventListener("drop", async (e) => {
    e.preventDefault();
    if (!dragSrc || dragSrc === tab.dataset.name) return;
    const names = [...$("#tabs").querySelectorAll(".tab")].map((t) => t.dataset.name);
    names.splice(names.indexOf(dragSrc), 1);
    names.splice(names.indexOf(tab.dataset.name), 0, dragSrc);
    const res = await api.post("/api/tabs/reorder", { order: names });
    renderTabs(res.tabs);
  });
}

async function selectTab(name) {
  const layout = await api.post(`/api/tabs/${enc(name)}/select`);
  currentTab = name;
  renderTabs(layout.tabs);
  await renderTab(layout);
}

function tabContextMenu(e, name) {
  openMenu(e.clientX, e.clientY, [
    { label: "Rename", fn: async () => {
        const next = await modalPrompt("Rename tab", { value: name });
        if (next && next !== name) {
          const res = await api.post(`/api/tabs/${enc(name)}/rename`, { new: next });
          if (currentTab === name) currentTab = next;
          renderTabs(res.tabs);
        }
      } },
    { label: "Duplicate", fn: async () => {
        const res = await api.post(`/api/tabs/${enc(name)}/duplicate`);
        renderTabs(res.tabs);
      } },
    { label: "Save as preset…", fn: () => saveAsPreset(name) },
    { label: "Delete", danger: true, fn: async () => {
        if (!(await modalConfirm("Delete tab", `Delete tab "${name}"?`, { danger: true }))) return;
        const res = await api.del(`/api/tabs/${enc(name)}`);
        renderTabs(res.tabs);
        if (currentTab === name && res.selected) selectTab(res.selected);
      } },
  ]);
}

// --- save / load / revert + presets ---------------------------------------

async function saveTab() {
  if (!currentTab) return;
  const res = await api.post(`/api/tabs/${enc(currentTab)}/save`);
  renderTabs(res.tabs);
  toast({ text: "saved" });
}

async function revertTab() {
  if (!currentTab) return;
  const cur = tabsState.find((t) => t.name === currentTab);
  if (cur && cur.dirty && !(await modalConfirm("Revert", "Discard unsaved changes on this tab?", { danger: true }))) return;
  const layout = await api.post(`/api/tabs/${enc(currentTab)}/revert`);
  renderTabs(layout.tabs);
  await renderTab(layout);
  toast({ text: "reverted" });
}

async function saveAsPreset(fromTab) {
  const name = await modalPrompt("Save as preset", { placeholder: "preset name" });
  if (!name) return;
  await api.post("/api/presets", { name, from_tab: fromTab });
  toast({ text: `saved preset "${name}"` });
}

async function openLoadMenu(e) {
  if (e) e.stopPropagation();   // don't let this click reach the document closer
  const { presets } = await api.get("/api/presets");
  const items = presets.map((p) => ({
    label: p,
    fn: async () => {
      const layout = await api.post(`/api/tabs/${enc(currentTab)}/load-preset`, { preset: p });
      renderTabs(layout.tabs);
      await renderTab(layout);
      toast({ text: `loaded "${p}"` });
    },
    delFn: async () => { await api.del(`/api/presets/${enc(p)}`); toast({ text: `deleted preset "${p}"` }); },
  }));
  items.push({ separator: true });
  items.push({ label: "＋ Save current as preset…", fn: () => saveAsPreset(currentTab) });
  if (!presets.length) items.unshift({ label: "no presets yet", disabled: true });
  const r = $("#load-btn").getBoundingClientRect();
  openMenu(r.left, r.bottom + 4, items);
}

// --- picker ---------------------------------------------------------------

let pickerCategory = "all";
let pickerWidgets = [];
let pickerQuery = "";

async function openPicker() {
  pickerWidgets = await api.get("/api/widgets");
  pickerQuery = "";
  $("#picker-search").value = "";
  const cats = ["all", ...[...new Set(pickerWidgets.map((w) => w.category))].sort()];
  $("#picker-cats").innerHTML = cats.map((c) =>
    `<button class="pcat ${c === pickerCategory ? "active" : ""}" data-cat="${c}">${c}</button>`).join("");
  $("#picker-cats").querySelectorAll(".pcat").forEach((b) => {
    b.onclick = () => {
      pickerCategory = b.dataset.cat;
      $("#picker-cats").querySelectorAll(".pcat").forEach((x) => x.classList.toggle("active", x === b));
      renderPicker();
    };
  });
  renderPicker();
  $("#picker-overlay").classList.remove("hidden");
  $("#picker-search").focus();
}

function renderPicker() {
  const list = $("#picker-list");
  list.innerHTML = "";
  const q = pickerQuery.toLowerCase();
  const shown = pickerWidgets.filter((w) =>
    (pickerCategory === "all" || w.category === pickerCategory) &&
    (!q || w.name.toLowerCase().includes(q) || (w.description || "").toLowerCase().includes(q)));
  if (!shown.length) { list.innerHTML = `<div class="picker-empty">no widgets match</div>`; return; }
  for (const w of shown) {
    const caps = [
      ...(w.host_services || []).map((s) => `host:${s}`),
      ...(w.event_sources || []).map((s) => `event:${s}`),
    ];
    const item = document.createElement("button");
    item.className = "picker-item";
    if (!w.available) item.setAttribute("disabled", "");
    item.innerHTML = `
      <div class="pi-icon">${iconFor(w)}</div>
      <div class="pi-main">
        <div class="pi-top"><span class="pi-name">${escapeHtml(w.name)}</span>
          <span class="pi-mode">${w.instance_mode}</span></div>
        <div class="pi-desc">${w.available ? escapeHtml(w.description) : escapeHtml(w.unavailable_reason || "unavailable")}</div>
        ${caps.length ? `<div class="pi-caps">${caps.map((c) => `<span class="cap">${c}</span>`).join("")}</div>` : ""}
      </div>`;
    if (w.available) {
      item.onclick = async () => {
        const rec = await api.post("/api/instances", { widget_id: w.id });
        $("#picker-overlay").classList.add("hidden");
        if (!rec.error) {
          grid.addWidget(gridItemHtml(rec));
          await mountWidget(rec);
          $("#empty-hint").classList.add("hidden");
          markDirty(currentTab);
        } else toast({ text: rec.error });
      };
    }
    list.appendChild(item);
  }
}

const CAT_ICONS = { system: "🖥", audio: "🔊", windows: "🗔", media: "🎬",
  productivity: "⌨", info: "🕓", custom: "✦" };
function iconFor(w) { return CAT_ICONS[w.category] || "✦"; }

// --- settings drawer ------------------------------------------------------

let drawerCleanup = null;

function closeDrawer() {
  if (drawerCleanup) { try { drawerCleanup(); } catch {} drawerCleanup = null; }
  $("#drawer").classList.add("hidden");
  $("#drawer-body").innerHTML = "";
}

async function openSettings(instanceId) {
  const rec = recById.get(instanceId);
  if (!rec) return;
  const meta = widgetMeta[rec.widget_id] || {};
  $("#drawer-title").textContent = `${meta.name || rec.widget_id} — settings`;
  const body = $("#drawer-body");
  body.innerHTML = "";
  const mod = await loadWidgetModule(rec.widget_id, "settings.js");
  if (!mod) { body.innerHTML = `<div class="drawer-empty">This widget has no settings.</div>`; $("#drawer").classList.remove("hidden"); return; }
  drawerCleanup = mod.mount(body, {
    settings: rec.settings || {},
    save: async (next) => {
      await api.put(`/api/instances/${instanceId}/settings`, next);
      rec.settings = next;
      markDirty(currentTab);
      closeDrawer();
      toast({ text: "settings updated (Save the tab to keep)" });
    },
    cancel: () => closeDrawer(),
  }) || null;
  $("#drawer").classList.remove("hidden");
}

async function openResponse(instanceId) {
  const rec = recById.get(instanceId);
  if (!rec) return;
  const meta = widgetMeta[rec.widget_id] || {};
  const r = await api.get(`/api/instances/${instanceId}/response`);
  $("#drawer-title").textContent = `${meta.name || rec.widget_id} — trigger response`;
  const body = $("#drawer-body");
  const toastCfg = r.toast || { enabled: false, text: "" };
  const soundCfg = r.sound || { enabled: false };
  body.innerHTML = `
    <p class="drawer-note">How the dashboard reacts when this widget fires a trigger.
      The widget decides <em>when</em>; you decide <em>how</em>.</p>
    <form class="wd-form">
      <label class="wd-check"><input name="badge" type="checkbox" ${r.badge ? "checked" : ""}/> Badge the widget</label>
      <label class="wd-check"><input name="toast_en" type="checkbox" ${toastCfg.enabled ? "checked" : ""}/> Show a toast</label>
      <label class="wd-when-toast">Toast text
        <input name="toast_text" type="text" value="${attr(toastCfg.text)}" placeholder="e.g. {app}: {summary}" />
      </label>
      <label class="wd-check"><input name="sound" type="checkbox" ${soundCfg.enabled ? "checked" : ""}/> Play a sound</label>
      <label class="wd-check"><input name="flash" type="checkbox" ${r.flash ? "checked" : ""}/> Flash the widget</label>
      <label class="wd-check"><input name="overlay" type="checkbox" ${r.overlay ? "checked" : ""}/> Full overlay</label>
      <label class="wd-check"><input name="reveal" type="checkbox" ${r.reveal ? "checked" : ""}/> Reveal if hidden</label>
      <label class="wd-check"><input name="switch_to_tab" type="checkbox" ${r.switch_to_tab ? "checked" : ""}/> Switch to this tab</label>
      <div class="wd-form-actions">
        <button type="button" data-test class="pill-btn">Test</button>
        <button type="submit" class="pill-btn primary">Apply</button>
      </div>
    </form>`;
  const form = body.querySelector("form");
  const sync = () => { body.querySelector(".wd-when-toast").style.display = form.toast_en.checked ? "" : "none"; };
  form.toast_en.onchange = sync; sync();
  const collect = () => ({
    badge: form.badge.checked,
    toast: { enabled: form.toast_en.checked, text: form.toast_text.value },
    sound: { enabled: form.sound.checked },
    flash: form.flash.checked,
    overlay: form.overlay.checked,
    reveal: form.reveal.checked,
    switch_to_tab: form.switch_to_tab.checked,
  });
  form.onsubmit = async (e) => {
    e.preventDefault();
    await api.put(`/api/instances/${instanceId}/response`, collect());
    markDirty(currentTab);
    closeDrawer();
    toast({ text: "response updated (Save the tab to keep)" });
  };
  body.querySelector("[data-test]").onclick = () => {
    applyResponse({ instance_id: instanceId, widget_id: rec.widget_id, response: collect(),
                    payload: { app: "Test", summary: "trigger preview" } });
  };
  drawerCleanup = null;
  $("#drawer").classList.remove("hidden");
}

// --- per-widget card color + global theme ---------------------------------

const CARD_COLORS = ["", "#ffd9d6", "#ffe6cc", "#fff4cc", "#f0f6c8", "#dff5d8",
  "#d2f5ec", "#d4f1fb", "#dbe8ff", "#e2e0ff", "#efe0ff", "#ffe0ef", "#e9edf0"];

function openColorPopup(id, anchor) {
  const rec = recById.get(id);
  const cur = (rec && rec.color) || "";
  const menu = $("#ctx-menu");
  menu.innerHTML = `<div class="color-grid">${CARD_COLORS.map((c) =>
    `<button class="swatch ${c === cur ? "sel" : ""} ${c === "" ? "none" : ""}"
             data-c="${c}" title="${c || "default"}" style="--sw:${c || "var(--wd-panel)"}"></button>`
  ).join("")}</div>`;
  menu.querySelectorAll(".swatch").forEach((b) => b.onclick = async () => {
    closeMenu();
    const color = b.dataset.c;
    if (rec) rec.color = color;
    const content = document.querySelector(`[gs-id="${id}"] .grid-stack-item-content`);
    if (content) content.style.backgroundColor = color || "";
    await api.put(`/api/instances/${id}/color`, { color });
    markDirty(currentTab);
  });
  const r = anchor.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(r.left - 80, innerWidth - 190))}px`;
  menu.style.top = `${r.bottom + 4}px`;
  menu.classList.remove("hidden");
}

// theme color math
function hexN(h) { h = (h || "").replace("#", ""); if (h.length === 3) h = h.split("").map((c) => c + c).join(""); return [parseInt(h.slice(0, 2), 16) || 0, parseInt(h.slice(2, 4), 16) || 0, parseInt(h.slice(4, 6), 16) || 0]; }
function toHex(r, g, b) { const f = (x) => Math.max(0, Math.min(255, Math.round(x))).toString(16).padStart(2, "0"); return "#" + f(r) + f(g) + f(b); }
function mix(c1, c2, t) { const a = hexN(c1), b = hexN(c2); return toHex(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t); }
function lum(hex) { const v = hexN(hex).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; }

function applyTheme(accent, bg) {
  accent = accent || "#19e3cf"; bg = bg || "#ffffff";
  const dark = lum(bg) < 0.4;
  const s = (k, v) => document.documentElement.style.setProperty(k, v);
  s("--wd-bg", bg);
  s("--wd-panel", dark ? mix(bg, "#ffffff", 0.10) : "#ffffff");
  s("--wd-panel-2", dark ? mix(bg, "#ffffff", 0.16) : mix(bg, "#ffffff", 0.55));
  s("--wd-edge", dark ? mix(bg, "#ffffff", 0.22) : mix(bg, "#000000", 0.10));
  s("--wd-fg", dark ? "#eef3f4" : "#14201f");
  s("--wd-fg-dim", dark ? "#9aa6a8" : "#7a8688");
  s("--wd-accent", accent);
  s("--wd-accent-strong", dark ? mix(accent, "#ffffff", 0.18) : mix(accent, "#000000", 0.18));
  s("--wd-accent-soft", dark ? mix(accent, bg, 0.78) : mix(accent, "#ffffff", 0.82));
  s("--wd-on-accent", lum(accent) > 0.55 ? "#06302c" : "#ffffff");
  s("--wd-scrim", dark ? "rgba(0,0,0,0.5)" : "rgba(20,40,60,0.18)");
}

let themeState = { accent: "#19e3cf", bg: "#ffffff" };
const THEME_PRESETS = [
  ["Aqua", "#19e3cf", "#ffffff"], ["Indigo", "#5b6cff", "#ffffff"],
  ["Coral", "#ff6b6b", "#fff8f5"], ["Forest", "#10b981", "#f6fbf8"],
  ["Amber", "#f59e0b", "#fffdf5"], ["Dark", "#19e3cf", "#16202b"],
];
function toColorInput(hex) { return /^#[0-9a-fA-F]{6}$/.test(hex || "") ? hex : "#ffffff"; }

function openThemeDialog() {
  $("#modal-title").textContent = "Theme";
  $("#modal-body").innerHTML = `
    <div class="theme-row"><label>Accent color</label><input type="color" id="th-accent" value="${toColorInput(themeState.accent)}"></div>
    <div class="theme-row"><label>Base (page) color</label><input type="color" id="th-bg" value="${toColorInput(themeState.bg)}"></div>
    <div class="theme-presets">${THEME_PRESETS.map(([n, a, b]) =>
      `<button class="pill-btn" data-a="${a}" data-b="${b}">${n}</button>`).join("")}</div>`;
  $("#modal-actions").innerHTML = `
    <button data-reset class="pill-btn">Reset</button>
    <button data-done class="pill-btn primary">Done</button>`;
  const overlay = $("#modal-overlay"); overlay.classList.remove("hidden");
  const acc = $("#th-accent"), bgc = $("#th-bg");
  const live = () => { themeState = { accent: acc.value, bg: bgc.value }; applyTheme(acc.value, bgc.value); };
  acc.oninput = live; bgc.oninput = live;
  $("#modal-body").querySelectorAll("[data-a]").forEach((b) =>
    b.onclick = () => { acc.value = b.dataset.a; bgc.value = b.dataset.b; live(); });
  $("#modal-actions").querySelector("[data-done]").onclick = async () => {
    await api.put("/api/config", { theme: themeState }); overlay.classList.add("hidden");
  };
  $("#modal-actions").querySelector("[data-reset]").onclick = () => { acc.value = "#19e3cf"; bgc.value = "#ffffff"; live(); };
}

// --- system channel: triggers, badges, lockdown, install ------------------

function connectSystem() {
  const ws = new WebSocket(`ws://${location.host}/api/system/ws`);
  ws.addEventListener("message", (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    switch (m.type) {
      case "trigger": applyResponse(m); break;
      case "lockdown":
        if (m.event === "bounce") toast({ text: `lockdown: bounced ${m.wm_class || "a window"}` });
        break;
      case "install_prompt": showInstallDialog(m); break;
      case "install_timeout": toast({ text: "no .wdwidget appeared (timed out)" }); break;
      case "install_error": toast({ text: `install error: ${m.error}` }); break;
    }
  });
  ws.addEventListener("close", () => setTimeout(connectSystem, 2000));
}

function applyResponse(m) {
  const r = m.response || {};
  if (r.toast && r.toast.enabled) toast({ text: r.toast.text || `${m.widget_id} triggered` });
  if (r.badge) badgeWidget(m.instance_id);
  if (r.flash) flashWidget(m.instance_id);
  if (r.reveal) handleControl(m.instance_id, { action: "reveal" });
  if (r.sound && r.sound.enabled) beep();
  if (r.overlay) showOverlay(r.toast?.text || `${m.widget_id} triggered`);
}

function badgeWidget(id) {
  const node = document.querySelector(`[gs-id="${id}"] .widget-badge`);
  if (node) node.classList.add("on");
}
function flashWidget(id) {
  const node = document.querySelector(`[gs-id="${id}"] .grid-stack-item-content`);
  if (!node) return;
  node.classList.remove("flash"); void node.offsetWidth; node.classList.add("flash");
  setTimeout(() => node.classList.remove("flash"), 1200);
}
function showOverlay(text) {
  const el = $("#trigger-overlay");
  el.textContent = text;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 2500);
}
let audioCtx = null;
function beep() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.frequency.value = 880; o.connect(g); g.connect(audioCtx.destination);
    g.gain.setValueAtTime(0.0001, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.12, audioCtx.currentTime + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.25);
    o.start(); o.stop(audioCtx.currentTime + 0.26);
  } catch {}
}

// --- toasts ---------------------------------------------------------------

function toast({ text }) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = text;
  $("#toasts").appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));
  setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 300); }, 4000);
}

// --- install dialog (permission confirm) ----------------------------------

function showInstallDialog(info) {
  const perms = info.permissions || {};
  const permRows = Object.entries(perms).filter(([, v]) => v && (!Array.isArray(v) || v.length))
    .map(([k, v]) => `<li><b>${k}</b>: ${Array.isArray(v) ? v.join(", ") : "yes"}</li>`).join("");
  const hs = (info.host_services || []).map((s) => `<span class="cap">host:${s}</span>`).join("");
  const reqs = (info.requires?.commands || []).map((c) => `<span class="cap">cmd:${c}</span>`).join("");
  $("#install-title").textContent = `Install ${info.widget_id} v${info.version}`;
  $("#install-body").innerHTML = `
    ${info.already_installed ? `<p class="install-warn">A widget with this id is already installed — this will update it.</p>` : ""}
    <p class="drawer-note">Review what this widget can do before trusting it:</p>
    <div class="install-section"><div class="install-h">Permissions</div>
      <ul>${permRows || "<li>none declared</li>"}</ul></div>
    ${hs ? `<div class="install-section"><div class="install-h">Host services</div><div class="caps">${hs}</div></div>` : ""}
    ${reqs ? `<div class="install-section"><div class="install-h">Requires</div><div class="caps">${reqs}</div></div>` : ""}
    <div class="modal-actions">
      <button data-cancel class="pill-btn">Cancel</button>
      <button data-confirm class="pill-btn primary">Trust &amp; install</button>
    </div>`;
  const close = () => $("#install-overlay").classList.add("hidden");
  $("#install-body").querySelector("[data-cancel]").onclick = close;
  $("#install-body").querySelector("[data-confirm]").onclick = async () => {
    const res = await api.post("/api/widgets/install", { staged: info.staged });
    close();
    toast({ text: res.ok ? `installed ${info.widget_id}` : `install failed: ${res.error}` });
    if (res.ok) widgetMeta = Object.fromEntries((await api.get("/api/widgets")).map((w) => [w.id, w]));
  };
  $("#install-overlay").classList.remove("hidden");
}

function uploadWidget() {
  const input = document.createElement("input");
  input.type = "file"; input.accept = ".wdwidget";
  input.onchange = async () => {
    if (!input.files.length) return;
    const fd = new FormData();
    fd.append("file", input.files[0]);
    const res = await (await fetch("/api/widgets/upload", { method: "POST", body: fd })).json();
    if (res.ok) showInstallDialog(res);
    else toast({ text: `invalid widget: ${res.error}` });
  };
  input.click();
}

// --- system menu (⋯) ------------------------------------------------------

function openSysMenu(e) {
  if (e) e.stopPropagation();
  const r = $("#sysmenu-btn").getBoundingClientRect();
  openMenu(r.right - 200, r.bottom + 4, [
    { label: "Upload a .wdwidget…", fn: uploadWidget },
    { label: "Install next download", fn: async () => {
        await api.post("/api/widgets/install-next-download");
        toast({ text: "watching Downloads for a .wdwidget (2 min)…" });
      } },
    { label: "Rescan widgets", fn: async () => {
        await api.post("/api/widgets/rescan");
        widgetMeta = Object.fromEntries((await api.get("/api/widgets")).map((w) => [w.id, w]));
        toast({ text: "widgets rescanned" });
      } },
    { separator: true },
    { label: "Theme…", fn: openThemeDialog },
    { label: "Pause lockdown (5 min)", fn: async () => {
        await api.post("/api/lockdown/pause", { minutes: 5 });
        toast({ text: "lockdown paused for 5 min" });
      } },
  ]);
}

// --- popup menu + styled modals -------------------------------------------

function openMenu(x, y, items) {
  const menu = $("#ctx-menu");
  menu.innerHTML = "";
  items.forEach((it) => {
    if (it.separator) { const hr = document.createElement("div"); hr.className = "menu-sep"; menu.appendChild(hr); return; }
    const row = document.createElement("div");
    row.className = "menu-row";
    const b = document.createElement("button");
    b.className = it.danger ? "danger" : "";
    if (it.disabled) b.setAttribute("disabled", "");
    b.textContent = it.label;
    if (!it.disabled && it.fn) b.onclick = () => { closeMenu(); it.fn(); };
    row.appendChild(b);
    if (it.delFn) {
      const d = document.createElement("button");
      d.className = "menu-del"; d.textContent = "✕"; d.title = "delete preset";
      d.onclick = (ev) => { ev.stopPropagation(); closeMenu(); it.delFn(); };
      row.appendChild(d);
    }
    menu.appendChild(row);
  });
  menu.style.left = `${Math.max(8, Math.min(x, innerWidth - 220))}px`;
  menu.style.top = `${y}px`;
  menu.classList.remove("hidden");
}
function closeMenu() { $("#ctx-menu").classList.add("hidden"); }
document.addEventListener("click", (e) => { if (!e.target.closest("#ctx-menu")) closeMenu(); });

function modalPrompt(title, { value = "", placeholder = "", label = "" } = {}) {
  return new Promise((resolve) => {
    $("#modal-title").textContent = title;
    $("#modal-body").innerHTML =
      `${label ? `<div class="modal-label">${escapeHtml(label)}</div>` : ""}
       <input id="modal-input" class="modal-input" type="text" value="${attr(value)}" placeholder="${attr(placeholder)}" />`;
    $("#modal-actions").innerHTML =
      `<button data-cancel class="pill-btn">Cancel</button>
       <button data-ok class="pill-btn primary">OK</button>`;
    const overlay = $("#modal-overlay");
    overlay.classList.remove("hidden");
    const input = $("#modal-input");
    input.focus(); input.select();
    const done = (v) => { overlay.classList.add("hidden"); resolve(v); };
    $("#modal-actions").querySelector("[data-ok]").onclick = () => done(input.value.trim() || null);
    $("#modal-actions").querySelector("[data-cancel]").onclick = () => done(null);
    input.onkeydown = (e) => { if (e.key === "Enter") done(input.value.trim() || null); if (e.key === "Escape") done(null); };
  });
}

function modalConfirm(title, message, { danger = false } = {}) {
  return new Promise((resolve) => {
    $("#modal-title").textContent = title;
    $("#modal-body").innerHTML = `<div class="modal-message">${escapeHtml(message)}</div>`;
    $("#modal-actions").innerHTML =
      `<button data-cancel class="pill-btn">Cancel</button>
       <button data-ok class="pill-btn ${danger ? "danger" : "primary"}">${danger ? "Delete" : "OK"}</button>`;
    const overlay = $("#modal-overlay");
    overlay.classList.remove("hidden");
    const done = (v) => { overlay.classList.add("hidden"); resolve(v); };
    $("#modal-actions").querySelector("[data-ok]").onclick = () => done(true);
    $("#modal-actions").querySelector("[data-cancel]").onclick = () => done(false);
  });
}

// --- helpers --------------------------------------------------------------

function escapeHtml(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function attr(v) { return v == null ? "" : String(v).replace(/"/g, "&quot;"); }
function enc(s) { return encodeURIComponent(s); }

function startMiniClock() {
  const el = $("#clock-mini");
  const tick = () => { el.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); };
  tick(); setInterval(tick, 10000);
}

// --- wiring ---------------------------------------------------------------

$("#fab-add").addEventListener("click", openPicker);
$("#empty-add").addEventListener("click", openPicker);
$("#save-btn").addEventListener("click", saveTab);
$("#revert-btn").addEventListener("click", revertTab);
$("#load-btn").addEventListener("click", openLoadMenu);
$("#sysmenu-btn").addEventListener("click", openSysMenu);
$("#add-tab").addEventListener("click", async () => {
  const name = await modalPrompt("New tab", { placeholder: "tab name" });
  if (!name) return;
  const res = await api.post(`/api/tabs/${enc(name)}/create`);
  renderTabs(res.tabs);
});
document.querySelectorAll("[data-close-picker]").forEach((b) => b.onclick = () => $("#picker-overlay").classList.add("hidden"));
document.querySelectorAll("[data-close-install]").forEach((b) => b.onclick = () => $("#install-overlay").classList.add("hidden"));
$("#drawer-close").addEventListener("click", closeDrawer);
$("#picker-search").addEventListener("input", (e) => { pickerQuery = e.target.value; renderPicker(); });
$("#picker-overlay").addEventListener("click", (e) => { if (e.target.id === "picker-overlay") $("#picker-overlay").classList.add("hidden"); });

// Grid chrome clicks via delegation.
$("#grid").addEventListener("click", async (e) => {
  const btn = e.target.closest(".mini-btn");
  if (btn) {
    const id = btn.dataset.id;
    if (btn.dataset.act === "remove") {
      await api.del(`/api/instances/${id}`);
      unmountWidget(id);
      const node = document.querySelector(`[gs-id="${id}"]`);
      if (node && grid) grid.removeWidget(node);
      recById.delete(id);
      markDirty(currentTab);
    } else if (btn.dataset.act === "settings") openSettings(id);
    else if (btn.dataset.act === "response") openResponse(id);
    else if (btn.dataset.act === "color") { e.stopPropagation(); openColorPopup(id, btn); }
    return;
  }
  const item = e.target.closest(".grid-stack-item");
  if (item) { const b = item.querySelector(".widget-badge"); if (b) b.classList.remove("on"); }
});

// boot
(async function init() {
  // Independent — fetch in parallel to shave a round-trip off cold start.
  const [widgets, tabsResp, config] = await Promise.all([
    api.get("/api/widgets"), api.get("/api/tabs"), api.get("/api/config"),
  ]);
  if (config && config.theme) {
    themeState = { accent: config.theme.accent || "#19e3cf", bg: config.theme.bg || "#ffffff" };
    applyTheme(themeState.accent, themeState.bg);
  }
  widgetMeta = Object.fromEntries(widgets.map((w) => [w.id, w]));
  connectSystem();
  startMiniClock();
  const { tabs, selected } = tabsResp;
  tabsState = tabs;
  if (selected) { currentTab = selected; await selectTab(selected); }
  else if (tabs.length) { currentTab = tabs[0].name; await selectTab(tabs[0].name); }
  else renderTabs(tabs);
})();
