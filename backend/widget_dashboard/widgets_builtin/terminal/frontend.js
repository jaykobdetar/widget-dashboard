// Terminal frontend — embeds the per-instance ttyd URL in an iframe once the
// backend reports it is ready.

export default {
  mount(container, api) {
    container.innerHTML = `<div class="term-status">starting shell…</div>`;

    const off = api.onMessage((msg) => {
      if (msg.error) {
        // textContent, not innerHTML: msg.error embeds the user's shell/cwd
        // setting and must not be parsed as markup.
        const div = document.createElement("div");
        div.className = "term-error";
        div.textContent = msg.error;
        container.replaceChildren(div);
      } else if (msg.ready && msg.url) {
        // Set src as a property rather than interpolating into HTML.
        const frame = document.createElement("iframe");
        frame.className = "term-frame";
        frame.title = "terminal";
        frame.src = msg.url;
        container.replaceChildren(frame);
      }
    });

    return () => off();
  },
};
