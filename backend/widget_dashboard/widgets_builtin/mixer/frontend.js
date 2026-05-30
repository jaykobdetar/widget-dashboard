// Audio Mixer frontend (state_intents).
//
// Output/input device pickers with a type icon, a filled master/mic slider,
// and a per-application list with avatars, a "what's playing" line, paused
// state, a boost zone past 100%, mute dimming, and scroll-to-adjust.
//
// Builds its DOM once and PATCHES it on each state update (never rebuilds),
// so live pushes don't drop handlers or reset a slider you're dragging.

const VOL_INTENT = { sink: "set_sink_volume", source: "set_source_volume", stream: "set_stream_volume" };
const MUTE_INTENT = { sink: "set_sink_mute", source: "set_source_mute", stream: "set_stream_mute" };
const MAX = 150;
const DEV_EMOJI = { speaker: "🔊", headphones: "🎧", hdmi: "🖥️", bluetooth: "🎧", mic: "🎙️" };
const APP_EMOJI = [
  ["firefox", "🦊"], ["chrom", "🌐"], ["spotify", "🎧"], ["mpv", "🎬"],
  ["vlc", "🎬"], ["steam", "🎮"], ["discord", "💬"], ["zoom", "📹"],
  ["telegram", "✈️"], ["mpd", "🎵"], ["obs", "🎥"],
];
const ROLE_EMOJI = { music: "🎵", video: "🎬", game: "🎮", phone: "📞", event: "🔔" };

function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function el(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }
function clamp(v) { return Math.max(0, Math.min(MAX, v)); }

function avatarHtml(s) {
  const bin = s.binary || "", name = (s.app || "").toLowerCase();
  for (const [k, e] of APP_EMOJI) if (bin.includes(k) || name.includes(k)) return { emoji: e };
  if (ROLE_EMOJI[(s.role || "").toLowerCase()]) return { emoji: ROLE_EMOJI[s.role.toLowerCase()] };
  return { letter: ((s.app || "?").trim()[0] || "?").toUpperCase() };
}

function setFill(row, slider, v) {
  slider.style.setProperty("--fill", `${(clamp(v) / MAX * 100).toFixed(1)}%`);
  row.classList.toggle("boost", v > 100);
}

// Shared slider behaviour: drag (don't fight live updates), commit on change,
// and scroll-to-adjust on the whole row.
function wireSlider(row, slider, pct, api, target) {
  let dragging = false, wheelTimer = null;
  const send = (v) => { const t = target(); api.intent(VOL_INTENT[t.kind], { id: t.id, pct: v }); };
  slider.addEventListener("pointerdown", () => { dragging = true; });
  ["pointerup", "pointercancel", "blur"].forEach((ev) => slider.addEventListener(ev, () => { dragging = false; }));
  slider.addEventListener("input", () => { pct.textContent = `${slider.value}%`; setFill(row, slider, Number(slider.value)); });
  slider.addEventListener("change", () => { dragging = false; send(Number(slider.value)); });
  row.addEventListener("wheel", (e) => {
    e.preventDefault();
    const v = clamp(Number(slider.value) + (e.deltaY < 0 ? 3 : -3));
    slider.value = v; pct.textContent = `${v}%`; setFill(row, slider, v);
    clearTimeout(wheelTimer); wheelTimer = setTimeout(() => send(v), 140);
  }, { passive: false });
  row._dragging = () => dragging;
}

// A device volume row (master / mic): [mute] [filled slider] [%].
function makeDeviceRow(api) {
  const row = el("div", "mx-row");
  row.innerHTML = `<button class="mx-mute" title="Mute">🔊</button>
    <input class="mx-slider" type="range" min="0" max="${MAX}" />
    <span class="mx-pct"></span>`;
  const slider = row.querySelector(".mx-slider"), pct = row.querySelector(".mx-pct"), mute = row.querySelector(".mx-mute");
  wireSlider(row, slider, pct, api, () => ({ kind: row.dataset.kind, id: Number(row.dataset.id) }));
  mute.addEventListener("click", () => api.intent(MUTE_INTENT[row.dataset.kind], { id: Number(row.dataset.id), muted: !row.classList.contains("muted") }));
  row.update = (kind, id, volume, muted) => {
    row.dataset.kind = kind; row.dataset.id = id;
    mute.textContent = muted ? "🔇" : "🔊";
    row.classList.toggle("muted", muted);
    if (!row._dragging()) { if (Number(slider.value) !== volume) slider.value = volume; pct.textContent = `${volume}%`; setFill(row, slider, volume); }
  };
  return row;
}

