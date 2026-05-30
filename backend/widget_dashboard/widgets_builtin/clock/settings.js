// Clock settings — 12/24-hour and seconds toggles. Reference settings.js.

export default {
  mount(container, api) {
    const s = api.settings || {};
    const hour24 = s.hour24 !== false;   // default true
    const seconds = s.seconds !== false; // default true
    container.innerHTML = `
      <form class="wd-form">
        <label class="wd-check">
          <input name="hour24" type="checkbox" ${hour24 ? "checked" : ""} />
          24-hour clock
        </label>
        <label class="wd-check">
          <input name="seconds" type="checkbox" ${seconds ? "checked" : ""} />
          Show seconds
        </label>
        <div class="wd-form-actions">
          <button type="button" data-cancel class="wd-btn">Cancel</button>
          <button type="submit" class="wd-btn wd-btn-primary">Save</button>
        </div>
      </form>`;
    const form = container.querySelector("form");
    form.onsubmit = (e) => {
      e.preventDefault();
      api.save({ hour24: form.hour24.checked, seconds: form.seconds.checked });
    };
    container.querySelector("[data-cancel]").onclick = () => api.cancel();
  },
};
