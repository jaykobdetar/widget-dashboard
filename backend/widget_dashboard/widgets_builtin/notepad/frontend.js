// Sticky Note frontend (free-form). A textarea that autosaves (debounced) as
// you type; the backend persists it. Use the card's 🎨 color (in the widget
// chrome) to tint it like a sticky note.

export default {
  mount(container, api) {
    container.innerHTML =
      `<textarea class="np-area" placeholder="Type a note…" spellcheck="true"></textarea>`;
    const area = container.querySelector(".np-area");

    let timer = null;
    const save = () => api.send({ save: area.value });
    area.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(save, 400);          // debounce while typing
    });

    const off = api.onMessage((m) => {
      // Don't clobber what the user is actively typing.
      if (m.text !== undefined && document.activeElement !== area) area.value = m.text;
    });

    return () => {
      clearTimeout(timer);
      save();                                 // flush any pending edit on unmount
      off();
    };
  },
};
