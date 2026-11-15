"""Live: browser control, a background task, history search, monitor handling."""
import logging
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.WARNING)

import jarvis.history as hist
hist.HISTORY_PATH = Path(tempfile.mkdtemp()) / "history.jsonl"

from jarvis.brain_code import CodeBrain
from jarvis.config import DEFAULTS
from jarvis.tools import ToolBox

work = Path(sys.argv[1])


class FakeHUD:
    state = "idle"

    def __getattr__(self, n):
        return lambda *a, **k: None


cfg = dict(DEFAULTS, code_dir=str(work))
tb = ToolBox(lambda q: False, FakeHUD(), print, cfg)
brain = CodeBrain(cfg, tb)
tb.bg = brain
done = threading.Event()
finished = {}
brain.on_background_done = lambda t: (finished.update(t), done.set())
status = lambda s: print("   [status]", s)

# 1) history (offline)
hist.log_exchange("How does a turbocharger work?", "It uses exhaust gas to spin a turbine...")
hist.log_exchange("Make me a script that renames screenshots", "Done, it's in rename_shots.py.")
hist.log_exchange("What's the weather?", "Six degrees and cloudy.")
hits = hist.search("that script we made", days=7)
print("HISTORY:", [h["user"] for h in hits][:2])

# 2) monitors
mons = tb.monitors()
print(f"MONITORS: {len(mons) - 1} screen(s), desktop {mons[0]['width']}x{mons[0]['height']}")

# 3) background task, and the user keeps talking meanwhile
t0 = time.time()
r = brain.ask("In the background, write a Python script bg_hello.py that prints the first five "
              "square numbers, run it, and tell me the output when you're done.", on_status=status)
print(f"BG START ({time.time() - t0:.1f}s):", r)
t1 = time.time()
r2 = brain.ask("While that runs: what's 9 times 7?", on_status=status)
print(f"MEANWHILE ({time.time() - t1:.1f}s):", r2)

# 4) browser control
t2 = time.time()
r3 = brain.ask("Open example.com in the browser and tell me the main heading on the page.",
               on_status=status)
print(f"BROWSER ({time.time() - t2:.1f}s):", r3)

done.wait(300)
print(f"BG DONE: status={finished.get('status')} after {finished.get('finished', 0) - finished.get('started', 0):.0f}s")
print("   result:", finished.get("result"))
print("   file exists:", (work / "bg_hello.py").exists())
print("   list:", brain.list_background())
brain.reset()
