"""Headless checks: overlay/corner drawing, tray icon, topic transcript. Works while locked."""
import sys, threading, time, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jarvis.config import DEFAULTS
from jarvis.hud import HUD
from jarvis.brain import Brain
from jarvis.tools import ToolBox, TOOL_DEFS

cfg = dict(DEFAULTS); hud = HUD(cfg); errors = []
tb = ToolBox(lambda q: False, hud, print, cfg)

# --- topic transcript (no API call)
b = Brain.__new__(Brain); b.cfg = cfg
b.messages = [{"role": "user", "content": "[Sat 12:00 | active window: x] help me fix this python error"},
              {"role": "assistant", "content": [types.SimpleNamespace(type="text", text="Sure, it's a typo on line 3.")]}]
print(b._transcript())
cfg_off = dict(cfg, topic_detection=False); b.cfg = cfg_off
assert b._is_new_topic("what's the weather") is False
print("tools:", [t["name"] for t in TOOL_DEFS])

# --- tray
import jarvis.__main__ as m
fake = types.SimpleNamespace(cfg=cfg, trigger=threading.Event(), running=True,
                             brain=types.SimpleNamespace(reset=lambda: None), toolbox=tb)
icon = m.make_tray(hud, fake)

def script():
    try:
        time.sleep(0.8)
        hud.set_state("listening"); time.sleep(0.6)
        assert hud.layer_shown, "corners should show while active"
        hud.mark_faces([(300, 300, 140, 200), (1250, 745, 37, 50)], 2)
        hud.annotate([{"x": 40, "y": 40, "w": 200, "h": 60, "label": "Sample"}], 2); time.sleep(0.2)
        hud.mark_faces([(300, 300, 140, 200)], 2); time.sleep(1.2)
        hud.set_state("idle"); time.sleep(2.5)
        assert not hud.layer_shown, "layer should hide when idle and marks expire"
        cfg["screen_corners"] = "always"; hud.set_state("idle"); time.sleep(1)
        assert hud.layer_shown, "always mode"
        cfg["screen_corners"] = "off"; time.sleep(1)
        assert not hud.layer_shown
        print("tray icon title:", icon.title)
    except Exception as e:
        errors.append(repr(e))
    finally:
        icon.stop(); hud.quit()

threading.Thread(target=script, daemon=True).start()
hud.run()
print("ERRORS:", errors or "none")
