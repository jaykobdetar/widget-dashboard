"""
Dashboard — the orchestrator (docs/architecture.md).

Owns the registry, the profile store, the instance manager, the global config,
the system broadcast channel, the lockdown client, and the runtime tab
run-states. Translates high-level actions ("select tab X", "add widget Y",
"enable tab Z", "a widget fired") into instance lifecycle calls, profile
saves, and shell-facing system events.

Tab run-states (docs/layout.md):
- disabled : not running. Default for every tab at launch.
- enabled  : running in the background; triggers fire, can notify.
- selected : running and rendered; exactly one at a time.

Rules implemented here:
- Selecting a tab forces it on. Selecting a *disabled* tab is a temporary
  view-time override: when you switch away it reverts to disabled.
- An enabled tab stays enabled when you switch away.
- On launch every tab starts disabled. Run-state is never persisted.

Triggers (docs/triggers.md): the widget owns the trigger (ctx.fire), the
dashboard owns the response. handle_fire() looks up the per-instance response
config (instance-authoritative, global default as seed), templates it from the
payload, and broadcasts it to the shell over the system channel.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .config import Config
from .instances import InstanceManager
from .lockdown import LockdownClient
from .profiles import (
    InstanceRecord,
    PresetStore,
    Profile,
    ProfileStore,
    new_instance_id,
)
from .registry import WidgetRegistry

if TYPE_CHECKING:
    from fastapi import WebSocket

log = logging.getLogger("dashboard")


def _is_hex_color(s: str) -> bool:
    s = s.lstrip("#")
    return len(s) in (3, 6, 8) and all(c in "0123456789abcdefABCDEF" for c in s)


class _SafeDict(dict):
    """str.format_map helper: leaves unknown {placeholders} intact instead of
    raising, so a user's toast template never crashes the trigger pipeline."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class SystemChannel:
    """Fan-out of shell-level events to every connected system websocket
    (toasts, badges, bounce notices, layout actions, rescan signals)."""

    def __init__(self) -> None:
        self._sockets: set["WebSocket"] = set()

    def attach(self, ws: "WebSocket") -> None:
        self._sockets.add(ws)

    def detach(self, ws: "WebSocket") -> None:
        self._sockets.discard(ws)

    async def broadcast(self, msg: dict) -> None:
        dead = []
        for ws in list(self._sockets):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._sockets.discard(ws)


