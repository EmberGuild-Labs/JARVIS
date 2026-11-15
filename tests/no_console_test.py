"""Run under pythonw (like JARVIS): make a real request and count console windows
that appear meanwhile. Writes the result to tests/no_console_result.txt."""
import ctypes
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
OUT = Path(__file__).with_name("no_console_result.txt")
user32 = ctypes.windll.user32
CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow"}


def visible_consoles() -> set[int]:
    found = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value in CONSOLE_CLASSES and user32.IsWindowVisible(hwnd):
            found.add(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found


try:
    import logging
    logging.basicConfig(level=logging.WARNING)
    from jarvis.brain_code import CodeBrain
    from jarvis.config import DEFAULTS
    from jarvis.tools import ToolBox

    class FakeHUD:
        state = "idle"

        def __getattr__(self, n):
            return lambda *a, **k: None

    before = visible_consoles()
    seen, done = set(), threading.Event()

    def watch():
        while not done.is_set():
            seen.update(visible_consoles() - before)
            time.sleep(0.05)

    threading.Thread(target=watch, daemon=True).start()
    cfg = dict(DEFAULTS, code_dir=sys.argv[1])
    brain = CodeBrain(cfg, ToolBox(lambda q: False, FakeHUD(), print, cfg))
    r1 = brain.ask("What's 2 plus 2?")
    r2 = brain.ask("Run the PowerShell command Get-Date and tell me the year.")
    done.set()
    brain.reset()
    OUT.write_text(f"new console windows: {len(seen)}\nr1: {r1}\nr2: {r2}\n", encoding="utf-8")
except Exception as e:
    import traceback
    OUT.write_text("ERROR\n" + traceback.format_exc(), encoding="utf-8")
