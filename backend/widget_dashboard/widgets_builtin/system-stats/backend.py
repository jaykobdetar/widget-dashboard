"""
System Stats widget (docs/default-widgets.md).

Samples CPU / GPU / memory / disk / network and the relevant temperatures, and
pushes a compact reading each tick (free-form pattern). The frontend keeps a
short history per metric for the sparklines.

CPU/mem/disk/net come from /proc + shutil (no subprocess). Temps are plain
hwmon file reads. GPU is read once-per-tick from a *persistent* `nvidia-smi`
loop process (so we don't pay nvidia-smi's startup cost every tick) or from the
amdgpu sysfs counter; if neither exists the GPU row is simply omitted.
"""

from __future__ import annotations

import asyncio
import glob
import shutil
import time

from widget_dashboard.widget_base import WidgetBase


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def _read_temp(path: str) -> float | None:
    """hwmon tempN_input is in millidegrees C."""
    raw = _read(path).strip()
    if not raw:
        return None
    try:
        return round(int(raw) / 1000.0)
    except ValueError:
        return None


def _find_temp_paths() -> tuple[str | None, str | None]:
    """Locate a CPU-package temp and an NVMe/SSD temp from hwmon."""
    cpu_path = ssd_path = None
    for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        name = _read(f"{hwmon}/name").strip()
        for inp in sorted(glob.glob(f"{hwmon}/temp*_input")):
            label = _read(inp.replace("_input", "_label")).strip()
            if name in ("k10temp", "zenpower") and label in ("Tctl", "Tdie") and not cpu_path:
                cpu_path = inp
            elif name == "coretemp" and label.startswith("Package") and not cpu_path:
                cpu_path = inp
            elif name == "nvme" and (label == "Composite" or not ssd_path):
                ssd_path = inp
        # Fallback: first temp on a k10temp/coretemp if labels were unhelpful.
        if not cpu_path and name in ("k10temp", "coretemp", "zenpower"):
            first = sorted(glob.glob(f"{hwmon}/temp*_input"))
            if first:
                cpu_path = first[0]
    return cpu_path, ssd_path


