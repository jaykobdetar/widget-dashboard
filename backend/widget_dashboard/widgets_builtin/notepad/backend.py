"""
Sticky Note widget (productivity).

A per-instance text pad. The frontend autosaves as you type; the backend stores
the text in this instance's state_dir so it survives tab switches and restarts.
free-form pattern: backend sends the saved text (and note color) on connect and
on settings change; the frontend sends {save: text} as you edit.
"""

from __future__ import annotations

from widget_dashboard.widget_base import WidgetBase


class Widget(WidgetBase):
    async def start(self) -> None:
        self._file = self.ctx.state_dir / "note.txt"
        text = ""
        try:
            text = self._file.read_text()
        except OSError:
            pass
        self.ctx.send({"text": text})

    async def on_message(self, msg: dict) -> None:
        if isinstance(msg, dict) and "save" in msg:
            try:
                self._file.write_text(str(msg["save"]))
            except OSError:
                self.ctx.log.warning("could not save note")
