"""AI cursor: tool hooks call the HUD pointer before acting, and the overlay draws it.
pyautogui is stubbed out, so nothing is actually clicked or typed."""
import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyautogui

log = []
for fn in ("click", "write", "hotkey", "scroll", "press"):
    setattr(pyautogui, fn, (lambda name: lambda *a, **k: log.append(("act", name)))(fn))
pyautogui.position = lambda: types.SimpleNamespace(x=900, y=500)

from jarvis.config import DEFAULTS
from jarvis.hud import HUD
from jarvis.tools import ToolBox

hud = HUD(dict(DEFAULTS))
real_pointer = hud.pointer
hud.pointer = lambda *a, **k: (log.append(("ptr", a[2], a[3])), real_pointer(*a, **k))
tb = ToolBox(lambda q: False, hud, print, dict(DEFAULTS))
tb.shot_scale = 1920 / 1366
errors = []


def drive():
    try:
        time.sleep(0.6)
        tb.run("mouse_click", {"x": 683, "y": 384, "button": "left", "double": False})
        time.sleep(0.2)
        assert hud.layer_shown and hud.ptr and hud.ptr["kind"] == "click", hud.ptr
        tb.run("type_text", {"text": "hello from JARVIS"})
        tb.run("press_keys", {"keys": "ctrl+s"})
        tb.run("scroll", {"amount": -3})
        time.sleep(2.6)
        assert hud.ptr is None and not hud.layer_shown, "cursor should fade out when idle"
    except Exception as e:
        errors.append(repr(e))
    finally:
        hud.quit()


threading.Thread(target=drive, daemon=True).start()
hud.run()
order_ok = [k for k, *_ in log] == ["ptr", "act"] * 4
print("events:", log)
print("cursor shown before every action:", order_ok)
print("click mapped to screen:", tb.shot_scale, "->", round(683 * tb.shot_scale), round(384 * tb.shot_scale))
print("ERRORS:", errors or "none")
