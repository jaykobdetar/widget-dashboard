"""
Widget registry (docs/architecture.md, docs/widgets.md).

Walks the widget directories, validates manifests, checks `requires`, and
keeps an in-memory map of available widgets. Re-runs on the manual "rescan"
action — there is no hot reload (decided in the spec: manual rescan only).

Two directories are scanned:
- the built-in widgets shipped with the app (widgets_builtin/)
- the user/AI-authored widgets dir (~/.local/share/widget-dashboard/widgets/)
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from .widget_base import WidgetBase, WidgetManifest

log = logging.getLogger("registry")


@dataclass
class RegisteredWidget:
    manifest: WidgetManifest
    widget_class: type[WidgetBase]
    available: bool
    unavailable_reason: str | None = None


class WidgetRegistry:
    def __init__(self, builtin_dir: Path, user_dir: Path) -> None:
        self.builtin_dir = builtin_dir
        self.user_dir = user_dir
        self.widgets: dict[str, RegisteredWidget] = {}

    def scan(self) -> None:
        """(Re)scan both widget directories. Replaces the registry."""
        self.widgets.clear()
        for base in (self.builtin_dir, self.user_dir):
            if not base.exists():
                continue
            for widget_dir in sorted(base.iterdir()):
                if not widget_dir.is_dir():
                    continue
                if not (widget_dir / "widget.json").exists():
                    continue
                self._load_one(widget_dir)
        log.info("registry: %d widgets loaded", len(self.widgets))

    def _load_one(self, widget_dir: Path) -> None:
        try:
            manifest = WidgetManifest.load(widget_dir)
        except Exception as e:  # malformed manifest: skip, don't crash
            log.warning("skipping %s: bad manifest: %s", widget_dir.name, e)
            return

        available, reason = self._check_requires(manifest)

        try:
            widget_class = self._import_widget_class(widget_dir)
        except Exception as e:
            log.warning("skipping %s: backend import failed: %s", manifest.id, e)
            return

        if manifest.id in self.widgets:
            log.warning("duplicate widget id %r; keeping first", manifest.id)
            return

        self.widgets[manifest.id] = RegisteredWidget(
            manifest=manifest,
            widget_class=widget_class,
            available=available,
            unavailable_reason=reason,
        )

    def _check_requires(self, manifest: WidgetManifest) -> tuple[bool, str | None]:
        """Check declared requirements. Marks widgets unavailable (with a
        reason) rather than hiding them, so the picker can explain why."""
        for cmd in manifest.requires.get("commands", []):
            if shutil.which(cmd) is None:
                return False, f"requires command '{cmd}' (not found on PATH)"
        # python deps intentionally not enforced in the foundation slice;
        # documented as a later refinement.
        return True, None

    def _import_widget_class(self, widget_dir: Path) -> type[WidgetBase]:
        """Import backend.py and return its `Widget` class."""
        module_name = f"wd_widget_{widget_dir.name}"
        spec = importlib.util.spec_from_file_location(
            module_name, widget_dir / "backend.py"
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load backend.py in {widget_dir}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "Widget"):
            raise ImportError(f"{widget_dir.name}/backend.py has no `Widget` class")
        widget_class = module.Widget
        if not issubclass(widget_class, WidgetBase):
            raise TypeError(f"{widget_dir.name}: Widget must subclass WidgetBase")
        return widget_class

    def get(self, widget_id: str) -> RegisteredWidget | None:
        return self.widgets.get(widget_id)

    def picker_list(self) -> list[dict]:
        return [
            rw.manifest.to_picker_json(rw.available, rw.unavailable_reason)
            for rw in self.widgets.values()
        ]
