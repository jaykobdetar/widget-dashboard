"""
Widget plugin contract (docs/widgets.md).

A widget is a folder with a manifest (widget.json), a backend module
(backend.py defining a `Widget` class), a frontend module (frontend.js),
and optional settings.js / style.css.

This module defines:
- `WidgetManifest`: parsed, validated widget.json
- `WidgetBase`: the base class widget backends subclass

The dashboard imports each widget's backend.py, finds the `Widget` class,
and drives it through the lifecycle: __init__ -> start -> (on_message /
on_settings_change)* -> stop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .context import WidgetContext


@dataclass
class Size:
    w: int
    h: int

    @classmethod
    def from_json(cls, d: dict | None, default: "Size | None" = None) -> "Size | None":
        if d is None:
            return default
        return cls(w=int(d["w"]), h=int(d["h"]))


@dataclass
class WidgetManifest:
    """Parsed widget.json. See docs/widgets.md for the full field list."""

    id: str
    name: str
    description: str
    version: str
    category: str
    instance_mode: str  # "singleton" | "multi"
    default_size: Size
    min_size: Size
    # The directory the widget lives in, filled in by the loader.
    path: Path = field(default=None)  # type: ignore[assignment]
    max_size: Size | None = None
    visibility: str = "pinned"  # pinned | hidden_until_triggered | overlay
    communication: str = "free_form"  # free_form | state_intents
    event_sources: list[str] = field(default_factory=list)
    host_services: list[str] = field(default_factory=list)
    requires: dict = field(default_factory=dict)
    permissions: dict = field(default_factory=dict)
    icon: str | None = None

    @classmethod
    def load(cls, widget_dir: Path) -> "WidgetManifest":
        raw = json.loads((widget_dir / "widget.json").read_text())

        mode = raw.get("instance_mode", "multi")
        if mode not in ("singleton", "multi"):
            raise ValueError(
                f"{widget_dir.name}: instance_mode must be 'singleton' or "
                f"'multi', got {mode!r}"
            )

        vis = raw.get("visibility", "pinned")
        if vis not in ("pinned", "hidden_until_triggered", "overlay"):
            raise ValueError(f"{widget_dir.name}: invalid visibility {vis!r}")

        return cls(
            id=raw["id"],
            name=raw["name"],
            description=raw.get("description", ""),
            version=raw.get("version", "0.0.0"),
            category=raw.get("category", "custom"),
            instance_mode=mode,
            default_size=Size.from_json(raw["default_size"]),
            min_size=Size.from_json(raw.get("min_size"), Size(1, 1)),
            max_size=Size.from_json(raw.get("max_size")),
            visibility=vis,
            communication=raw.get("communication", "free_form"),
            event_sources=raw.get("event_sources", []),
            host_services=raw.get("host_services", []),
            requires=raw.get("requires", {}),
            permissions=raw.get("permissions", {}),
            icon=raw.get("icon"),
            path=widget_dir,
        )

    def to_picker_json(self, available: bool, reason: str | None = None) -> dict:
        """The shape the frontend picker consumes."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "instance_mode": self.instance_mode,
            "default_size": {"w": self.default_size.w, "h": self.default_size.h},
            "min_size": {"w": self.min_size.w, "h": self.min_size.h},
            "max_size": (
                {"w": self.max_size.w, "h": self.max_size.h}
                if self.max_size
                else None
            ),
            "visibility": self.visibility,
            "version": self.version,
            "icon": self.icon,
            "host_services": self.host_services,
            "event_sources": self.event_sources,
            "permissions": self.permissions,
            "available": available,
            "unavailable_reason": reason,
        }


class WidgetBase:
    """Base class for widget backends.

    Widgets subclass this and override the lifecycle methods they need.
    All overrides are optional except that a widget that shows live data
    will want `start`. See docs/widgets.md.
    """

    def __init__(self, ctx: WidgetContext) -> None:
        self.ctx = ctx

    async def start(self) -> None:
        """Begin work. Long-running polls/subscriptions start here."""

    async def stop(self) -> None:
        """Tear down. Cancel tasks, kill subprocesses, close files."""

    async def on_message(self, msg: dict) -> None:
        """Handle a message from the frontend (free-form pattern)."""

    async def on_settings_change(self, new_settings: dict) -> None:
        """React to the user editing this instance's settings."""
