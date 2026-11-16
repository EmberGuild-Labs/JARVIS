"""'Watch my screen': glance at the screen now and then and only speak up if something's wrong.

Cheap by design:
- A local check every 5 s: a new window whose title looks like an error ("Error",
  "has stopped working", "Not Responding", "Crash", "Exception"...) triggers a look now.
- Otherwise a look every `screen_watch_interval_sec` (default 120 s), and only if the
  screen has visibly changed since the last look.
- A look = one small screenshot read by the fast model (Haiku), which answers in JSON.
  Alerts are de-duplicated so the same problem is never announced twice.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import threading
import time

import numpy as np

from . import osutil
from .config import DATA_DIR

log = logging.getLogger("jarvis")
ERR_TITLE = re.compile(r"\b(error|exception|crash(ed)?|fatal|failed|failure|not responding|"
                       r"stopped working|problem|traceback|unhandled|assert)\b", re.I)
SHOT = DATA_DIR / "screenwatch.jpg"

PROMPT = """You glance at the user's screen for JARVIS, their voice assistant, and say \
something ONLY if something is wrong that they'd want to know about right now.
Read the screenshot at {path} (all of the user's screens side by side).{focus}
A problem: an error or warning dialog, a crash report, a stack trace or exception, failed \
build/test/compile output, an app "Not Responding", a game that crashed to the desktop, a \
download or render that failed. Not a problem: normal apps, code being written, videos, \
games being played, warnings that were already there before.{new}
Answer with ONLY JSON: {{"problem": true or false, "say": "one short spoken sentence for the \
user naming what's wrong and where, or empty"}}"""


class ScreenWatcher:
    def __init__(self, assistant):
        self.a = assistant
        self.until = 0.0
        self.focus = ""
        self._thread: threading.Thread | None = None
        self._last_look = 0.0
        self._last_thumb = None
        self._said: dict[str, float] = {}
        self._last_alert = 0.0
        self._seen_titles: set[str] = set()

    @property
    def active(self) -> bool:
        return time.time() < self.until

    def start(self, minutes: int, focus: str = "") -> str:
        minutes = max(1, min(int(minutes), 8 * 60))
        self.until = time.time() + minutes * 60
        self.focus = focus.strip()
        self._seen_titles = set(self._titles())
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, daemon=True, name="screenwatch")
            self._thread.start()
        self.a.hud.panel("Screen watch", [f"Watching for {minutes} min" +
                                          (f": {self.focus}" if self.focus else ""),
                                          "I'll only speak up if something's wrong"], 10)
        return f"Watching the screen for {minutes} minutes."

    def stop(self) -> str:
        was = self.active
        self.until = 0.0
        self.a.hud.panel("Screen watch", ["Stopped"], 5)
        return "Stopped watching the screen." if was else "I wasn't watching the screen."

    # ------------------------------------------------------------------ loop
    @staticmethod
    def _titles() -> list[str]:
        try:
            from .winfront import _title, windows
            return [_title(h) for h in windows()]
        except Exception:
            return []

    @staticmethod
    def _window_text(title: str) -> str:
        """App name plus the text inside a window (message boxes and most dialogs keep it in
        child controls), so a dialog hidden behind other windows can still be read."""
        try:
            if osutil.IS_MAC:   # no child-control text without Accessibility walking: app + title only
                from .winfront import _exe, _title, find_window
                h = find_window(title)
                return f"[{_exe(h).removesuffix('.exe')}] {_title(h)}"[:600] if h else ""
            import ctypes
            from ctypes import wintypes
            from .winfront import _exe, _title, find_window
            user32 = ctypes.windll.user32
            h = find_window(title)
            if not h:
                return ""
            parts: list[str] = []
            proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def cb(child, _):
                t = _title(child).strip()
                if t and t not in parts and t.lower() not in ("ok", "cancel", "&ok", "close", "yes", "no"):
                    parts.append(t)
                return len(parts) < 20
            user32.EnumChildWindows(h, proc(cb), 0)
            return f"[{_exe(h).removesuffix('.exe')}] " + " | ".join(parts)[:600]
        except Exception:
            return ""

    def _loop(self):
        interval = float(self.a.cfg.get("screen_watch_interval_sec", 120))
        while self.active:
            time.sleep(5)
            if not self.active:
                break
            titles = self._titles()
            new = [t for t in titles if t not in self._seen_titles]
            self._seen_titles = set(titles)
            alarming = [t for t in new if ERR_TITLE.search(t)]
            due = time.time() - self._last_look >= interval
            if alarming or due:
                try:
                    self._look(alarming)
                except Exception:
                    log.exception("screen watch look failed")
        log.info("screen watch ended")
        self.a.hud.panel("Screen watch", ["Finished watching"], 6)

    def _grab(self):
        """All screens, downscaled; returns (jpeg path, small grey thumbnail)."""
        import mss
        from PIL import Image
        self.a.hud.hide_for_capture(True)
        try:
            time.sleep(0.1)
            with mss.MSS() as s:
                raw = s.grab(s.monitors[0])
        finally:
            self.a.hud.hide_for_capture(False)
        img = Image.frombytes("RGB", raw.size, raw.rgb)
        thumb = np.asarray(img.convert("L").resize((96, 54)), dtype=np.float32)
        img.thumbnail((1600, 1600))
        img.save(SHOT, "JPEG", quality=70)
        return SHOT, thumb

    def _look(self, alarming: list[str]):
        path, thumb = self._grab()
        changed = self._last_thumb is None or float(np.abs(thumb - self._last_thumb).mean()) > 3.0
        self._last_look = time.time()
        if not alarming and not changed:
            return
        self._last_thumb = thumb
        verdict = self._ask(path, alarming)
        if not verdict.get("problem") or not verdict.get("say"):
            return
        say = str(verdict["say"]).strip()
        key = re.sub(r"\W+", " ", say.lower())[:60]
        now = time.time()
        if now - self._said.get(key, 0) < 600 or now - self._last_alert < 90:
            return
        self._said[key] = self._last_alert = now
        log.info("screen watch: %s", say)
        self.a.hud.panel("Screen watch", [say], 20)
        self.a.say_async(f"{self.a.cfg['user_name'].capitalize()}, {say[0].lower() + say[1:]}"
                         if say[1:2].islower() else say)

    def _ask(self, path, alarming: list[str]) -> dict:
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
        focus = f"\nThe user asked you to watch for: {self.focus}." if self.focus else ""
        new = ""
        for t in alarming[:3]:   # it may be hidden behind other windows: give its text too
            inner = self._window_text(t)
            new += (f"\nA window just appeared (possibly behind others, so maybe not in the "
                    f"screenshot) titled \"{t[:120]}\"" + (f", from the app and containing: {inner}" if inner else "")
                    + ". Judge it from this text; name the app it came from.")
        opts = dict(model=self.a.cfg.get("screen_watch_model") or "claude-haiku-5-5",
                    allowed_tools=["Read"], max_turns=3, setting_sources=[], effort="low",
                    cwd=str(DATA_DIR), system_prompt="You check screenshots and answer in JSON only.")
        cli = shutil.which("claude")
        if cli:
            opts["cli_path"] = cli

        async def run():
            out = ""
            async for msg in query(prompt=PROMPT.format(path=str(path).replace("\\", "/"),
                                                        focus=focus, new=new),
                                   options=ClaudeAgentOptions(**opts)):
                if isinstance(msg, ResultMessage):
                    out = msg.result or ""
            return out
        text = asyncio.run(run())
        m = re.search(r"\{.*\}", text, re.S)
        try:
            return json.loads(m.group(0)) if m else {}
        except Exception:
            return {}
