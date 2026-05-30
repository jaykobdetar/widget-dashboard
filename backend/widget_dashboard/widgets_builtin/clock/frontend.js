// Clock widget frontend (docs/widgets.md "Frontend").
//
// A frontend module default-exports an object with mount(container, api).
// - container: the DOM element this widget owns
// - api.send(msg): send a message to the backend
// - api.onMessage(handler): receive backend messages
// - api.settings: current settings snapshot
// Returns a cleanup function, called on unmount.
//
// This is the reference widget: vanilla DOM, no framework, no build step.

export default {
  mount(container, api) {
    container.innerHTML = `
      <div class="clock-widget">
        <div class="clock-time" data-role="time">--:--:--</div>
        <div class="clock-date" data-role="date">—</div>
      </div>
    `;
    const timeEl = container.querySelector('[data-role="time"]');
    const dateEl = container.querySelector('[data-role="date"]');

    const off = api.onMessage((msg) => {
      if (msg.time !== undefined) timeEl.textContent = msg.time;
      if (msg.date !== undefined) dateEl.textContent = msg.date;
    });

    // Cleanup on unmount.
    return () => { off(); };
  },
};
