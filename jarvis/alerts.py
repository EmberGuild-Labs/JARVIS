"""Background health checks that speak up when something needs attention.

One pass a minute (a few ms of CPU): GPU temperature (nvidia-smi), sustained CPU
load, RAM pressure, a single process hogging memory, low disk space, and finished
downloads. Each alert type has a cooldown so it never nags.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import psutil

log = logging.getLogger("jarvis")

PARTIAL = (".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload")


class SystemMonitor:
    def __init__(self, notify, cfg: dict):
        """notify(kind, title, spoken, lines) delivers an alert."""
        self.notify, self.cfg = notify, cfg
        self.last: dict[str, float] = {}
        self.cpu_high = 0
        self.ram_high = 0
        self.nvsmi = shutil.which("nvidia-smi")
        self.downloads = Path.home() / "Downloads"
        self.seen = self._snapshot()
        self.net_down, self.net_lost, self.hung = 0, False, 0

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="monitor").start()

    def _loop(self):
        psutil.cpu_percent()  # prime
        while True:
            time.sleep(60)
            if not self.cfg.get("alerts_enabled", True):
                continue
            try:
                self.check()
            except Exception:
                log.exception("monitor check failed")

    def _fire(self, kind: str, cooldown_min: float, title: str, spoken: str, lines: list[str]):
        if time.time() - self.last.get(kind, 0) < cooldown_min * 60:
            return
        self.last[kind] = time.time()
        log.info("alert %s: %s", kind, spoken)
        self.notify(kind, title, spoken, lines)

    # ----------------------------------------------------------------- checks
    def check(self):
        c = self.cfg
        # GPU temperature
        if self.nvsmi:
            try:
                out = subprocess.run(
                    [self.nvsmi, "--query-gpu=temperature.gpu,utilization.gpu,name",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip().splitlines()
                if out:
                    temp, util, name = [x.strip() for x in out[0].split(",", 2)]
                    if int(temp) >= c.get("alert_gpu_temp", 85):
                        self._fire("gpu_temp", 20, "GPU temperature",
                                   f"Your graphics card is running hot, at {temp} degrees.",
                                   [f"{name}: {temp} C, {util}% load"])
            except Exception:
                pass

        # CPU sustained load (5 consecutive minutes)
        cpu = psutil.cpu_percent()
        self.cpu_high = self.cpu_high + 1 if cpu >= c.get("alert_cpu_percent", 90) else 0
        if self.cpu_high >= 5:
            top = self._top_cpu()
            self._fire("cpu", 30, "CPU load",
                       f"Your processor has been near full load for five minutes{', mostly ' + top if top else ''}.",
                       [f"CPU {cpu:.0f}%"] + ([f"Top: {top}"] if top else []))

        # RAM pressure (3 consecutive minutes) and a single memory hog
        vm = psutil.virtual_memory()
        self.ram_high = self.ram_high + 1 if vm.percent >= c.get("alert_ram_percent", 90) else 0
        if self.ram_high >= 3:
            self._fire("ram", 30, "Memory", f"Memory is at {vm.percent:.0f} percent.",
                       [f"RAM {vm.percent:.0f}% of {vm.total / 2**30:.0f} GB"])
        hog = self._memory_hog(vm.total)
        if hog:
            name, gb = hog
            self._fire(f"hog:{name}", 60, "Memory hog",
                       f"{name} is using {gb:.0f} gigabytes of memory.",
                       [f"{name}: {gb:.1f} GB"])

        # Disk space (system drive), at most twice a day
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        du = psutil.disk_usage(drive)
        free_gb = du.free / 2**30
        if free_gb < c.get("alert_disk_free_gb", 20) or du.percent >= 95:
            self._fire("disk", 720, "Disk space",
                       f"Your {drive[0]} drive is getting full: {free_gb:.0f} gigabytes left.",
                       [f"{drive} {du.percent:.0f}% used, {free_gb:.0f} GB free"])

        # Battery (laptops): low, then critical
        bat = psutil.sensors_battery()
        if bat and not bat.power_plugged:
            if bat.percent <= 10:
                self._fire("battery_crit", 10, "Battery critical",
                           f"Battery is at {bat.percent:.0f} percent. Plug in soon.", [f"Battery {bat.percent:.0f}%"])
            elif bat.percent <= c.get("alert_battery_percent", 20):
                self._fire("battery", 60, "Battery low",
                           f"Battery is down to {bat.percent:.0f} percent.", [f"Battery {bat.percent:.0f}%"])

        # Internet: lost after two failed checks in a row, and when it comes back
        if c.get("alert_internet", True):
            up = self._online()
            self.net_down = 0 if up else self.net_down + 1
            if self.net_down == 2:
                self.net_lost = True
                self._fire("net_down", 10, "Network", "The internet connection has dropped.", ["Offline"])
            elif up and self.net_lost:
                self.net_lost = False
                self.last.pop("net_down", None)
                self._fire("net_up", 0, "Network", "We're back online.", ["Online"])

        # The app you're using has frozen ("not responding") for over a minute
        hung = self._hung_foreground()
        self.hung = self.hung + 1 if hung else 0
        if self.hung >= 2 and hung:
            self._fire(f"hung:{hung}", 30, "Not responding",
                       f"{hung} has stopped responding.", [f"{hung}: not responding"])

        # Finished downloads
        if c.get("alert_downloads", True):
            now = self._snapshot()
            new = [n for n in now - self.seen if not n.lower().endswith(PARTIAL)]
            self.seen = now
            for name in new[:3]:
                self._fire(f"dl:{name}", 0, "Download complete",
                           f"Download finished: {Path(name).stem[:40]}.", [name])

    @staticmethod
    def _online() -> bool:
        import socket
        for host in (("1.1.1.1", 443), ("8.8.8.8", 53)):
            try:
                with socket.create_connection(host, timeout=3):
                    return True
            except OSError:
                pass
        return False

    @staticmethod
    def _hung_foreground() -> str:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        h = u.GetForegroundWindow()
        if not h or not u.IsHungAppWindow(h):
            return ""
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        try:
            return psutil.Process(pid.value).name().removesuffix(".exe")
        except Exception:
            return "An app"

    def _snapshot(self) -> set[str]:
        try:
            return {p.name for p in self.downloads.iterdir()}
        except Exception:
            return set()

    def _top_cpu(self) -> str:
        best = None
        for p in psutil.process_iter(["name", "cpu_percent"]):
            try:
                v = p.info["cpu_percent"] or 0
                if p.info["name"] not in ("System Idle Process", "Idle") and (best is None or v > best[0]):
                    best = (v, p.info["name"])
            except Exception:
                pass
        return best[1].removesuffix(".exe") if best else ""

    def _memory_hog(self, total: int):
        limit = total * self.cfg.get("alert_process_ram_share", 0.4)
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                rss = p.info["memory_info"].rss
                name = p.info["name"]
                if rss > limit and name not in ("MemCompression", "System", "vmmem", "vmmemWSL"):
                    return name.removesuffix(".exe"), rss / 2**30
            except Exception:
                pass
        return None
