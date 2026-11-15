"""Live test of model routing + escalation on the Claude Code backend."""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
for n in ("httpx", "httpx2"):
    logging.getLogger(n).setLevel(logging.WARNING)

from jarvis.brain_code import CodeBrain
from jarvis.config import DEFAULTS
from jarvis.tools import ToolBox


class FakeHUD:
    state = "idle"

    def __getattr__(self, name):
        return lambda *a, **k: None


work = Path(sys.argv[1])
cfg = dict(DEFAULTS, code_dir=str(work))
brain = CodeBrain(cfg, ToolBox(lambda q: False, FakeHUD(), print, cfg))
status = lambda s: print("   [status]", s)

cases = [
    ("What's 17 times 23?", "simple"),
    ("Compare the pros and cons of electric versus petrol cars for a long commute, briefly.", "complex"),
    ("And which is cheaper to run over five years?", "complex"),  # follow-up keeps tier
    ("Write a Python script primes.py that prints the first 10 primes and run it.", "coding"),
    ("What time is it?", "simple"),
]
for q, expect in cases:
    t = time.time()
    print("\nYOU:", q)
    reply = brain.ask(q, on_status=status)
    print(f"JARVIS [{brain.short(brain.model)}/{brain.tier}, expected {expect}]:", reply,
          f"({time.time() - t:.1f}s)")

# Forced escalation: tell Haiku to hand off, check Opus finishes the job in the same context.
brain.reset()
print("\n--- escalation test")
brain.cfg = dict(cfg, tier_models=dict(cfg["tier_models"]))
q = ("Please call the escalate_to_opus tool right away with reason 'test handoff', "
     "and the stronger model should then tell me the square root of 144.")
continues, tier = False, "simple"
reply = brain._run(brain._handle(q, q, "simple", status), timeout=600)
print(f"JARVIS [{brain.short(brain.model)}]:", reply)
brain.reset()
