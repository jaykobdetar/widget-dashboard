"""
Global configuration (docs/architecture.md "Config and state on disk").

config.yaml holds dashboard-wide settings: which monitor the dashboard owns,
the lockdown allowlist, and the default trigger-response template. It is read
at startup and rewritten when the user changes a global setting from the UI.

Kept deliberately small: per-tab and per-instance state lives in profiles, not
here. This is only the handful of things that are global to the whole app.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("config")


# The default response config seeded onto every new instance at placement
# time (docs/triggers.md "Precedence"). The user edits down from here.
DEFAULT_RESPONSE: dict[str, Any] = {
    "badge": True,
    "toast": {"enabled": True, "text": "{__widget__}: triggered"},
    "sound": {"enabled": False},
    "flash": False,
    "overlay": False,
    "reveal": False,
    "switch_to_tab": False,
}

DEFAULTS: dict[str, Any] = {
    # 0-based index into the connected outputs (matches launch-ui.sh).
    "dashboard_monitor": 0,
    # Where lockdown bounces stray windows to.
    "fallback_monitor": 1,
    # WM_CLASS values lockdown leaves alone on the dashboard monitor.
    "lockdown_allowlist": ["chromium", "chromium-browser", "google-chrome"],
    "lockdown_enabled": True,
    "default_response": DEFAULT_RESPONSE,
    # Global theme: accent + base (page) color. The frontend derives the rest of
    # the --wd-* tokens from these (incl. a dark scheme if the base is dark).
    "theme": {"accent": "#19e3cf", "bg": "#ffffff"},
}


class Config:
    """Loads/saves config.yaml, falling back to DEFAULTS for missing keys."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        self._data = dict(DEFAULTS)
        if self.path.exists():
            try:
                loaded = yaml.safe_load(self.path.read_text()) or {}
                if isinstance(loaded, dict):
                    self._data.update(loaded)
            except Exception as e:
                log.warning("could not parse %s: %s; using defaults", self.path, e)
        # Make sure default_response is fully populated even if the file
        # supplied only a partial dict.
        merged = dict(DEFAULT_RESPONSE)
        merged.update(self._data.get("default_response") or {})
        self._data["default_response"] = merged

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(self._data, sort_keys=False))
        log.info("saved config to %s", self.path)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    @property
    def default_response(self) -> dict:
        return self._data["default_response"]

    def as_dict(self) -> dict:
        return dict(self._data)
