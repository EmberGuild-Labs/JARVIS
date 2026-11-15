"""End-to-end test of the Claude Code backend (uses your Claude Code login)."""
import sys, time, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
for n in ("httpx", "httpx2"): logging.getLogger(n).setLevel(logging.WARNING)
from jarvis.config import DEFAULTS
from jarvis.brain_code import CodeBrain
from jarvis.tools import ToolBox

class FakeHUD:
    state = "idle"
    def __getattr__(self, name): return lambda *a, **k: print(f"   [hud.{name}] {str(a)[:120]}")

work = Path(sys.argv[1])
cfg = dict(DEFAULTS, code_dir=str(work))
asked = []
def confirm(q): asked.append(q); print("   [confirm?]", q, "-> no"); return False
brain = CodeBrain(cfg, ToolBox(confirm, FakeHUD(), print, cfg))
status = lambda s: print("   [status]", s)
for q in ["How much RAM is my PC using right now?",
          "Write a Python script called fizz.py that prints FizzBuzz from 1 to 15, run it, and tell me the last line it printed.",
          "Now make it go to 20 instead.",
          "Delete the fizz.py file please.",
          "What's the capital of Australia?"]:
    t = time.time(); print("\nYOU:", q)
    print("JARVIS:", brain.ask(q, on_status=status), f"({time.time()-t:.1f}s)")
print("\nfiles:", [p.name for p in work.iterdir()], "| confirms asked:", len(asked))
print("fizz.py tail:", (work / "fizz.py").read_text().splitlines()[-3:] if (work / "fizz.py").exists() else "missing")
brain.reset()
