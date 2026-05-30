// Clipboard History frontend (state_intents). Click an entry to copy it back;
// pin to keep it at the top; ✕ to delete. Header has a clear-all action.

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function render(container, state, api) {
  const entries = state.entries || [];
  container.innerHTML = `
    <div class="ch-root">
      <div class="ch-head">
        <span class="ch-title">CLIPBOARD</span>
        <span class="ch-count">${entries.length || ""}</span>
      </div>
      <div class="ch-list">
        ${entries.length ? entries.map((e, i) => `
          <div class="ch-row ${e.pinned ? "pinned" : ""}" data-i="${i}" title="${esc(e.text)}">
            <button class="ch-pin" data-act="pin" title="${e.pinned ? "Unpin" : "Pin"}">${e.pinned ? "★" : "☆"}</button>
            <div class="ch-text">${esc(e.text)}</div>
            <button class="ch-del" data-act="delete" title="Delete">✕</button>
          </div>`).join("")
        : `<div class="ch-empty">Copy something to start a history.</div>`}
      </div>
      ${entries.length ? `<button class="ch-clear" type="button" title="Remove all unpinned entries">Clear history</button>` : ""}
    </div>`;

  const clear = container.querySelector(".ch-clear");
  if (clear) clear.onclick = () => api.intent("clear");

  container.querySelectorAll(".ch-row").forEach((row) => {
    const i = Number(row.getAttribute("data-i"));
    const text = entries[i] && entries[i].text;   // key by text, not position
    row.onclick = (e) => {
      const btn = e.target.closest("[data-act]");
      if (btn) api.intent(btn.dataset.act, { text });
      else { api.intent("copy", { text }); flash(row); }
    };
  });
}

function flash(row) {
  row.classList.remove("copied"); void row.offsetWidth; row.classList.add("copied");
}

export default {
  mount(container, api) {
    container.innerHTML = `<div class="ch-empty">…</div>`;
    const off = api.onState((state) => render(container, state, api));
    return () => off();
  },
};
