"""Proactive suggestions: JARVIS notices your patterns and offers to help.

  - Habits: "You usually play BeamNG around now, sir. Shall I launch it?" (a game you've
    played at this hour on at least 3 of the last 14 days, not running yet)
  - Breaks: "You've been at it for three hours. Perhaps a short break?"
  - Late nights: "It's past one, sir. Shall I initiate night protocol?"

Learns locally from which games/apps are in the foreground (one check a minute, only
while you're active) into data/usage.json. At most one suggestion every 45 minutes,
and never while you're away, in standby, in game/focus mode, or mid-conversation.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from pathlib import Path

import psutil

from .config import DATA_DIR

log = logging.getLogger("jarvis")
USAGE_PATH = DATA_DIR / "usage.json"
KEEP_DAYS = 30


def foreground_app() -> tuple[str, str] | None:
    """(friendly name, exe name) of the foreground app if it's a game (Steam) or
    something JARVIS has launched for you before; else None."""
    import ctypes
    from ctypes import wintypes
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        exe = psutil.Process(pid.value).exe()
    except Exception:
        return None
    p = Path(exe)
    parts = [x.lower() for x in p.parts]
    if "steamapps" in parts and "common" in parts:
        game = p.parts[parts.index("common") + 1]
        return game, p.name.lower()
    return None


class SuggestionEngine:
    def __init__(self, assistant, clock=time.time):
        self.a = assistant
        self.cfg = assistant.cfg
        self.clock = clock
        self.usage = self._load()
        self.active_since: float | None = None
        self.last_suggestion = 0.0
        self.last_break = clock()
        self.suggested_today: dict[str, str] = {}

    # ------------------------------------------------------------- storage
    def _load(self) -> dict:
        try:
            return json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"days": {}, "exes": {}}

    def _save(self):
        cutoff = (dt.date.today() - dt.timedelta(days=KEEP_DAYS)).isoformat()
        self.usage["days"] = {d: v for d, v in self.usage["days"].items() if d >= cutoff}
        USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        USAGE_PATH.write_text(json.dumps(self.usage), encoding="utf-8")

    def record(self, app: str, exe: str, when: dt.datetime | None = None):
        when = when or dt.datetime.now()
        day = self.usage["days"].setdefault(when.date().isoformat(), {})
        hour = day.setdefault(f"{when.hour:02d}", [])
        if app not in hour:
            hour.append(app)
        self.usage["exes"][app] = exe

    # ------------------------------------------------------------- analysis
    def habit_for(self, when: dt.datetime) -> str | None:
        """An app used at this hour on >= 3 distinct days in the last 14 (not today)."""
        today = when.date()
        counts: dict[str, int] = {}
        for back in range(1, 15):
            d = (today - dt.timedelta(days=back)).isoformat()
            hours = self.usage["days"].get(d, {})
            seen = set()
            for h in (when.hour - 1, when.hour):  # a little slack around the hour
                seen.update(hours.get(f"{h % 24:02d}", []))
            for app in seen:
                counts[app] = counts.get(app, 0) + 1
        best = max(counts.items(), key=lambda kv: kv[1], default=(None, 0))
        return best[0] if best[1] >= 3 else None

    def running(self, app: str) -> bool:
        exe = self.usage["exes"].get(app, "").lower()
        if not exe:
            return False
        return any((p.info["name"] or "").lower() == exe for p in psutil.process_iter(["name"]))

    # ----------------------------------------------------------------- loop
    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="suggestions").start()

    def _quiet(self) -> bool:
        a = self.a
        return (a.away or a.standby or a.mode != "normal" or a.hud.state not in ("idle",)
                or not self.cfg.get("suggestions_enabled", True))

    def _loop(self):
        tick = 0
        while True:
            time.sleep(60)
            tick += 1
            try:
                self.observe()
                if tick % 5 == 0:
                    self.consider()
            except Exception:
                log.exception("suggestions failed")

    def observe(self, idle: float | None = None):
        from .presence import idle_seconds
        idle = idle_seconds() if idle is None else idle
        now = self.clock()
        if idle < 300:
            self.active_since = self.active_since or now
            fg = foreground_app()
            if fg:
                self.record(*fg)
                self._save()
        else:
            self.active_since = None
            self.last_break = now  # a 5-minute pause counts as a break

    def consider(self, when: dt.datetime | None = None):
        """Maybe offer one suggestion (queued to the assistant's main loop)."""
        if self._quiet():
            return
        now, when = self.clock(), (when or dt.datetime.now())
        if now - self.last_suggestion < 45 * 60:
            return
        today = when.date().isoformat()
        user = self.cfg.get("user_name", "sir")

        # Late night
        if 1 <= when.hour < 5 and self.active_since and self.suggested_today.get("night") != today:
            self.suggested_today["night"] = today
            self._offer(f"It's past {when.hour} in the morning, {user}. Shall I initiate night protocol?",
                        lambda: self.a.protocols.run("night"))
            return

        # Break
        hours = self.cfg.get("break_reminder_hours", 3)
        if self.active_since and now - max(self.active_since, self.last_break) >= hours * 3600:
            self.last_break = now
            self.last_suggestion = now
            word = {1: "an hour", 2: "two hours", 3: "three hours", 4: "four hours"}.get(int(hours), f"{hours} hours")
            self.a.say_async(f"You've been at it for {word}, {user}. Perhaps a short break?")
            return

        # Habit
        app = self.habit_for(when)
        if app and not self.running(app) and self.suggested_today.get(app) != today:
            self.suggested_today[app] = today
            name = app.replace(".exe", "")
            self._offer(f"You usually play {name} around now, {user}. Shall I launch it?",
                        lambda: self.a.toolbox.run("launch_beamng" if "beamng" in app.lower()
                                                   else "open_application",
                                                   {} if "beamng" in app.lower() else {"name": app}))

    def _offer(self, question: str, action):
        self.last_suggestion = self.clock()
        log.info("suggestion: %s", question)
        self.a.offer(question, action)
