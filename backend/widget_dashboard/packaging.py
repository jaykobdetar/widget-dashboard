"""
Widget packaging & install (docs/packaging.md).

A .wdwidget is a zip with the widget's files at the root. This module packs a
widget folder into one, validates a .wdwidget, and installs it (with the
permission-confirm step left to the caller/UI — this module surfaces the
declared permissions but does not decide trust).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

REQUIRED_FILES = ("widget.json", "backend.py", "frontend.js")
EXTENSION = ".wdwidget"


@dataclass
class ValidationResult:
    ok: bool
    widget_id: str | None = None
    version: str | None = None
    permissions: dict | None = None
    host_services: list | None = None
    requires: dict | None = None
    error: str | None = None


def _is_valid_id(wid: str) -> bool:
    return bool(wid) and wid == wid.lower() and all(
        c.isalnum() or c == "-" for c in wid
    )


def _unsafe_member(name: str) -> bool:
    """A zip entry that would write outside the extraction dir. CPython's
    extractall already strips these, but we reject them explicitly so a hostile
    .wdwidget is refused at validate() rather than silently sanitized — and so
    the guarantee doesn't depend on stdlib internals."""
    if name.startswith("/") or name.startswith("\\") or ":" in name.split("/")[0]:
        return True   # absolute path or drive letter
    return any(part == ".." for part in name.replace("\\", "/").split("/"))


def pack(widget_dir: Path, out_dir: Path | None = None) -> Path:
    """Zip a widget folder's contents (at the zip root) into <id>.wdwidget."""
    manifest = json.loads((widget_dir / "widget.json").read_text())
    wid = manifest["id"]
    out_dir = out_dir or widget_dir.parent
    out_path = out_dir / f"{wid}{EXTENSION}"

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(widget_dir.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(widget_dir)
            # Skip build/editor junk that shouldn't ship in a widget.
            parts = rel.parts
            if any(p == "__pycache__" or p.startswith(".") for p in parts):
                continue
            if f.suffix in (".pyc", ".pyo"):
                continue
            zf.write(f, rel.as_posix())
    return out_path


def validate(wdwidget_path: Path) -> ValidationResult:
    """Validate a .wdwidget without installing it. Surfaces declared
    permissions/host_services/requires for the UI's confirm step."""
    if not zipfile.is_zipfile(wdwidget_path):
        return ValidationResult(False, error="not a valid zip archive")

    with zipfile.ZipFile(wdwidget_path) as zf:
        names = set(zf.namelist())
        bad = next((n for n in names if _unsafe_member(n)), None)
        if bad is not None:
            return ValidationResult(False, error=f"unsafe path in archive: {bad!r}")
        for required in REQUIRED_FILES:
            if required not in names:
                return ValidationResult(
                    False, error=f"missing required file: {required} "
                                 f"(files must be at the zip root)"
                )
        try:
            manifest = json.loads(zf.read("widget.json"))
        except Exception as e:
            return ValidationResult(False, error=f"bad widget.json: {e}")

    wid = manifest.get("id", "")
    if not _is_valid_id(wid):
        return ValidationResult(
            False, error=f"invalid id {wid!r} (lowercase + hyphens only)"
        )

    return ValidationResult(
        ok=True,
        widget_id=wid,
        version=manifest.get("version", "0.0.0"),
        permissions=manifest.get("permissions", {}),
        host_services=manifest.get("host_services", []),
        requires=manifest.get("requires", {}),
    )


def install(wdwidget_path: Path, widgets_dir: Path, *, overwrite: bool = False) -> Path:
    """Install a validated .wdwidget into widgets_dir/<id>/.

    The caller is responsible for having run validate() and obtained the
    user's permission confirmation first (docs/packaging.md). Returns the
    installed widget directory.
    """
    result = validate(wdwidget_path)
    if not result.ok:
        raise ValueError(f"invalid widget: {result.error}")

    target = widgets_dir / result.widget_id  # type: ignore[arg-type]
    if target.exists() and not overwrite:
        raise FileExistsError(f"widget {result.widget_id} already installed")

    # Extract to a temp dir first, then move into place atomically-ish.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp) / result.widget_id  # type: ignore[arg-type]
        tmp_dir.mkdir()
        with zipfile.ZipFile(wdwidget_path) as zf:
            zf.extractall(tmp_dir)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(tmp_dir), str(target))
    return target
