"""
Clipboard History widget (productivity).

Polls the X clipboard for new text and keeps a recent history; clicking an entry
copies it back (via the `clipboard` host service). Entries can be pinned (kept
at the top, never evicted) or deleted. History persists in the instance's
state_dir so it survives tab switches and restarts.

Reads the clipboard with `xclip -o -t UTF8_STRING` (text only — non-text
selections like images are ignored). state_intents pattern.

Note: a clipboard history can capture sensitive text (passwords, tokens). It's
kept local to this machine; use the trash/clear actions to drop anything you
don't want remembered.
"""

from __future__ import annotations

import asyncio
import json

from widget_dashboard import sysutil
from widget_dashboard.widget_base import WidgetBase

MAX_ENTRIES = 50
MAX_LEN = 5000          # cap stored text so a huge copy can't bloat state


class Widget(WidgetBase):
    async def start(self) -> None:
        self._file = self.ctx.state_dir / "clipboard.json"
        self._entries = self._load()
        self._last_seen = self._entries[0]["text"] if self._entries else None
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def get_initial_state(self) -> dict:
        return self._publish()

    async def on_intent(self, intent_type: str, payload: dict) -> None:
        # Operate by entry TEXT (entries are unique), never a positional index —
        # the displayed order and the stored order can diverge (pins, the poll
        # reordering), so an index would target the wrong entry.
        text = payload.get("text")
        if intent_type == "copy" and text is not None:
            # Mark seen BEFORE the await so the poll doesn't re-add our own copy.
            self._last_seen = text
            await self.ctx.host.clipboard.set_text(text)
            self._move_to_top_text(text)
        elif intent_type == "pin" and text is not None:
            self._toggle_pin_text(text)
        elif intent_type == "delete" and text is not None:
            self._entries = [e for e in self._entries if e["text"] != text]
            self._save()
        elif intent_type == "clear":
            self._entries = [e for e in self._entries if e["pinned"]]
            self._save()
        self.ctx.set_state(self._publish())

    # --- polling ---

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            text = await self._read_clipboard()
            if text and text != self._last_seen:
                self._last_seen = text
                self._add(text)
                self.ctx.set_state(self._publish())

    async def _read_clipboard(self) -> str | None:
        try:
            res = await sysutil.run(
                "xclip", "-selection", "clipboard", "-o", "-t", "UTF8_STRING",
                timeout=2,
            )
        except FileNotFoundError:
            return None
        if not res.ok:
            return None
        text = res.stdout
        return text if text and text.strip() else None

    # --- history ops ---

    def _normalize(self) -> None:
        # Keep pinned entries first (stable: preserves order within each group),
        # so the stored order matches what the frontend shows — click indices
        # then line up with self._entries.
        self._entries.sort(key=lambda e: 0 if e["pinned"] else 1)

    def _add(self, text: str) -> None:
        text = text[:MAX_LEN]
        self._entries = [e for e in self._entries if e["text"] != text]
        self._entries.insert(0, {"text": text, "pinned": False})
        self._normalize()
        # Enforce the cap, but never evict a pinned entry.
        while len(self._entries) > MAX_ENTRIES:
            for i in range(len(self._entries) - 1, -1, -1):
                if not self._entries[i]["pinned"]:
                    del self._entries[i]
                    break
            else:
                break
        self._save()

    def _move_to_top_text(self, text: str) -> None:
        for i, e in enumerate(self._entries):
            if e["text"] == text:
                self._entries.insert(0, self._entries.pop(i))
                self._normalize()
                self._save()
                return

    def _toggle_pin_text(self, text: str) -> None:
        for e in self._entries:
            if e["text"] == text:
                e["pinned"] = not e["pinned"]
                break
        self._normalize()
        self._save()

    def _publish(self) -> dict:
        return {
            "entries": [
                {"text": e["text"], "pinned": e["pinned"]}
                for e in self._entries
            ],
        }

    def _load(self) -> list:
        try:
            data = json.loads(self._file.read_text())
            return [
                {"text": str(e["text"]), "pinned": bool(e.get("pinned"))}
                for e in data if e.get("text")
            ]
        except Exception:  # noqa: BLE001 — missing/corrupt file → empty history
            return []

    def _save(self) -> None:
        try:
            self._file.write_text(json.dumps(self._entries))
        except OSError:
            self.ctx.log.warning("could not persist clipboard history")
