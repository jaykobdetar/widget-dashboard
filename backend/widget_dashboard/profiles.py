"""
Profiles, tabs, and tab run-states (docs/layout.md).

A profile is a named layout, stored as one JSON file. Tabs ARE profiles —
"tab" is the UI, "profile" is the stored object. Each profile is self-
contained: grid config + widget instances (with their settings and per-
trigger response config).

Tab run-state (disabled / enabled / selected) is NOT stored here. It is
runtime-only and resets to "all disabled" every launch (docs/layout.md).
That lives in the Dashboard, not in these files.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("profiles")


def new_instance_id() -> str:
    return "inst_" + uuid.uuid4().hex[:8]


@dataclass
class InstanceRecord:
    """One widget placement within a profile."""

    id: str
    widget_id: str
    x: int
    y: int
    w: int
    h: int
    settings: dict = field(default_factory=dict)
    response: dict = field(default_factory=dict)  # trigger response config
    color: str = ""                               # per-widget card tint (hex or "")

    @classmethod
    def from_json(cls, d: dict) -> "InstanceRecord":
        return cls(
            id=d["id"],
            widget_id=d["widget_id"],
            x=d["x"], y=d["y"], w=d["w"], h=d["h"],
            settings=d.get("settings", {}),
            response=d.get("response", {}),
            color=d.get("color", ""),
        )

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "widget_id": self.widget_id,
            "x": self.x, "y": self.y, "w": self.w, "h": self.h,
            "settings": self.settings,
            "response": self.response,
            "color": self.color,
        }


@dataclass
class Profile:
    name: str
    instances: list[InstanceRecord] = field(default_factory=list)
    columns: int = 12
    row_height: int = 60

    @classmethod
    def from_json(cls, d: dict) -> "Profile":
        grid = d.get("grid", {})
        return cls(
            name=d["name"],
            instances=[InstanceRecord.from_json(i) for i in d.get("instances", [])],
            columns=grid.get("columns", 12),
            row_height=grid.get("row_height", 60),
        )

    def to_json(self) -> dict:
        return {
            "version": 1,
            "name": self.name,
            "grid": {"columns": self.columns, "row_height": self.row_height},
            "instances": [i.to_json() for i in self.instances],
        }


class ProfileStore:
    def __init__(self, profiles_dir: Path) -> None:
        self.dir = profiles_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        # Tab order is a UI concern, persisted OUTSIDE the profiles directory so
        # it is never picked up by the `*.json` profile scan (an earlier version
        # kept it as `profiles/_order.json`, which showed up as a bogus "_order"
        # tab and crashed selection — see migration below).
        self._order_file = self.dir.parent / "tab-order.json"
        self._migrate_order_file()

    def _migrate_order_file(self) -> None:
        old = self.dir / "_order.json"
        if old.exists():
            try:
                if not self._order_file.exists():
                    self._order_file.write_text(old.read_text())
            except Exception:  # noqa: BLE001
                pass
            old.unlink(missing_ok=True)   # remove the stray file so it's not a "tab"

    def _stored_order(self) -> list[str]:
        if self._order_file.exists():
            try:
                return list(json.loads(self._order_file.read_text()))
            except Exception:
                return []
        return []

    def list_names(self) -> list[str]:
        """Profiles in the user's chosen tab order. Names not yet in the order
        file (freshly created) are appended alphabetically. Underscore-prefixed
        files are reserved/internal and never listed as tabs."""
        on_disk = {p.stem for p in self.dir.glob("*.json") if not p.stem.startswith("_")}
        order = [n for n in self._stored_order() if n in on_disk]
        rest = sorted(on_disk - set(order))
        return order + rest

    def set_order(self, names: list[str]) -> None:
        self._order_file.write_text(json.dumps(names))

    def load(self, name: str) -> Profile:
        return Profile.from_json(json.loads((self.dir / f"{name}.json").read_text()))

    def save(self, profile: Profile) -> None:
        path = self.dir / f"{profile.name}.json"
        path.write_text(json.dumps(profile.to_json(), indent=2))
        log.info("saved profile %s (%d instances)",
                 profile.name, len(profile.instances))

    def delete(self, name: str) -> None:
        (self.dir / f"{name}.json").unlink(missing_ok=True)

    def create_empty(self, name: str) -> Profile:
        profile = Profile(name=name)
        self.save(profile)
        return profile

    def ensure_seed(self) -> str:
        """If there are no profiles yet, create a starter one so the
        dashboard has something to show on first run. Returns a profile
        name to select."""
        names = self.list_names()
        if names:
            return names[0]
        profile = Profile(name="main")
        # Seed with a single clock so first run isn't a blank screen.
        profile.instances.append(
            InstanceRecord(
                id=new_instance_id(), widget_id="clock",
                x=0, y=0, w=3, h=2,
            )
        )
        self.save(profile)
        return "main"


class PresetStore:
    """A reusable layout library (docs/layout.md, user request).

    Presets are saved layouts decoupled from the tab bar: the user snapshots a
    tab into a named preset and can later load any preset into any tab. Stored
    in PRESETS_DIR using the same Profile JSON format as tabs, but listed and
    managed separately so they don't appear as tabs themselves.
    """

    def __init__(self, presets_dir: Path) -> None:
        self.dir = presets_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def list_names(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.json"))

    def load(self, name: str) -> Profile:
        return Profile.from_json(json.loads((self.dir / f"{name}.json").read_text()))

    def save(self, name: str, source: Profile) -> Profile:
        """Snapshot a (tab) profile's layout under a preset name."""
        preset = Profile(
            name=name,
            instances=[InstanceRecord.from_json(i.to_json()) for i in source.instances],
            columns=source.columns,
            row_height=source.row_height,
        )
        (self.dir / f"{name}.json").write_text(
            json.dumps(preset.to_json(), indent=2)
        )
        log.info("saved preset %s (%d instances)", name, len(preset.instances))
        return preset

    def delete(self, name: str) -> None:
        (self.dir / f"{name}.json").unlink(missing_ok=True)