class Widget(WidgetBase):
    async def start(self) -> None:
        self._prev_cpu = self._cpu_times()
        self._prev_net = self._net_bytes()
        self._prev_t = time.monotonic()
        self._cpu_temp_path, self._ssd_temp_path = _find_temp_paths()

        # GPU detection: NVIDIA via a persistent nvidia-smi loop, else amdgpu sysfs.
        self._gpu = {}                       # latest reading
        self._gpu_mode = None
        self._gpu_proc = None
        self._amd_busy = None
        self._amd_temp = None
        if shutil.which("nvidia-smi"):
            self._gpu_mode = "nvidia"
        else:
            busy = glob.glob("/sys/class/drm/card*/device/gpu_busy_percent")
            if busy:
                self._gpu_mode = "amd"
                self._amd_busy = busy[0]
                hw = glob.glob("/sys/class/drm/card*/device/hwmon/hwmon*/temp1_input")
                self._amd_temp = hw[0] if hw else None

        self._tasks = [asyncio.create_task(self._loop())]
        if self._gpu_mode == "nvidia":
            self._tasks.append(asyncio.create_task(self._nvidia_loop()))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self._gpu_proc is not None and self._gpu_proc.returncode is None:
            self._gpu_proc.terminate()
            await self._gpu_proc.wait()   # reap so the nvidia-smi child doesn't linger
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        interval = float(self.ctx.settings.get("interval", 1.5) or 1.5)
        while True:
            # Send immediately on connect so the widget isn't blank for a tick.
            self.ctx.send(self._sample())
            await asyncio.sleep(interval)

    async def _nvidia_loop(self) -> None:
        """One persistent nvidia-smi streaming a line every ~1.5s; avoids the
        per-call startup cost of spawning nvidia-smi on every tick."""
        try:
            self._gpu_proc = await asyncio.create_subprocess_exec(
                "nvidia-smi",
                "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits", "-lms", "1500",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return
        assert self._gpu_proc.stdout is not None
        async for raw in self._gpu_proc.stdout:
            parts = [p.strip() for p in raw.decode("utf-8", "replace").split(",")]
            if len(parts) >= 4:
                try:
                    used, total = float(parts[2]), float(parts[3])
                    self._gpu = {
                        "util": round(float(parts[0])),
                        "temp": round(float(parts[1])),
                        "mem_pct": round(100.0 * used / total) if total else 0,
                    }
                except ValueError:
                    pass

    # --- sampling ---

    def _cpu_times(self) -> tuple[int, int]:
        line = _read("/proc/stat").splitlines()
        if not line:
            return (0, 0)
        parts = [int(x) for x in line[0].split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        return (idle, sum(parts))

    def _net_bytes(self) -> tuple[int, int]:
        rx = tx = 0
        for line in _read("/proc/net/dev").splitlines()[2:]:
            iface, _, rest = line.partition(":")
            if iface.strip() == "lo":
                continue
            cols = rest.split()
            if len(cols) >= 9:
                rx += int(cols[0])
                tx += int(cols[8])
        return (rx, tx)

    def _gpu_reading(self) -> dict:
        if self._gpu_mode == "nvidia":
            return dict(self._gpu)
        if self._gpu_mode == "amd":
            busy = _read(self._amd_busy).strip() if self._amd_busy else ""
            out = {"util": int(busy)} if busy.isdigit() else {}
            t = _read_temp(self._amd_temp) if self._amd_temp else None
            if t is not None:
                out["temp"] = t
            return out
        return {}

    def _sample(self) -> dict:
        now = time.monotonic()
        dt = max(0.001, now - self._prev_t)

        idle, total = self._cpu_times()
        pidle, ptotal = self._prev_cpu
        dtotal = total - ptotal
        cpu = 100.0 * (1 - (idle - pidle) / dtotal) if dtotal else 0.0
        self._prev_cpu = (idle, total)

        mem = self._meminfo()

        rx, tx = self._net_bytes()
        prx, ptx = self._prev_net
        rx_rate, tx_rate = (rx - prx) / dt, (tx - ptx) / dt
        self._prev_net = (rx, tx)
        self._prev_t = now

        try:
            du = shutil.disk_usage("/")
            disk_pct, disk_free_gb = 100.0 * du.used / du.total, du.free / 1e9
        except OSError:
            disk_pct, disk_free_gb = 0.0, 0.0

        gpu = self._gpu_reading()
        return {
            "cpu": round(max(0.0, min(100.0, cpu)), 1),
            "cpu_temp": _read_temp(self._cpu_temp_path) if self._cpu_temp_path else None,
            "gpu_present": self._gpu_mode is not None,
            "gpu": gpu.get("util"),
            "gpu_temp": gpu.get("temp"),
            "gpu_mem_pct": gpu.get("mem_pct"),
            "mem_pct": mem["pct"],
            "mem_used_gb": mem["used_gb"],
            "mem_total_gb": mem["total_gb"],
            "disk_pct": round(disk_pct, 1),
            "disk_free_gb": round(disk_free_gb, 1),
            "disk_temp": _read_temp(self._ssd_temp_path) if self._ssd_temp_path else None,
            "net_rx_kbs": round(rx_rate / 1024, 1),
            "net_tx_kbs": round(tx_rate / 1024, 1),
        }

    def _meminfo(self) -> dict:
        info = {}
        for line in _read("/proc/meminfo").splitlines():
            key, _, val = line.partition(":")
            info[key] = int(val.strip().split()[0]) if val.strip() else 0
        total = info.get("MemTotal", 1)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        used = total - avail
        return {
            "pct": round(100.0 * used / total, 1),
            "used_gb": round(used / 1024 / 1024, 1),
            "total_gb": round(total / 1024 / 1024, 1),
        }
