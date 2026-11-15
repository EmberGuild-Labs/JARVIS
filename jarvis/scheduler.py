"""Persistent scheduled tasks: "boot up BeamNG.drive at 17:00", "remind me every weekday at 9".

Tasks live in data/schedule.json so they survive restarts. A tiny background thread
checks every 15 seconds (negligible CPU). Tasks missed while the PC was off or JARVIS
wasn't running fire on startup if they're less than `grace_min` late; older ones are
skipped (one-offs are dropped, repeating ones roll forward).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
import uuid

from .config import DATA_DIR

log = logging.getLogger("jarvis")
SCHEDULE_PATH = DATA_DIR / "schedule.json"
ACTIONS = ("open_application", "open_url", "remind", "jarvis_request")
REPEATS = ("none", "daily", "weekdays", "weekly")


def _next(when: dt.datetime, repeat: str) -> dt.datetime | None:
    if repeat == "daily":
        return when + dt.timedelta(days=1)
    if repeat == "weekly":
        return when + dt.timedelta(days=7)
    if repeat == "weekdays":
        n = when + dt.timedelta(days=1)
        while n.weekday() >= 5:
            n += dt.timedelta(days=1)
        return n
    return None


def spoken_time(when: dt.datetime) -> str:
    today = dt.date.today()
    day = ("today" if when.date() == today else
           "tomorrow" if when.date() == today + dt.timedelta(days=1) else
           when.strftime("%A %d %B"))
    return f"{when.strftime('%H:%M')} {day}"


class Scheduler:
    def __init__(self, execute, grace_min: int = 10):
        """execute(task) runs a due task; called from the scheduler thread."""
        self.execute = execute
        self.grace = dt.timedelta(minutes=grace_min)
        self._lock = threading.Lock()
        self.tasks: list[dict] = self._load()

    # -------------------------------------------------------------- storage
    def _load(self) -> list[dict]:
        try:
            return json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save(self):
        SCHEDULE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCHEDULE_PATH.write_text(json.dumps(self.tasks, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------ api
    def add(self, when: str, action: str, target: str, repeat: str = "none",
            label: str = "") -> dict:
        if action not in ACTIONS:
            raise ValueError(f"action must be one of {ACTIONS}")
        if repeat not in REPEATS:
            raise ValueError(f"repeat must be one of {REPEATS}")
        t = dt.datetime.fromisoformat(when.strip().replace("Z", ""))
        if t.tzinfo is not None:
            t = t.astimezone().replace(tzinfo=None)
        now = dt.datetime.now()
        if t <= now:
            if repeat == "none":
                raise ValueError(f"{t:%Y-%m-%d %H:%M} is in the past (now {now:%H:%M}).")
            while t <= now:
                t = _next(t, repeat)
        task = {"id": uuid.uuid4().hex[:6], "when": t.isoformat(timespec="minutes"),
                "action": action, "target": target, "repeat": repeat,
                "label": label or target}
        with self._lock:
            self.tasks.append(task)
            self.tasks.sort(key=lambda x: x["when"])
            self._save()
        log.info("scheduled %s", task)
        return task

    def cancel(self, ident: str) -> list[dict]:
        """Cancel by id, or by fuzzy label match ('all' cancels everything)."""
        key = ident.lower().strip()
        with self._lock:
            if key == "all":
                gone, self.tasks = self.tasks, []
            else:
                gone = [t for t in self.tasks
                        if t["id"] == key or key in t["label"].lower() or key in t["target"].lower()]
                self.tasks = [t for t in self.tasks if t not in gone]
            self._save()
        return gone

    def describe(self, t: dict) -> str:
        when = dt.datetime.fromisoformat(t["when"])
        rep = "" if t["repeat"] == "none" else f" ({t['repeat']})"
        return f"{spoken_time(when)}{rep}: {t['label']}  [id {t['id']}]"

    def listing(self) -> list[str]:
        with self._lock:
            return [self.describe(t) for t in self.tasks]

    # --------------------------------------------------------------- runner
    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="scheduler").start()

    def _loop(self):
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            time.sleep(15)

    def tick(self, now: dt.datetime | None = None):
        now = now or dt.datetime.now()
        due = []
        with self._lock:
            changed = False
            for t in list(self.tasks):
                when = dt.datetime.fromisoformat(t["when"])
                if when > now:
                    continue
                late = now - when
                if late <= self.grace:
                    due.append(dict(t))
                else:
                    log.info("skipping missed task %s (%s late)", t["label"], late)
                nxt = _next(when, t["repeat"])
                while nxt is not None and nxt <= now:
                    nxt = _next(nxt, t["repeat"])
                if nxt is None:
                    self.tasks.remove(t)
                else:
                    t["when"] = nxt.isoformat(timespec="minutes")
                changed = True
            if changed:
                self.tasks.sort(key=lambda x: x["when"])
                self._save()
        for t in due:
            log.info("running scheduled task %s", t)
            try:
                self.execute(t)
            except Exception:
                log.exception("scheduled task failed")
