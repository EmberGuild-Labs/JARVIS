"""Standby: HUD dims, corners/alerts quiet, wake restores. No audio, no Claude calls."""
import sys, threading, time, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jarvis.config import DEFAULTS
from jarvis.hud import HUD
import jarvis.assistant as A

cfg = dict(DEFAULTS, screen_corners="always")
hud = HUD(cfg)
a = A.Assistant.__new__(A.Assistant)          # skip mic/model loading
a.cfg, a.hud, a.standby = cfg, hud, False
spoken = []
a.say = lambda t: spoken.append(t)
a.say_async = lambda t: spoken.append("async:" + t)
errors = []

def drive():
    try:
        time.sleep(0.8)
        assert hud.layer_shown, "always-mode corners visible before standby"
        a.set_standby(True)
        time.sleep(2.2)   # the shutdown sweep runs first
        assert hud.state == "standby" and not hud.layer_shown, (hud.state, hud.layer_shown)
        a.alert("gpu_temp", "GPU", "Your graphics card is hot.", ["GPU 90 C"])
        assert not any(s.startswith("async:") for s in spoken), "alerts silent in standby"
        a.set_standby(False)
        time.sleep(0.6)
        assert hud.state == "idle" and hud.layer_shown, (hud.state, hud.layer_shown)
        a.alert("gpu_temp", "GPU", "Your graphics card is hot.", ["GPU 90 C"])
        assert any(s.startswith("async:") for s in spoken), "alerts speak again"
    except Exception as e:
        errors.append(repr(e))
    finally:
        hud.quit()

threading.Thread(target=drive, daemon=True).start()
hud.run()
print("spoken:", spoken)
print("ERRORS:", errors or "none")
