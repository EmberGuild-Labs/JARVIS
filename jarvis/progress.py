"""Spoken progress on long jobs: "Render at 50 percent, about four minutes left."

Three ways to watch something:
  window   a window title that shows a percentage or "12/250" (installers, Steam,
           exporters, many renderers); done when it reaches 100% or the window closes
  process  a process; done when it exits (with periodic "still running" updates)
  file     a file being written or downloaded; done when it stops growing
Background Claude tasks also get an occasional "still working" update.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path

import psutil

log = logging.getLogger("jarvis")
PCT = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s?%")
FRAC = re.compile(r"\b(\d+)\s?(?:/|of)\s?(\d+)\b")


def _mins(sec: float) -> str:
    if sec < 90:
        return "under two minutes" if sec > 50 else "less than a minute"
    m = round(sec / 60)
    return f"about {m} minutes" if m < 90 else f"about {m / 60:.1f} hours"


def _bar(p: float) -> str:
    n = int(p / 5)
    return "█" * n + "░" * (20 - n) + f"  {p:.0f}%"


class ProgressTracker:
    def __init__(self, assistant):
        self.a = assistant
        self.jobs: dict[str, dict] = {}
        threading.Thread(target=self._bg_loop, daemon=True, name="progress-bg").start()

    def say(self, text: str):
        self.a.say_async(text)

    def panel(self, label: str, lines: list[str], secs: int = 12):
        self.a.hud.panel(f"Tracking  //  {label}", lines, secs)

    def track(self, kind: str, target: str, label: str) -> str:
        key = f"{kind}:{target.lower()}"
        if key in self.jobs:
            return f"Already tracking {label}."
        job = {"kind": kind, "target": target, "label": label, "t0": time.time(), "stop": False}
        if kind == "window":
            if not self._find_window(target):
                return f"No window with '{target}' in its title right now."
        elif kind == "process":
            if not self._procs(target):
                return f"No running process called '{target}'."
        elif kind == "file":
            if not Path(target).exists():
                return f"No file at {target} yet."
        else:
            return "kind must be window, process or file."
        self.jobs[key] = job
        threading.Thread(target=getattr(self, f"_watch_{kind}"), args=(key, job), daemon=True).start()
        self.panel(label, ["Watching..."], 6)
        return f"Tracking {label}; I'll give updates and say when it's done."

    # ------------------------------------------------------------ window
    @staticmethod
    def _find_window(part: str) -> str | None:
        from .winfront import _title, windows
        p = part.lower()
        return next((_title(h) for h in windows() if p in _title(h).lower()), None)

    @staticmethod
    def _percent(title: str) -> float | None:
        m = PCT.search(title)
        if m:
            return min(100.0, float(m.group(1).replace(",", ".")))
        m = FRAC.search(title)
        if m and int(m.group(2)) > 0 and int(m.group(1)) <= int(m.group(2)):
            return 100.0 * int(m.group(1)) / int(m.group(2))
        return None

    def _watch_window(self, key, job):
        label, nxt, first = job["label"], 25, None
        try:
            while not job["stop"]:
                time.sleep(4)
                title = self._find_window(job["target"])
                if title is None:
                    elapsed = time.time() - job["t0"]
                    self.say(f"{label.capitalize()} is done" + (f", after {_mins(elapsed)}." if elapsed > 120 else "."))
                    self.panel(label, ["Done"], 10)
                    return
                p = self._percent(title)
                if p is None:
                    continue
                if first is None:
                    first = (time.time(), p)
                    nxt = (int(p // 25) + 1) * 25
                if p >= 99.9:
                    self.say(f"{label.capitalize()} has reached 100 percent.")
                    self.panel(label, [_bar(100)], 10)
                    return
                if p >= nxt:
                    t0, p0 = first
                    rate = (p - p0) / max(1.0, time.time() - t0)
                    left = (100 - p) / rate if rate > 0 else None
                    self.say(f"{label.capitalize()} at {p:.0f} percent" +
                             (f", {_mins(left)} left." if left else "."))
                    self.panel(label, [_bar(p), title[:60]], 12)
                    nxt = (int(p // 25) + 1) * 25
        finally:
            self.jobs.pop(key, None)

    # ----------------------------------------------------------- process
    def _procs(self, name: str) -> list:
        n = name.lower().removesuffix(".exe")
        out = []
        for p in psutil.process_iter(["name"]):
            try:
                if (p.info["name"] or "").lower().removesuffix(".exe") == n:
                    out.append(p)
            except Exception:
                pass
        return out

    def _watch_process(self, key, job):
        every = 60 * float(self.a.cfg.get("progress_update_min", 10))
        last = time.time()
        try:
            while not job["stop"]:
                time.sleep(5)
                if not self._procs(job["target"]):
                    el = time.time() - job["t0"]
                    self.say(f"{job['label'].capitalize()} has finished" +
                             (f", after {_mins(el)}." if el > 120 else "."))
                    self.panel(job["label"], ["Finished"], 10)
                    return
                if time.time() - last >= every:
                    last = time.time()
                    self.say(f"{job['label'].capitalize()} is still running, {_mins(time.time() - job['t0'])} so far.")
        finally:
            self.jobs.pop(key, None)

    # -------------------------------------------------------------- file
    def _watch_file(self, key, job):
        path = Path(job["target"])
        every = 60 * float(self.a.cfg.get("progress_update_min", 10)) / 2
        last_say, last_size, still = time.time(), -1, 0
        try:
            while not job["stop"]:
                time.sleep(5)
                size = path.stat().st_size if path.exists() else -1
                still = still + 1 if size == last_size and size >= 0 else 0
                last_size = size
                if still >= 6:   # unchanged for 30 seconds
                    self.say(f"{job['label'].capitalize()} looks finished: {size / 2**20:.0f} megabytes.")
                    self.panel(job["label"], [f"{size / 2**20:.0f} MB", "Finished"], 10)
                    return
                if time.time() - last_say >= every and size > 0:
                    last_say = time.time()
                    rate = size / max(1.0, time.time() - job["t0"])
                    self.say(f"{job['label'].capitalize()}: {size / 2**20:.0f} megabytes so far, "
                             f"about {rate / 2**20:.1f} megabytes a second.")
        finally:
            self.jobs.pop(key, None)

    # --------------------------------------------- background Claude tasks
    def _bg_loop(self):
        """'Still working on X' for background tasks running over N minutes."""
        every = 60 * float(self.a.cfg.get("progress_update_min", 10))
        said: dict[str, float] = {}
        while True:
            time.sleep(30)
            if not self.a.cfg.get("progress_bg_updates", True):
                continue
            brain = getattr(self.a, "brain", None)
            for t in list(getattr(brain, "bg_tasks", {}).values()):
                if t["status"] != "running":
                    continue
                run = time.time() - t["started"]
                if run >= every and time.time() - said.get(t["id"], t["started"]) >= every:
                    said[t["id"]] = time.time()
                    self.say(f"Still working on {t['title']}, {_mins(run)} in.")

    def list(self) -> list[str]:
        return [f"{j['label']} ({j['kind']})" for j in self.jobs.values()]
