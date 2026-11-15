"""Scan the screen for faces + show a sample annotation and the corner brackets."""
import sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jarvis.config import DEFAULTS
from jarvis.hud import HUD
from jarvis.tools import ToolBox
cfg = dict(DEFAULTS); hud = HUD(cfg); tb = ToolBox(lambda q: False, hud, print, cfg)
def script():
    time.sleep(1)
    hud.set_state("thinking")
    print(tb.run("scan_faces", {"delay_seconds": 0}))
    tb.run("take_screenshot", {})
    print(tb.run("annotate_screen", {"marks": [{"x": 40, "y": 40, "w": 200, "h": 60, "label": "Sample annotation"}], "seconds": 8}))
    time.sleep(1.6)
    import mss, mss.tools
    with mss.MSS() as s:
        g = s.grab(s.monitors[1]); mss.tools.to_png(g.rgb, g.size, output=str(Path(__file__).parent / "face_preview.png"))
    print("saved"); hud.set_state("idle"); time.sleep(0.5); hud.quit()
threading.Thread(target=script, daemon=True).start(); hud.run()
