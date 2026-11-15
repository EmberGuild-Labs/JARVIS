"""20-second auto-lock, instant stranger lock, typing guard, guest mode (simulated camera)."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from PIL import Image

tmp = Path(tempfile.mkdtemp())
import jarvis.config as config
config.DATA_DIR = tmp
import jarvis.face_id as fid
fid.FACEPRINT_PATH = tmp / "faceprint.npy"
from jarvis.config import DEFAULTS
from jarvis.presence import PresenceMonitor
from jarvis.vision import detect_faces_raw

img_dir = Path(sys.argv[1])


def scene(path, size=300):
    face = np.asarray(Image.open(path).convert("RGB"))
    x, y, w, h = (int(v) for v in max(detect_faces_raw(face), key=lambda r: r[2] * r[3])[:4])
    pad = int(w * 0.6)
    crop = face[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
    frame = np.full((360, 640, 3), 50, np.uint8)
    frame[30:30 + size, 170:170 + size] = np.asarray(Image.fromarray(crop).resize((size, size)))
    return frame


me, other, blank = scene(img_dir / "sample.jpg"), scene(img_dir / "other.jpg"), np.full((360, 640, 3), 50, np.uint8)
it = iter([me] * 40)
fid.enroll_from_camera(lambda: next(it), say=lambda t: None, samples=8, timeout=20)

results = []
def check(name, cond, info=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name} {info}")


class Cam:
    frame = me
    def open(self): return True
    def read_rgb(self): return self.frame
    def close(self): pass


def make(cfg=None):
    st = {"t": 0.0, "locked": False, "locks": [], "idle": 0.0, "ev": [], "photos": []}
    cam = Cam()

    def lock():
        st["locked"] = True
        st["locks"].append(st["t"])
    m = PresenceMonitor(dict(DEFAULTS, **(cfg or {})), lambda: st["ev"].append("away"),
                        lambda s: st["ev"].append("back"), camera=cam, clock=lambda: st["t"],
                        locker=lock, is_locked=lambda: st["locked"], idle=lambda: st["idle"],
                        face_id=fid.FaceID(), on_intruder=lambda p, w: st["photos"].append(p))

    def run(sec, frame, typing=False):
        cam.frame = frame
        end = st["t"] + sec
        while st["t"] < end:
            st["t"] += 1.5
            st["idle"] = 0.0 if typing else st["idle"] + 1.5
            m.step()
    return m, st, run


# 1) nobody in view -> lock at ~20 s (opt-in now)
m, st, run = make({'presence_lock': True})
run(6, me)
run(15, blank)
check("no lock at 15 s", not st["locks"])
run(9, blank)
check("locks ~20 s after you leave", st["locks"] and st["locks"][0] - 6 <= 24, st["locks"])

# 2) typing but face out of shot -> no lock
m, st, run = make({'presence_lock': True})
run(6, me)
run(60, blank, typing=True)
check("no lock while keyboard/mouse is in use", not st["locks"])
run(25, blank)
check("locks once input stops too", len(st["locks"]) == 1)

# 0) default: nobody in view never locks
m, st, run = make()
run(6, me)
run(120, blank)
check("default: empty camera never locks", not st["locks"])

# 3) a different face right after you were in view -> lock after ~9 s
m, st, run = make()
run(6, me)
t0 = st["t"]
run(6, other)
check("no lock in the first 6 s (you were just here)", not st["locks"])
run(4.5, other)
check("stranger right after you -> lock by ~9-10 s", st["locks"] and st["locks"][0] - t0 <= 10.5, st["locks"])
check("sentry photo saved", st["photos"] and st["photos"][0].exists())

# 3b) stranger after you've been gone a while -> lock in ~4.5 s
m, st, run = make()
run(6, me)
run(30, blank)
t0 = st["t"]
run(6, other)
check("stranger after you left -> lock within ~4.5 s", st["locks"] and st["locks"][0] - t0 <= 4.5, st["locks"])

# 3c) YOU looking down / tilting your head must never lock
def squash(img, k):
    h, w = img.shape[:2]
    small = np.asarray(Image.fromarray(img).resize((w, int(h * k))))
    out = np.full_like(img, 50); out[h - small.shape[0]:] = small
    return out
def rotate(img, deg):
    return np.asarray(Image.fromarray(img).rotate(deg, fillcolor=(50, 50, 50)))
for label, frame in [("looking down (squashed)", squash(me, 0.55)), ("head tilted 35deg", rotate(me, 35)),
                     ("head tilted -35deg", rotate(me, -35)), ("looking down more", squash(me, 0.4))]:
    m, st, run = make()
    run(6, me)
    run(60, frame)
    check(f"you {label}: no lock", not st["locks"])

# 4) you plus a friend behind you -> no lock
m, st, run = make()
both = me.copy()
small = np.asarray(Image.fromarray(other).resize((320, 180)))
both[180:360, 0:320] = small
run(30, both)
check("no lock when you're in view (even with someone else)", not st["locks"])

# 5) guest mode
import time as _t
m, st, run = make({"_guest_until": 1e12})
run(6, me)
run(30, other)
check("guest mode: no lock for a guest", not st["locks"])

print(f"\n{sum(results)}/{len(results)} passed")
