"""
Window Inventory widget (docs/default-widgets.md).

Shows every window on the monitors *other* than the dashboard monitor, grouped
by monitor (each with a user-defined label, e.g. "Top"/"Bottom"). The user can
drag a window from one monitor's group to another to move it, or right-click a
window to move / resize (tile) / fullscreen / focus / close it.

state_intents pattern. Window operations go through the `windows` host service.
Intents:
  focus {id} / close {id} / fullscreen {id}
  move {id, monitor}            — send to another monitor
  place {id, region}            — tile on the window's current monitor:
                                  maximize | left | right | top | bottom | center
"""

from __future__ import annotations

import asyncio

from widget_dashboard.widget_base import WidgetBase


class Widget(WidgetBase):
    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def get_initial_state(self) -> dict:
        return await self._snapshot()

    async def on_settings_change(self, new_settings: dict) -> None:
        # Re-push so new monitor labels show immediately.
        self.ctx.set_state(await self._snapshot())

    async def on_intent(self, intent_type: str, payload: dict) -> None:
        win = self.ctx.host.windows
        wid = payload.get("id")
        try:
            if intent_type == "focus":
                await win.focus(wid)
            elif intent_type == "close":
                await win.close(wid)
            elif intent_type == "fullscreen":
                await win.fullscreen(wid)
            elif intent_type in ("above", "sticky"):
                await win.set_window_state(wid, intent_type)
            elif intent_type == "move":
                await win.move(wid, int(payload["monitor"]))
            elif intent_type == "place":
                await self._place(wid, payload.get("region", "maximize"))
        except Exception as e:  # noqa: BLE001 — surface, don't crash the widget
            self.ctx.log.warning("window op %s failed: %s", intent_type, e)
        await asyncio.sleep(0.2)
        self.ctx.set_state(await self._snapshot())

    # --- placement ---

    async def _place(self, wid: str, region: str) -> None:
        from widget_dashboard import sysutil
        win = self.ctx.host.windows
        if region == "fullscreen":
            await win.fullscreen(wid)
            return
        if region == "maximize":
            await win.maximize(wid)
            return
        windows = await win.list()
        target = next((w for w in windows if w["id"] == wid), None)
        if target is None:
            return
        mons = await sysutil.monitors()
        mon = next((m for m in mons if m.index == target["monitor"]), None)
        if mon is None:
            return
        rect = _region_rect(mon, region)
        if rect:
            await win.set_geometry(wid, *rect)

    # --- snapshot ---

    async def _loop(self) -> None:
        last = None
        while True:
            try:
                state = await self._snapshot()
            except Exception as e:  # noqa: BLE001
                state = {"error": str(e), "windows": [], "monitors": []}
            if state != last:
                self.ctx.set_state(state)
                last = state
            await asyncio.sleep(1.5)

    async def _snapshot(self) -> dict:
        from widget_dashboard import sysutil
        win = self.ctx.host.windows
        dash = win.dashboard_monitor()
        all_windows = await win.list()
        labels = self.ctx.settings.get("labels", {}) or {}
        hidden = {str(i) for i in (self.ctx.settings.get("hidden", []) or [])}
        windows = [
            w for w in all_windows
            if _is_real_window(w) and str(w.get("monitor")) not in hidden
        ]
        mons = [m for m in await sysutil.monitors() if str(m.index) not in hidden]
        # Show ALL monitors and ALL windows, grouped by where each window
        # physically sits. We don't special-case the dashboard monitor: the
        # configured dashboard index isn't reliably detected, so excluding it
        # hid the wrong screen. Treating every monitor the same is correct and
        # robust. (is_dashboard is included only as an optional hint.)
        return {
            "windows": windows,
            "monitors": [
                {
                    "index": m.index, "name": m.name,
                    "label": labels.get(str(m.index)) or m.name or f"Monitor {m.index}",
                    "is_dashboard": m.index == dash,
                    "geometry": {"x": m.x, "y": m.y, "w": m.w, "h": m.h},
                }
                for m in mons
            ],
            "dashboard_monitor": dash,
            "error": None,
        }


def _is_real_window(w: dict) -> bool:
    """Filter out things that aren't user windows you'd manage: the desktop
    background ("Desktop Icons") windows and anything whose center isn't on any
    monitor (off-screen / bogus geometry)."""
    if w.get("monitor") is None:
        return False
    title = (w.get("title") or "").lower()
    if title.startswith("desktop icons") or title in ("desktop", "@!"):
        return False
    return True


def _region_rect(mon, region: str):
    """Compute an (x, y, w, h) tile within a monitor for a named region."""
    x, y, w, h = mon.x, mon.y, mon.w, mon.h
    half_w, half_h = w // 2, h // 2
    return {
        "left":   (x, y, half_w, h),
        "right":  (x + half_w, y, half_w, h),
        "top":    (x, y, w, half_h),
        "bottom": (x, y + half_h, w, half_h),
        "center": (x + w // 6, y + h // 6, w * 2 // 3, h * 2 // 3),
        "full":   (x, y, w, h),
    }.get(region)
