"""
Filesystem locations (docs/architecture.md "Config and state on disk").
"""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(env: str, default: Path) -> Path:
    val = os.environ.get(env)
    return Path(val) if val else default


HOME = Path.home()

CONFIG_DIR = _xdg("XDG_CONFIG_HOME", HOME / ".config") / "widget-dashboard"
DATA_DIR = _xdg("XDG_DATA_HOME", HOME / ".local" / "share") / "widget-dashboard"
STATE_DIR = _xdg("XDG_STATE_HOME", HOME / ".local" / "state") / "widget-dashboard"

CONFIG_FILE = CONFIG_DIR / "config.yaml"
PROFILES_DIR = CONFIG_DIR / "profiles"
PRESETS_DIR = CONFIG_DIR / "presets"          # reusable layout library
USER_WIDGETS_DIR = DATA_DIR / "widgets"
INCOMING_WIDGETS_DIR = DATA_DIR / "incoming"   # staging for installs
WIDGET_STATE_DIR = STATE_DIR / "widget-state"
LOCKDOWN_SOCKET = STATE_DIR / "lockdown.sock"

# Shipped with the app.
_PKG_ROOT = Path(__file__).resolve().parent
BUILTIN_WIDGETS_DIR = _PKG_ROOT / "widgets_builtin"
FRONTEND_DIR = _PKG_ROOT.parent.parent / "frontend"

for d in (PROFILES_DIR, PRESETS_DIR, USER_WIDGETS_DIR, INCOMING_WIDGETS_DIR,
          WIDGET_STATE_DIR):
    d.mkdir(parents=True, exist_ok=True)