// An application row: [avatar] [name + what's-playing / slider] [% + mute].
function makeAppRow(api) {
  const row = el("div", "mx-row mx-app");
  row.innerHTML = `
    <div class="mx-av"></div>
    <div class="mx-mid">
      <div class="mx-line"><span class="mx-name"></span><span class="mx-sub"></span></div>
      <input class="mx-slider" type="range" min="0" max="${MAX}" />
      <div class="mx-meter"><div class="mx-meter-fill"></div></div>
    </div>
    <div class="mx-right"><span class="mx-pct"></span><button class="mx-mute" title="Mute">🔊</button></div>`;
  const meterFill = row.querySelector(".mx-meter-fill");
  // sqrt curve = perceptual loudness, so normal audio fills the bar visibly
  // instead of hugging the low end.
  row.setLevel = (peak) => {
    meterFill.style.width = `${Math.min(100, Math.sqrt(Math.max(0, peak)) * 100).toFixed(1)}%`;
  };
  const slider = row.querySelector(".mx-slider"), pct = row.querySelector(".mx-pct");
  const name = row.querySelector(".mx-name"), sub = row.querySelector(".mx-sub");
  const av = row.querySelector(".mx-av"), mute = row.querySelector(".mx-mute");
  wireSlider(row, slider, pct, api, () => ({ kind: "stream", id: Number(row.dataset.id) }));
  mute.addEventListener("click", () => api.intent("set_stream_mute", { id: Number(row.dataset.id), muted: !row.classList.contains("muted") }));
  row.update = (s) => {
    row.dataset.id = s.id;
    if (name.textContent !== s.app) name.textContent = s.app;
    const subTxt = s.corked ? "paused" : (s.media || "");
    if (sub.textContent !== subTxt) sub.textContent = subTxt;
    row.classList.toggle("paused", !!s.corked);
    const a = avatarHtml(s);
    const avTxt = a.emoji || a.letter;
    if (av.textContent !== avTxt) { av.textContent = avTxt; av.classList.toggle("letter", !a.emoji); }
    mute.textContent = s.muted ? "🔇" : "🔊";
    row.classList.toggle("muted", s.muted);
    if (!row._dragging()) { if (Number(slider.value) !== s.volume) slider.value = s.volume; pct.textContent = `${s.volume}%`; setFill(row, slider, s.volume); }
  };
  return row;
}

function syncSelect(sel, iconEl, devices, onPick) {
  const sig = devices.map((d) => `${d.name}${d.desc}`).join("");
  if (sel._sig !== sig) {
    sel._sig = sig;
    sel.innerHTML = devices.map((d) => `<option value="${esc(d.name)}">${esc(d.desc)}</option>`).join("");
    if (!sel._wired) { sel.addEventListener("change", () => onPick(sel.value)); sel._wired = true; }
  }
  const def = devices.find((d) => d.is_default);
  if (def) {
    if (iconEl) iconEl.textContent = DEV_EMOJI[def.icon] || "🔊";
    if (document.activeElement !== sel && sel.value !== def.name) sel.value = def.name;
  }
}

export default {
  mount(container, api) {
    container.innerHTML = `
      <div class="mx-root">
        <div class="mx-section">
          <div class="mx-sec-head">Output</div>
          <div class="mx-device"><span class="mx-dev-icon">🔊</span><select data-dev="sink"></select></div>
          <div class="mx-master"></div>
        </div>
        <div class="mx-section mx-input">
          <div class="mx-sec-head">Input</div>
          <div class="mx-device"><span class="mx-dev-icon">🎙️</span><select data-dev="source"></select></div>
          <div class="mx-mic"></div>
        </div>
        <div class="mx-section mx-streams">
          <div class="mx-sec-head">Applications <span class="mx-count"></span></div>
          <div class="mx-stream-list"></div>
        </div>
      </div>`;

    const sinkSel = container.querySelector('select[data-dev="sink"]');
    const sourceSel = container.querySelector('select[data-dev="source"]');
    const sinkIcon = sinkSel.closest(".mx-device").querySelector(".mx-dev-icon");
    const sourceIcon = sourceSel.closest(".mx-device").querySelector(".mx-dev-icon");
    const list = container.querySelector(".mx-stream-list");
    const count = container.querySelector(".mx-count");

    const masterRow = makeDeviceRow(api); container.querySelector(".mx-master").appendChild(masterRow);
    const micRow = makeDeviceRow(api); container.querySelector(".mx-mic").appendChild(micRow);
    const streamRows = new Map();

    const off = api.onState((state) => {
      const sinks = state.sinks || [], sources = state.sources || [], streams = state.streams || [];

      syncSelect(sinkSel, sinkIcon, sinks, (name) => api.intent("set_default_sink", { name }));
      syncSelect(sourceSel, sourceIcon, sources, (name) => api.intent("set_default_source", { name }));

      // Fall back to the first device if the system default is something we
      // don't list (e.g. a monitor source), so the section still shows.
      const defSink = sinks.find((s) => s.is_default) || sinks[0];
      container.querySelector(".mx-master").style.display = defSink ? "" : "none";
      if (defSink) masterRow.update("sink", defSink.id, defSink.volume, defSink.muted);

      const defSource = sources.find((s) => s.is_default) || sources[0];
      container.querySelector(".mx-input").style.display = defSource ? "" : "none";
      if (defSource) micRow.update("source", defSource.id, defSource.volume, defSource.muted);

      // Diff application streams by id.
      const seen = new Set();
      for (const s of streams) {
        seen.add(String(s.id));
        let row = streamRows.get(String(s.id));
        if (!row) { row = makeAppRow(api); streamRows.set(String(s.id), row); list.appendChild(row); }
        row.update(s);
      }
      for (const [id, row] of streamRows) if (!seen.has(id)) { row.remove(); streamRows.delete(id); }
      count.textContent = streams.length ? `· ${streams.length}` : "";
      list.classList.toggle("mx-empty-state", streams.length === 0);
    });

    // Live per-app activity meters arrive on the free-form channel.
    const offLevels = api.onMessage((m) => {
      if (!m || !m.levels) return;
      for (const [id, row] of streamRows) row.setLevel(m.levels[id] || 0);
    });

    return () => { off(); offLevels(); };
  },
};
