"""Presence state machine with a simulated camera and clock (no webcam needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from PIL import Image

from jarvis.config import DEFAULTS
from jarvis.presence import PresenceMonitor

face_img = np.asarray(Image.open(sys.argv[1]).convert("RGB").resize((360, 360)))
face = np.full((360, 640, 3), 40, np.uint8)
face[:, 140:500] = face_img
empty = np.full((360, 640, 3), 40, np.uint8)


class FakeCam:
    frame = face
    def open(self): return True
    def read_rgb(self): return self.frame
    def close(self): pass


t = [0.0]
events, locks = [], []
state = {"locked": False, "idle": 0.0}
cam = FakeCam()
m = PresenceMonitor(dict(DEFAULTS, presence_away_sec=60, presence_lock_sec=180), on_away=lambda: events.append(("away", t[0])),
                    on_return=lambda s: events.append(("back", t[0], round(s))),
                    camera=cam, clock=lambda: t[0],
                    locker=lambda: (locks.append(t[0]), state.update(locked=True)),
                    is_locked=lambda: state["locked"], idle=lambda: state["idle"])


def run(seconds, frame, idle_grows=True):
    cam.frame = frame
    end = t[0] + seconds
    while t[0] < end:
        t[0] += 1.5
        if idle_grows:
            state["idle"] += 1.5
        m.step()


results = []
def check(name, cond, info=""):
    results.append(cond); print(f"[{'PASS' if cond else 'FAIL'}] {name} {info}")

run(30, face)
check("present while a face is visible", m.present and not events)
run(45, empty)
check("no 'away' after 45 s (threshold 60 s)", m.present)
run(30, empty)
check("'away' after 60 s without a face", events and events[-1][0] == "away", events)
state["idle"] = 0
run(200, empty, idle_grows=False)
check("doesn't lock while you're still using keyboard/mouse", not locks)
run(200, empty)
check("locks after 3 min away and idle", len(locks) == 1, locks)
run(10, face)
check("no 'welcome back' while the PC is still locked", events[-1][0] == "away")
state["locked"] = False
run(4, face)
check("'welcome back' once unlocked, face seen twice", events[-1][0] == "back", events[-1])
cam.frame = empty; m.step(); cam.frame = face; m.step()
check("single missed frame doesn't trigger away", m.present and events[-1][0] == "back")
print(f"\n{sum(results)}/{len(results)} passed")
