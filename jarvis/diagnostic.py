"""'Run a diagnostic': a full system check, shown as a scan on the HUD row by row."""
from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
import subprocess
import time

import psutil

from . import osutil

log = logging.getLogger("jarvis")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(args: list[str], timeout: float = 10) -> str:
    """Run a command, return stdout ('' on any failure or timeout)."""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              errors="replace", **osutil.no_window()).stdout.strip()
    except Exception:
        return ""


def _mac_gpu_line() -> str:
    """Apple GPU name + core count from system_profiler (slow-ish, hence the timeout)."""
    out = _run(["system_profiler", "SPDisplaysDataType"], 12)
    name = cores = ""
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.startswith("Chipset Model:") and not name:
            name = ln.split(":", 1)[1].strip()
        elif ln.startswith("Total Number of Cores:") and not cores:
            cores = ln.split(":", 1)[1].strip()
    return f"{name}, {cores} cores" if name and cores else name


def _mac_updates() -> int | None:
    """Pending macOS updates via softwareupdate (slow; None if it didn't answer in time)."""
    try:
        p = subprocess.run(["softwareupdate", "-l"], capture_output=True, text=True, timeout=25,
                           errors="replace")
    except Exception:
        return None
    text = p.stdout + p.stderr
    return sum(1 for ln in text.splitlines() if ln.strip().startswith("* Label:"))


def _ps(cmd: str, timeout: float = 25) -> str:
    return osutil.run_shell(cmd, timeout).stdout.strip()


def run_diagnostic(hud, cfg: dict) -> str:
    hud.scan_start("Diagnostic  //  full system scan")
    report: list[str] = []
    problems = 0

    def row(label, value, status="ok"):
        nonlocal problems
        problems += status != "ok"
        hud.scan_row(label, value, status)
        report.append(f"{label}: {value}{'' if status == 'ok' else ' (' + status.upper() + ')'}")
        time.sleep(0.25)   # lets each row land on the HUD like a real scan

    # System (macOS: OS version + chip)
    if osutil.IS_MAC:
        ver = _run(["sw_vers", "-productVersion"], 5)
        chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"], 5)
        if ver or chip:
            row("System", f"macOS {ver}, {chip}".strip(", "))
    # CPU
    load = psutil.cpu_percent(interval=0.8)
    row("CPU", f"{load:.0f}% load, {psutil.cpu_count()} threads", "warn" if load > 90 else "ok")
    # GPU
    if osutil.IS_MAC:
        gpu = _mac_gpu_line()
        row("GPU", gpu or "no reading", "ok" if gpu else "warn")
    elif shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,"
                                  "memory.used,memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW).stdout
            name, temp, util, used, total = [s.strip() for s in out.splitlines()[0].split(",")]
            t = int(temp)
            row("GPU", f"{t}°C, {util}% load, {int(used) / 1024:.1f}/{int(total) / 1024:.0f} GB",
                "bad" if t >= 88 else "warn" if t >= cfg.get("alert_gpu_temp_c", 83) else "ok")
        except Exception:
            row("GPU", "no reading", "warn")
    # RAM
    vm = psutil.virtual_memory()
    row("Memory", f"{vm.percent:.0f}% of {vm.total / 2**30:.0f} GB used",
        "warn" if vm.percent > 90 else "ok")
    # Drives
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        if osutil.IS_MAC and not (part.mountpoint == "/" or part.mountpoint.startswith("/Volumes/")):
            continue
        if osutil.IS_MAC and part.mountpoint.rsplit("/", 1)[-1] in ("Recovery", "Preboot", "VM", "Update",
                                                                     "xarts", "iSCPreboot", "Hardware"):
            continue   # APFS system helper volumes
        try:
            u = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        free = u.free / 2**30
        label = ("Disk " + ("startup" if part.mountpoint == "/" else part.mountpoint.rsplit("/", 1)[-1])
                 if osutil.IS_MAC else f"Drive {part.device[:2]}")
        row(label, f"{free:.0f} GB free of {u.total / 2**30:.0f} GB",
            "bad" if free < 10 else "warn" if free < cfg.get("alert_disk_free_gb", 20) else "ok")
    # Battery
    bat = psutil.sensors_battery()
    if bat:
        row("Battery", f"{bat.percent:.0f}% {'charging' if bat.power_plugged else 'on battery'}",
            "warn" if bat.percent < 20 and not bat.power_plugged else "ok")
    if osutil.IS_WIN:
        # Devices with driver problems
        try:
            bad = _ps("Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
                      "Where-Object { $_.Status -eq 'Error' } | Select-Object -ExpandProperty FriendlyName")
            names = [n for n in bad.splitlines() if n.strip()]
            row("Drivers", "all devices healthy" if not names else
                f"{len(names)} with problems: {', '.join(names[:2])}", "warn" if names else "ok")
        except Exception:
            row("Drivers", "check failed", "warn")
        # Windows Update
        try:
            n = _ps("$s=New-Object -ComObject Microsoft.Update.Session; "
                    "($s.CreateUpdateSearcher().Search('IsInstalled=0 and IsHidden=0').Updates).Count", 40)
            n = int(n or 0)
            row("Updates", "up to date" if n == 0 else f"{n} pending", "warn" if n else "ok")
        except Exception:
            row("Updates", "couldn't check", "warn")
    else:
        # macOS: no driver list; softwareupdate is slow, so it gets a timeout and "unknown"
        n = _mac_updates()
        if n is None:
            row("Updates", "unknown (check timed out)")
        else:
            row("Updates", "up to date" if n == 0 else f"{n} pending", "warn" if n else "ok")
    # Uptime
    up = dt.timedelta(seconds=int(time.time() - psutil.boot_time()))
    row("Uptime", f"{up.days}d {up.seconds // 3600}h" if up.days else f"{up.seconds // 3600}h {up.seconds % 3600 // 60}m",
        "warn" if up.days >= 14 else "ok")
    # Heaviest process
    try:
        procs = sorted(((p.info["memory_info"].rss, p.info["name"]) for p in
                        psutil.process_iter(["name", "memory_info"]) if p.info["memory_info"]
                        and p.info["name"] not in ("MemCompression", "System", "Registry", "vmmem",
                                                   "vmmemWSL", "Secure System", "kernel_task")), reverse=True)
        row("Top process", f"{procs[0][1]} {procs[0][0] / 2**30:.1f} GB")
    except Exception:
        pass

    verdict = "All systems nominal" if problems == 0 else \
        f"{problems} item{'s' if problems != 1 else ''} need{'s' if problems == 1 else ''} attention"
    hud.scan_end(verdict, 40)
    log.info("diagnostic: %s", verdict)
    return verdict + ".\n" + "\n".join(report)
