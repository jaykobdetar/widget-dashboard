"""
Audio Mixer widget (docs/default-widgets.md).

PipeWire mixer through its PulseAudio-compatible interface (`pactl`), which the
spec names as the fallback and which is the most parseable surface (it speaks
JSON with `-f json`). Exposes per-application stream sliders + mute and
output/input device selection.

state_intents pattern (docs/widgets.md 4.5): the backend polls pactl and
publishes a state object; the frontend renders sliders and emits intents:
  set_sink_volume / set_sink_mute / set_default_sink
  set_source_volume / set_source_mute / set_default_source
  set_stream_volume / set_stream_mute

Live VU peak metering (the spec's stretch goal) needs a recording stream per
device; this slice polls volume/mute state instead, a documented simplification
(docs/default-widgets.md). Polling cadence is modest to stay cheap.
"""

from __future__ import annotations

import array
import asyncio
import json

from widget_dashboard.widget_base import WidgetBase


async def _pactl(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "pactl", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return out.decode("utf-8", "replace")


async def _pactl_json(*args: str):
    try:
        return json.loads(await _pactl("-f", "json", *args) or "[]")
    except json.JSONDecodeError:
        return []


def _avg_volume(volume: dict) -> int:
    """Mean channel volume as a 0-150 percentage."""
    if not volume:
        return 0
    pcts = []
    for ch in volume.values():
        vp = ch.get("value_percent", "0%").rstrip("%")
        try:
            pcts.append(int(vp))
        except ValueError:
            pass
    return round(sum(pcts) / len(pcts)) if pcts else 0


def _sink_kind(d: dict) -> str:
    """A coarse device type the frontend turns into an icon."""
    props = d.get("properties", {})
    blob = " ".join(str(x).lower() for x in (
        d.get("active_port", ""), d.get("description", ""),
        props.get("device.form_factor", ""), props.get("device.icon_name", ""),
    ))
    if props.get("device.bus") == "bluetooth" or "bluetooth" in blob:
        return "bluetooth"
    if "headphone" in blob or "headset" in blob:
        return "headphones"
    if "hdmi" in blob or "displayport" in blob or "tv" in blob:
        return "hdmi"
    return "speaker"


_GENERIC_MEDIA = {"", "(null)", "null", "playback", "playstream", "audio stream", "audio"}


def _stream_media(props: dict, app: str) -> str:
    """The 'what's playing' line, if it adds anything beyond the app name."""
    media = props.get("media.name", "") or ""
    if media.lower() in _GENERIC_MEDIA or media.lower() == app.lower():
        return ""
    return media


class Widget(WidgetBase):
    async def start(self) -> None:
        self._last = None
        self._proc: asyncio.subprocess.Process | None = None
        self._wake = asyncio.Event()
        self._wake.set()                       # push an initial snapshot
        # Per-application audio activity meters (parec --monitor-stream).
        self._levels: dict[str, float] = {}    # stream id -> peak 0..1
        self._meters: dict[str, tuple] = {}     # stream id -> (proc, task)
        self._tasks = [
            asyncio.create_task(self._subscribe()),
            asyncio.create_task(self._worker()),
            asyncio.create_task(self._emit_levels()),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
        for sid in list(self._meters):
            self._stop_meter(sid)
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass

    async def get_initial_state(self) -> dict:
        return await self._snapshot()

    async def on_intent(self, intent_type: str, payload: dict) -> None:
        p = payload
        if intent_type == "set_sink_volume":
            await _pactl("set-sink-volume", str(p["id"]), f"{int(p['pct'])}%")
        elif intent_type == "set_sink_mute":
            await _pactl("set-sink-mute", str(p["id"]), "1" if p["muted"] else "0")
        elif intent_type == "set_default_sink":
            await _pactl("set-default-sink", str(p["name"]))
        elif intent_type == "set_source_volume":
            await _pactl("set-source-volume", str(p["id"]), f"{int(p['pct'])}%")
        elif intent_type == "set_source_mute":
            await _pactl("set-source-mute", str(p["id"]), "1" if p["muted"] else "0")
        elif intent_type == "set_default_source":
            await _pactl("set-default-source", str(p["name"]))
        elif intent_type == "set_stream_volume":
            await _pactl("set-sink-input-volume", str(p["id"]), f"{int(p['pct'])}%")
        elif intent_type == "set_stream_mute":
            await _pactl("set-sink-input-mute", str(p["id"]), "1" if p["muted"] else "0")
        # Push the new state right away so the UI feels responsive.
        self.ctx.set_state(await self._snapshot())

    async def _subscribe(self) -> None:
        """Wake the worker on real PipeWire/Pulse change events instead of
        polling. `pactl subscribe` is one persistent process; if it's missing,
        the worker's 5s safety timeout still keeps state fresh."""
        try:
            self._proc = await asyncio.create_subprocess_exec(
                "pactl", "subscribe",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return
        assert self._proc.stdout is not None
        async for raw in self._proc.stdout:
            line = raw.decode("utf-8", "replace")
            if any(k in line for k in ("sink", "source", "server", "card")):
                self._wake.set()

    async def _worker(self) -> None:
        """Snapshot + push when woken by an event (debounced) or every 5s as a
        safety net. Only pushes when the state actually changed."""
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            await asyncio.sleep(0.15)          # coalesce a burst of events
            self._wake.clear()
            state = await self._snapshot()
            await self._sync_meters([s["id"] for s in state.get("streams", [])])
            if state != self._last:
                self.ctx.set_state(state)
                self._last = state

    # --- per-application audio activity metering ---

    async def _sync_meters(self, stream_ids: list) -> None:
        """Keep one parec monitor per current app stream (pavucontrol-style)."""
        want = {str(i) for i in stream_ids}
        for sid in list(self._meters):
            if sid not in want:
                self._stop_meter(sid)
        for sid in want:
            if sid not in self._meters:
                await self._start_meter(sid)

    async def _start_meter(self, sid: str) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                # --latency-msec keeps parec from buffering, so peaks arrive
                # promptly in small fragments (a smooth, responsive meter).
                "parec", "--monitor-stream", sid, "--client-name=wd-meter",
                "--format=s16le", "--rate=8000", "--channels=1", "--raw",
                "--latency-msec=25",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return

        async def read() -> None:
            assert proc.stdout is not None
            try:
                while True:
                    data = await proc.stdout.read(400)   # ~25ms @ 8kHz mono s16
                    if not data:
                        break
                    samples = array.array("h")
                    samples.frombytes(data[: len(data) // 2 * 2])
                    peak = (max(map(abs, samples)) if samples else 0) / 32768.0
                    # Peak-HOLD: keep the loudest sample seen since the next
                    # emit (decay happens on the emit clock, not per read).
                    if peak > self._levels.get(sid, 0.0):
                        self._levels[sid] = peak
            except asyncio.CancelledError:
                pass

        self._meters[sid] = (proc, asyncio.create_task(read()))

    def _stop_meter(self, sid: str) -> None:
        entry = self._meters.pop(sid, None)
        if entry:
            proc, task = entry
            task.cancel()
            if proc.returncode is None:
                proc.terminate()
        self._levels.pop(sid, None)

    async def _emit_levels(self) -> None:
        """Push activity levels ~20x/s on the free-form channel, separate from
        state so meters animate without re-rendering the rows. After each send
        the held peaks decay on this steady clock, so the bar falls smoothly
        between peaks while new peaks (peak-hold in read()) refill it instantly."""
        while True:
            await asyncio.sleep(0.05)                 # 20 fps
            if not self._meters:
                continue
            self.ctx.send({"levels": {k: round(v, 3) for k, v in self._levels.items()}})
            for k in list(self._levels):
                self._levels[k] *= 0.6                # ~classic VU fall to 0 in ~0.3s

    async def _snapshot(self) -> dict:
        default_sink = (await _pactl("get-default-sink")).strip()
        default_source = (await _pactl("get-default-source")).strip()
        sinks = await _pactl_json("list", "sinks")
        sources = await _pactl_json("list", "sources")
        inputs = await _pactl_json("list", "sink-inputs")

        def dev(d: dict, default_name: str, kind: str) -> dict:
            return {
                "id": d.get("index"),
                "name": d.get("name", ""),
                "desc": d.get("description") or d.get("name", ""),
                "volume": _avg_volume(d.get("volume", {})),
                "muted": bool(d.get("mute")),
                "is_default": d.get("name") == default_name,
                "icon": "mic" if kind == "source" else _sink_kind(d),
            }

        def stream(d: dict) -> dict:
            props = d.get("properties", {})
            app = (props.get("application.name")
                   or props.get("media.name") or "audio")
            return {
                "id": d.get("index"),
                "app": app,
                "media": _stream_media(props, app),
                "binary": (props.get("application.process.binary") or "").lower(),
                "role": props.get("media.role", ""),
                "corked": bool(d.get("corked")),
                "volume": _avg_volume(d.get("volume", {})),
                "muted": bool(d.get("mute")),
            }

        return {
            "sinks": [dev(s, default_sink, "sink") for s in sinks],
            "sources": [dev(s, default_source, "source") for s in sources
                        if not s.get("monitor_source")],
            "streams": [stream(s) for s in inputs],
        }
