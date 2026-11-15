"""Live: streamed reply timing, barge-in interrupt, and recovery (Claude Code backend)."""
import logging
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.WARNING)

from jarvis.brain_code import CodeBrain
from jarvis.config import DEFAULTS
from jarvis.tools import ToolBox


class FakeHUD:
    state = "idle"

    def __getattr__(self, n):
        return lambda *a, **k: None


cfg = dict(DEFAULTS, code_dir=sys.argv[1])
brain = CodeBrain(cfg, ToolBox(lambda q: False, FakeHUD(), print, cfg))

# 1) streaming: time to first text vs. full reply
first, chunks = [None], []
t0 = time.time()


def on_text(c):
    if c:
        if first[0] is None:
            first[0] = time.time() - t0
        chunks.append(c)


reply = brain.ask("Explain in about five sentences how a turbocharger works.", on_text=on_text)
total = time.time() - t0
print(f"STREAM: first text after {first[0]:.1f}s, full reply after {total:.1f}s, "
      f"{len(chunks)} chunks, streamed={brain.streamed}")
print("   reply:", reply[:160], "...")

# 2) interrupt: cut a long answer off right after it starts talking
got, t0 = [], time.time()


def on_text2(c):
    if c:
        got.append(c)
        if len(got) == 3:
            threading.Thread(target=brain.interrupt).start()


reply2 = brain.ask("Tell me a long, detailed history of the automobile, at least twenty sentences.",
                   on_text=on_text2)
print(f"INTERRUPT: returned after {time.time() - t0:.1f}s, reply={reply2!r}, "
      f"chunks before stop={len(got)}")

# 3) recovery: next request works normally, knows it was interrupted
t0 = time.time()
reply3 = brain.ask("(interrupted) Never mind that. What's 12 squared?")
print(f"RECOVERY: {reply3!r} ({time.time() - t0:.1f}s)")
brain.reset()