class Dashboard:
    def __init__(
        self,
        registry: WidgetRegistry,
        profile_store: ProfileStore,
        state_root: Path,
        config: Config,
        preset_store: PresetStore,
    ) -> None:
        self.registry = registry
        self.profiles = profile_store
        self.presets = preset_store
        self.config = config
        self.instances = InstanceManager(state_root, self)
        self.system = SystemChannel()
        self.lockdown = LockdownClient(config, self._on_lockdown_event)

        # runtime tab run-state, never persisted
        self.enabled_tabs: set[str] = set()      # explicitly enabled
        self.selected_tab: str | None = None      # at most one
        self._selected_was_disabled = False

        # Tabs with in-memory edits not yet written to disk. Persistence is
        # now explicit (Save), not automatic, per the user's document-style
        # save/load model — this deliberately overrides SPEC §6 auto-save.
        self.dirty: set[str] = set()

        # cache of loaded profiles by name
        self._loaded: dict[str, Profile] = {}

    # --- startup ---

    async def startup(self) -> None:
        self.registry.scan()
        first = self.profiles.ensure_seed()
        await self.select_tab(first)
        await self.lockdown.connect()

    async def shutdown(self) -> None:
        await self.instances.destroy_all()
        await self.lockdown.close()

    # --- profile/tab helpers ---

    def _profile(self, name: str) -> Profile:
        if name not in self._loaded:
            self._loaded[name] = self.profiles.load(name)
        return self._loaded[name]

    def _forget(self, name: str) -> None:
        self._loaded.pop(name, None)

    def tab_states(self) -> list[dict]:
        """Tab bar model for the frontend, in stored order."""
        out = []
        for name in self.profiles.list_names():
            if name == self.selected_tab:
                state = "selected"
            elif name in self.enabled_tabs:
                state = "enabled"
            else:
                state = "disabled"
            out.append({
                "name": name,
                "state": state,
                "dirty": name in self.dirty,
            })
        return out

    def _find_record(self, instance_id: str) -> tuple[Profile, InstanceRecord] | None:
        for profile in self._loaded.values():
            for rec in profile.instances:
                if rec.id == instance_id:
                    return profile, rec
        return None

    # --- running a tab's instances ---

    async def _start_tab_instances(self, name: str) -> None:
        profile = self._profile(name)
        for rec in profile.instances:
            if self.instances.get(rec.id) is not None:
                continue  # already running
            registered = self.registry.get(rec.widget_id)
            if registered is None:
                log.warning("tab %s references unknown widget %s",
                            name, rec.widget_id)
                continue
            await self.instances.create(rec.id, registered, rec.settings)

    async def _stop_tab_instances(self, name: str) -> None:
        profile = self._profile(name)
        for rec in profile.instances:
            await self.instances.destroy(rec.id)

    def _tab_is_running(self, name: str) -> bool:
        return name == self.selected_tab or name in self.enabled_tabs

    # --- actions ---

    async def select_tab(self, name: str) -> None:
        """Make `name` the visible tab. Forces it on. Applies the peek-revert
        rule to the tab we're leaving."""
        if name == self.selected_tab:
            return

        previous = self.selected_tab
        prev_was_peek = self._selected_was_disabled

        if previous is not None and prev_was_peek and previous not in self.enabled_tabs:
            await self._stop_tab_instances(previous)

        self._selected_was_disabled = name not in self.enabled_tabs
        self.selected_tab = name
        await self._start_tab_instances(name)
        log.info("selected tab %s (peek=%s)", name, self._selected_was_disabled)

    async def enable_tab(self, name: str) -> None:
        self.enabled_tabs.add(name)
        if name == self.selected_tab:
            self._selected_was_disabled = False
        await self._start_tab_instances(name)
        log.info("enabled tab %s", name)

    async def disable_tab(self, name: str) -> None:
        if name == self.selected_tab:
            log.info("refusing to disable selected tab %s", name)
            return
        self.enabled_tabs.discard(name)
        await self._stop_tab_instances(name)
        log.info("disabled tab %s", name)

    # --- tab management (docs/layout.md "UI affordances on tabs") ---

    def rename_tab(self, old: str, new: str) -> None:
        profile = self._profile(old)
        self.profiles.delete(old)
        self._forget(old)
        profile.name = new
        self.profiles.save(profile)
        self._loaded[new] = profile
        if self.selected_tab == old:
            self.selected_tab = new
        if old in self.enabled_tabs:
            self.enabled_tabs.discard(old)
            self.enabled_tabs.add(new)
        # Renaming writes the file, so the renamed tab is clean.
        self.dirty.discard(old)
        self.dirty.discard(new)

    def duplicate_tab(self, name: str) -> str:
        src = self._profile(name)
        base = f"{name} copy"
        new_name = base
        existing = set(self.profiles.list_names())
        n = 2
        while new_name in existing:
            new_name = f"{base} {n}"
            n += 1
        clone = Profile(name=new_name, columns=src.columns, row_height=src.row_height)
        for rec in src.instances:
            clone.instances.append(InstanceRecord(
                id=new_instance_id(), widget_id=rec.widget_id,
                x=rec.x, y=rec.y, w=rec.w, h=rec.h,
                settings=dict(rec.settings), response=dict(rec.response),
            ))
        self.profiles.save(clone)
        return new_name

    async def delete_tab(self, name: str) -> None:
        if self._tab_is_running(name):
            await self._stop_tab_instances(name)
        self.enabled_tabs.discard(name)
        self.dirty.discard(name)
        self.profiles.delete(name)
        self._forget(name)
        if self.selected_tab == name:
            self.selected_tab = None
            remaining = self.profiles.list_names()
            if remaining:
                await self.select_tab(remaining[0])

    def reorder_tabs(self, order: list[str]) -> None:
        self.profiles.set_order(order)

    # --- editing the selected tab ---

    async def add_widget(self, widget_id: str) -> InstanceRecord | None:
        if self.selected_tab is None:
            return None
        registered = self.registry.get(widget_id)
        if registered is None or not registered.available:
            return None

        profile = self._profile(self.selected_tab)

        if registered.manifest.instance_mode == "singleton":
            if any(r.widget_id == widget_id for r in profile.instances):
                log.info("singleton %s already on tab", widget_id)
                return None

        ds = registered.manifest.default_size
        rec = InstanceRecord(
            id=new_instance_id(), widget_id=widget_id,
            x=0, y=0, w=ds.w, h=ds.h,
            # Seed the response config from the global default (docs/triggers.md).
            response=dict(self.config.default_response),
        )
        profile.instances.append(rec)
        self.dirty.add(self.selected_tab)
        await self.instances.create(rec.id, registered, rec.settings)
        return rec

    async def remove_widget(self, instance_id: str) -> None:
        if self.selected_tab is None:
            return
        profile = self._profile(self.selected_tab)
        profile.instances = [r for r in profile.instances if r.id != instance_id]
        self.dirty.add(self.selected_tab)
        await self.instances.destroy(instance_id)

    def update_layout(self, positions: list[dict]) -> None:
        if self.selected_tab is None:
            return
        profile = self._profile(self.selected_tab)
        by_id = {r.id: r for r in profile.instances}
        changed = False
        for p in positions:
            rec = by_id.get(p["id"])
            if rec is not None:
                rec.x, rec.y, rec.w, rec.h = p["x"], p["y"], p["w"], p["h"]
                changed = True
        if changed:
            self.dirty.add(self.selected_tab)

    async def update_instance_settings(self, instance_id: str, settings: dict) -> None:
        found = self._find_record(instance_id)
        if found is None:
            return
        profile, rec = found
        rec.settings = settings
        self.dirty.add(profile.name)
        # Settings apply to the LIVE instance immediately so the widget updates
        # now; only persistence to disk waits for an explicit Save.
        inst = self.instances.get(instance_id)
        if inst is not None:
            await inst.update_settings(settings)

    def update_instance_response(self, instance_id: str, response: dict) -> None:
        found = self._find_record(instance_id)
        if found is None:
            return
        profile, rec = found
        rec.response = response
        self.dirty.add(profile.name)

    def update_instance_color(self, instance_id: str, color: str) -> None:
        found = self._find_record(instance_id)
        if found is None:
            return
        profile, rec = found
        # Accept only an empty string or a #hex color.
        c = (color or "").strip()
        rec.color = c if (c == "" or _is_hex_color(c)) else ""
        self.dirty.add(profile.name)

    def get_instance_response(self, instance_id: str) -> dict:
        found = self._find_record(instance_id)
        if found is None:
            return dict(self.config.default_response)
        return found[1].response or dict(self.config.default_response)

    # --- explicit save / load / revert + preset library ---

    def save_tab(self, name: str) -> None:
        """Persist a tab's in-memory layout to disk (the Save action)."""
        self.profiles.save(self._profile(name))
        self.dirty.discard(name)

    async def revert_tab(self, name: str) -> None:
        """Discard a tab's unsaved edits, reloading it from disk (Revert)."""
        running = self._tab_is_running(name)
        if running:
            await self._stop_tab_instances(name)
        self._forget(name)
        self.dirty.discard(name)
        if running:
            await self._start_tab_instances(name)

    def list_presets(self) -> list[str]:
        return self.presets.list_names()

    def save_as_preset(self, preset_name: str, from_tab: str) -> None:
        """Snapshot a tab's current (in-memory) layout into the preset library."""
        self.presets.save(preset_name, self._profile(from_tab))

    def delete_preset(self, preset_name: str) -> None:
        self.presets.delete(preset_name)

    async def load_preset(self, tab: str, preset_name: str) -> None:
        """Replace a tab's contents with a saved preset, giving every instance a
        fresh id. Marks the tab dirty (the user still chooses when to Save)."""
        preset = self.presets.load(preset_name)
        profile = self._profile(tab)
        running = self._tab_is_running(tab)
        if running:
            await self._stop_tab_instances(tab)
        profile.instances = [
            InstanceRecord(
                id=new_instance_id(), widget_id=rec.widget_id,
                x=rec.x, y=rec.y, w=rec.w, h=rec.h,
                settings=dict(rec.settings), response=dict(rec.response),
            )
            for rec in preset.instances
        ]
        self.dirty.add(tab)
        if running:
            await self._start_tab_instances(tab)

    async def rescan(self) -> None:
        self.registry.scan()
        await self.system.broadcast({"type": "rescan"})

    # --- layout host service → shell ---

    def layout_action(self, instance_id: str, action: str) -> None:
        """A widget asked to change its own visibility (ctx.host.layout.*)."""
        import asyncio
        asyncio.create_task(self.system.broadcast({
            "type": "layout", "instance_id": instance_id, "action": action,
        }))

    # --- trigger handling (docs/triggers.md) ---

    async def handle_fire(self, instance_id: str, payload: dict) -> None:
        """A widget fired its trigger. Look up the per-instance response config,
        template it from the payload, and broadcast to the shell, which renders
        the toast/badge/flash/reveal/switch-to-tab the user configured."""
        found = self._find_record(instance_id)
        response = found[1].response if found else dict(self.config.default_response)
        if not response:
            response = dict(self.config.default_response)

        inst = self.instances.get(instance_id)
        widget_name = inst.widget_id if inst else "widget"

        ctx = _SafeDict(payload)
        ctx["__widget__"] = widget_name
        rendered = dict(response)
        toast = response.get("toast")
        if isinstance(toast, dict) and toast.get("text"):
            rendered = dict(response)
            rendered["toast"] = dict(toast)
            rendered["toast"]["text"] = str(toast["text"]).format_map(ctx)

        log.info("trigger fired by %s: %s", instance_id, payload)
        await self.system.broadcast({
            "type": "trigger",
            "instance_id": instance_id,
            "widget_id": widget_name,
            "response": rendered,
            "payload": payload,
        })

    async def _on_lockdown_event(self, event: dict) -> None:
        """Forward a lockdown daemon event (e.g. a bounce) to the shell."""
        await self.system.broadcast({"type": "lockdown", **event})
