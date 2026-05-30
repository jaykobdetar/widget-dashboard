#!/usr/bin/env python3
"""
wdwidget — pack and validate Widget Dashboard widgets (docs/packaging.md).

Usage:
  wdwidget pack <widget_dir> [-o OUT_DIR]   # make <id>.wdwidget
  wdwidget validate <file.wdwidget>          # check without installing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from the repo without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from widget_dashboard import packaging  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(prog="wdwidget")
    sub = p.add_subparsers(dest="cmd", required=True)

    pk = sub.add_parser("pack", help="zip a widget folder into a .wdwidget")
    pk.add_argument("widget_dir", type=Path)
    pk.add_argument("-o", "--out-dir", type=Path, default=None)

    vd = sub.add_parser("validate", help="validate a .wdwidget")
    vd.add_argument("file", type=Path)

    args = p.parse_args()

    if args.cmd == "pack":
        out = packaging.pack(args.widget_dir, args.out_dir)
        print(f"packed → {out}")
        return 0

    if args.cmd == "validate":
        r = packaging.validate(args.file)
        if not r.ok:
            print(f"INVALID: {r.error}")
            return 1
        print(f"OK: {r.widget_id} v{r.version}")
        print(f"  permissions:   {r.permissions}")
        print(f"  host_services: {r.host_services}")
        print(f"  requires:      {r.requires}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
