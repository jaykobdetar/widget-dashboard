"""
WidgetContext — the `ctx` object handed to every widget backend instance.

Per docs/widgets.md, a widget never touches the HTTP server, the websocket,
or other instances directly. Everything goes through this object. Keeping all
privileged actions behind `ctx` is also what makes a future per-widget
subprocess sandbox a drop-in change (docs/open-questions.md): today these are
in-process method calls; later they can become RPC across a process boundary
with no change to widget code.

`host` and `events` are scoped to exactly what the widget declared in its
manifest (`host_services` / `event_sources`); reaching for anything else is a
clear error, keeping the manifest honest.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .events import EventBus
from .host_services import HostServices

if TYPE_CHECKING:
    from .instances import WidgetInstance


class WidgetContext:
    """Everything a widget backend instance is allowed to do.

    Attributes
    ----------
    instance_id:
        Unique id of this placement on the grid.
    settings:
        This instance's current settings dict (from the profile).
    state_dir:
        Per-instance scratch directory for persistent state (graph buffers,
        history). Large/disposable data lives here, not in the profile.
    log:
        Logger scoped to this instance.
    host:
        Shared dashboard services (docs/host-services.md), scoped to the
        widget's declared `host_services`.
    events:
        Event-source subscriptions for triggers (docs/triggers.md), scoped to
        the widget's declared `event_sources`.
    """

    def __init__(self, instance: "WidgetInstance") -> None:
        self._instance = instance
        self.instance_id: str = instance.instance_id
        self.settings: dict = instance.settings
        self.state_dir: Path = instance.state_dir
        self.log: logging.Logger = logging.getLogger(
            f"widget.{instance.widget_id}.{instance.instance_id}"
        )
        manifest = instance.registered.manifest
        self.host = HostServices(instance, manifest.host_services)
        self.events = EventBus(manifest.event_sources)

    def send(self, msg: dict) -> None:
        """Push a message to this instance's frontend over its websocket
        (free-form pattern, docs/widgets.md)."""
        self._instance.send_to_frontend(msg)

    def set_state(self, state: dict) -> None:
        """Publish new backend state to the frontend (state_intents pattern,
        docs/widgets.md 4.5). Wrapped so the frontend's onState() handler can
        distinguish it from a free-form message."""
        self._instance.send_to_frontend({"__state__": state})

    async def fire(self, payload: dict | None = None) -> None:
        """Report that this widget's trigger fired (docs/triggers.md).

        The widget specifies no severity and no presentation — only that
        something happened, with an optional payload. The dashboard renders
        whatever response the user configured for this instance.
        """
        await self._instance.dashboard.handle_fire(
            self.instance_id, payload or {}
        )
