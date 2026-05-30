// Terminal settings — shell, working directory, font size.

export default {
  mount(container, api) {
    const s = api.settings || {};
    const attr = (v) => (v == null ? "" : String(v).replace(/"/g, "&quot;"));
    container.innerHTML = `
      <form class="wd-form">
        <label>Shell
          <input name="shell" type="text" value="${attr(s.shell)}" placeholder="$SHELL (e.g. /bin/bash)" />
        </label>
        <label>Working directory
          <input name="cwd" type="text" value="${attr(s.cwd)}" placeholder="~ (home)" />
        </label>
        <label>Font size
          <input name="font_size" type="number" min="8" max="32" value="${attr(s.font_size ?? 14)}" />
        </label>
        <div class="wd-form-actions">
          <button type="button" data-cancel class="wd-btn">Cancel</button>
          <button type="submit" class="wd-btn wd-btn-primary">Save</button>
        </div>
      </form>`;
    const form = container.querySelector("form");
    form.onsubmit = (e) => {
      e.preventDefault();
      api.save({
        shell: form.shell.value.trim(),
        cwd: form.cwd.value.trim(),
        font_size: Number(form.font_size.value) || 14,
      });
    };
    container.querySelector("[data-cancel]").onclick = () => api.cancel();
  },
};
