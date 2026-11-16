"""Exercise the HUD and the non-destructive tools without voice or API."""
import inspect
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic.resources.beta.messages import Messages as BetaMessages

from jarvis.config import DEFAULTS
from jarvis.hud import HUD
from jarvis.tools import RISKY, ToolBox

print("fallbacks param:", "fallbacks" in inspect.signature(BetaMessages.create).parameters)
assert RISKY.search("Remove-Item foo") and not RISKY.search("Get-ChildItem C:\\")
assert RISKY.search("sudo rm -rf /") and RISKY.search("killall Finder") and not RISKY.search("ls -la ~/Documents")

cfg = dict(DEFAULTS)
hud = HUD(cfg)
tb = ToolBox(lambda q: False, hud, print, cfg)


def script():
    time.sleep(1)
    print(tb.run("system_status", {})[0])
    print("windows:", len(tb.run("list_windows", {})[0].splitlines()))
    shot, err = tb.run("take_screenshot", {"monitor": 1})
    print("screenshot:", shot[0]["text"] if not err else shot)
    from jarvis import osutil
    risky, safe = (("rm -rf /tmp/nope", "date +%Y") if osutil.IS_MAC
                   else ("Remove-Item C:\\nope", "(Get-Date).Year"))
    print(tb.run("run_shell", {"command": risky, "reason": "test"})[0])
    print(tb.run("run_shell", {"command": safe, "reason": "test"})[0])
    hud.set_state("listening"); hud.set_level(0.6); time.sleep(1)
    hud.heard("What's using all my RAM?")
    hud.set_state("thinking"); hud.status("SYSTEM STATUS"); time.sleep(1.5)
    hud.panel("System status", tb.run("system_status", {})[0].splitlines(), 10)
    hud.say("Your browser is the heaviest at the moment, sir, using about two gigabytes.")
    hud.set_state("speaking"); time.sleep(2.5)
    out = Path(__file__).parent / "hud_preview.png"
    import mss, mss.tools
    with mss.MSS() as s:
        m = s.monitors[1]
        g = s.grab({"left": m["left"] + m["width"] - 480, "top": m["top"], "width": 480, "height": 680})
        mss.tools.to_png(g.rgb, g.size, output=str(out))
    print("preview saved", out)
    hud.set_state("idle"); time.sleep(1)
    hud.quit()


threading.Thread(target=script, daemon=True).start()
hud.run()
print("hud closed cleanly")
