"""
Widget instances (docs/architecture.md "Widget hosting model").

An instance is one placement of a widget on the grid. The manager creates
instances when the user adds a widget (or when a tab starts running), tears
them down on removal/stop, and routes each instance's websocket to its
backend.

Both communication patterns (docs/widgets.md 4.5) are first-class:
- free_form    : arbitrary JSON via ctx.send / on_message
- state_intents: ctx.set_state pushes state; frontend intents arrive as
                 {"__intent__": {type, payload}} and go to on_intent()

In this slice instances run in the dashboard's own process (no sandbox — see
docs/open-questions.md). The `ctx` indirection means that can change later
without touching widget code.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .context import WidgetContext
from .registry import RegisteredWidget

if TYPE_CHECKING:
    from fastapi import WebSocket
    from .dashboard import Dashboard

log = logging.getLogger("instances")


class WidgetInstance:
    def __init__(
        self,
        instance_id: str,
        registered: RegisteredWidget,
        settings: dict,
        state_root: Path,
        dashboard: "Dashboard",
    ) -> None:
        self.instance_id = instance_id
        self.widget_id = registered.manifest.id
        self.registered = registered
        self.settings = settings
        self.dashboard = dashboard

        self.state_dir = state_root / instance_id
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self._ws: "WebSocket | None" = None
        self._backend = None  # the user's Widget instance
        self._ctx: WidgetContext | None = None
        self._started = False
        # Buffer the most recent free-form message / state so a frontend that
        # connects after the backend started still gets current data.
        self._last_msg: dict | None = None

    # --- lifecycle ---

    async def start(self) -> None:
        if self._started:
            return
        self._ctx = WidgetContext(self)
        self._backend = self.registered.widget_class(self._ctx)
        await self._backend.start()
        self._started = True
        log.info("instance %s (%s) started", self.instance_id, self.widget_id)

    async def stop(self) -> None:
        if not self._started:
            return
        try:
            await self._backend.stop()
        except Exception:
            log.exception("error stopping instance %s", self.instance_id)
        if self._ctx is not None:
            await self._ctx.events.cancel_all()
        self._started = False
        log.info("instance %s stopped", self.instance_id)

    async def update_settings(self, new_settings: dict) -> None:
        self.settings = new_settings
        if self._ctx is not None:
            self._ctx.settings = new_settings
        if self._backend is not None:
            await self._backend.on_settings_change(new_settings)

    # --- websocket plumbing ---

    def attach_ws(self, ws: "WebSocket") -> None:
        self._ws = ws
        # state_intents widgets get a fresh initial state on connect; free_form
        # widgets get the last buffered message replayed.
        if self.registered.manifest.communication == "state_intents" \
                and self._backend is not None \
                and hasattr(self._backend, "get_initial_state"):
            asyncio.create_task(self._send_initial_state())
        elif self._last_msg is not None:
            asyncio.create_task(self._safe_ws_send(self._last_msg))

    async def _send_initial_state(self) -> None:
        try:
            state = await self._backend.get_initial_state()  # type: ignore[union-attr]
            await self._safe_ws_send({"__state__": state})
        except Exception:
            log.exception("get_initial_state failed for %s", self.instance_id)

    def detach_ws(self, ws: "WebSocket") -> None:
        if self._ws is ws:
            self._ws = None

    def send_to_frontend(self, msg: dict) -> None:
        """Called by ctx.send()/ctx.set_state(). Buffers latest and pushes."""
        self._last_msg = msg
        if self._ws is not None:
            asyncio.create_task(self._safe_ws_send(msg))

    async def _safe_ws_send(self, msg: dict) -> None:
        try:
            await self._ws.send_json(msg)  # type: ignore[union-attr]
        except Exception:
            # Frontend went away; drop the socket, keep the backend running.
            self._ws = None

    async def handle_frontend_message(self, msg: dict) -> None:
        if self._backend is None:
            return
        # state_intents widgets receive {"__intent__": {type, payload}}.
        intent = msg.get("__intent__") if isinstance(msg, dict) else None
        if intent is not None and hasattr(self._backend, "on_intent"):
            await self._backend.on_intent(  # type: ignore[union-attr]
                intent.get("type"), intent.get("payload", {})
            )
        else:
            await self._backend.on_message(msg)

    def send_control(self, msg: dict) -> None:
        """Send a shell-level control message (e.g. a layout action) to the
        frontend host, namespaced so widget code never sees it."""
        self.send_to_frontend({"__control__": msg})


class InstanceManager:
    def __init__(self, state_root: Path, dashboard: "Dashboard") -> None:
        self.state_root = state_root
        self.dashboard = dashboard
        self.instances: dict[str, WidgetInstance] = {}

    async def create(
        self, instance_id: str, registered: RegisteredWidget, settings: dict
    ) -> WidgetInstance:
        inst = WidgetInstance(
            instance_id=instance_id,
            registered=registered,
            settings=settings,
            state_root=self.state_root,
            dashboard=self.dashboard,
        )
        self.instances[instance_id] = inst
        await inst.start()
        return inst

    async def destroy(self, instance_id: str) -> None:
        inst = self.instances.pop(instance_id, None)
        if inst is not None:
            await inst.stop()

    async def destroy_all(self) -> None:
        for instance_id in list(self.instances):
            await self.destroy(instance_id)

    def get(self, instance_id: str) -> WidgetInstance | None:
        return self.instances.get(instance_id)
