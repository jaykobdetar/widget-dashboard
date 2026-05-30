// Terminal frontend — embeds the per-instance ttyd URL in an iframe once the
// backend reports it is ready.

export default {
  mount(container, api) {
    container.innerHTML = `<div class="term-status">starting shell…</div>`;

    const off = api.onMessage((msg) => {
      if (msg.error) {
        container.innerHTML = `<div class="term-error">${msg.error}</div>`;
      } else if (msg.ready && msg.url) {
        container.innerHTML =
          `<iframe class="term-frame" src="${msg.url}" title="terminal"></iframe>`;
      }
    });

    return () => off();
  },
};
