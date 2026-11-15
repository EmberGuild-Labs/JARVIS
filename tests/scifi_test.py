"""Face ID + sentry, protocols (dry run), voice waveform, proactive suggestions."""
import datetime as dt
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from PIL import Image

results = []


def check(name, cond, info=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {info}")


tmp = Path(tempfile.mkdtemp())
import jarvis.config as config
config.DATA_DIR = tmp
import jarvis.face_id as fid
fid.FACEPRINT_PATH = tmp / "faceprint.npy"
from jarvis.config import DEFAULTS
from jarvis.vision import detect_faces_raw

img_dir = Path(sys.argv[1])


def scene(path, size=300):
    face = np.asarray(Image.open(path).convert("RGB"))
    rows = detect_faces_raw(face)
    x, y, w, h = (int(v) for v in max(rows, key=lambda r: r[2] * r[3])[:4])
    pad = int(w * 0.6)
    crop = face[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
    crop = np.asarray(Image.fromarray(crop).resize((size, size)))
    frame = np.full((360, 640, 3), 50, np.uint8)
    frame[30:30 + size, 170:170 + size] = crop
    return frame


me, other = scene(img_dir / "sample.jpg"), scene(img_dir / "other.jpg")
blank = np.full((360, 640, 3), 50, np.uint8)

# ------------------------------------------------------------------- face id
frames = [me, me[:, ::-1].copy(), np.clip(me * 0.85, 0, 255).astype(np.uint8)]
calls = iter(frames * 10)
ok = fid.enroll_from_camera(lambda: next(calls), say=lambda t: None, samples=8, timeout=20)
check("face enrollment from camera frames", ok and fid.FACEPRINT_PATH.exists())
f = fid.FaceID()
r_me = max(detect_faces_raw(me), key=lambda r: r[2])
r_other = max(detect_faces_raw(other), key=lambda r: r[2])
own, s1 = f.is_owner(me, r_me)
own2, s2 = f.is_owner(other, r_other)
check("recognises the owner", own, f"(score {s1:.2f})")
check("a different person is 'not the owner'", not own2, f"(score {s2:.2f})")

# ------------------------------------------------------------------- sentry
from jarvis.presence import PresenceMonitor


class Cam:
    frame = me
    def open(self): return True
    def read_rgb(self): return self.frame
    def close(self): pass


t = [0.0]
st = {"locked": False}
ev, intr = [], []
cam = Cam()
pm = PresenceMonitor(dict(DEFAULTS), lambda: ev.append("away"), lambda s: ev.append("back"),
                     camera=cam, clock=lambda: t[0], locker=lambda: st.update(locked=True),
                     is_locked=lambda: st["locked"], idle=lambda: 0.0, face_id=f,
                     on_intruder=lambda p, w: intr.append(p))


def run(sec, frame):
    cam.frame = frame
    end = t[0] + sec
    while t[0] < end:
        t[0] += 1.5
        pm.step()


run(10, me)
run(70, blank)
check("away when the owner leaves", ev == ["away"], ev)
run(6, other)
check("stranger while away -> sentry: photo + lock", intr and st["locked"] and intr[0] and intr[0].exists(), intr)
check("stranger doesn't get 'welcome back'", ev == ["away"])
st["locked"] = False
run(30, other)
check("sentry cooldown (no repeat photos)", len(intr) == 1)
check("stranger still there -> locked again", st["locked"])
st["locked"] = False   # owner unlocks
run(4, me)
check("owner returns -> welcome back", ev[-1] == "back")
run(10, other)
check("photo cooldown holds on repeat visits", len(intr) == 1)

# --------------------------------------------------------------- protocols
import jarvis.protocols as pr
pr.PROTOCOLS_PATH = tmp / "protocols.json"
pr.subprocess = types.SimpleNamespace(run=lambda *a, **k: calls_log.append(("powercfg", a[0][-1])),
                                      CREATE_NO_WINDOW=0)
import pyautogui
calls_log = []
pyautogui.hotkey = lambda *k: calls_log.append(("hotkey", k))
fake = types.SimpleNamespace(
    say=lambda x: calls_log.append(("say", x)),
    set_mode=lambda m: calls_log.append(("mode", m)),
    hud=types.SimpleNamespace(status=lambda *a: None, panel=lambda *a, **k: calls_log.append(("panel",))),
    toolbox=types.SimpleNamespace(run=lambda n, a: calls_log.append(("tool", n))))
runner = pr.ProtocolRunner(fake)
out = runner.run("gaming")
kinds = [c[0] if c[0] != "tool" else c[1] for c in calls_log]
check("gaming protocol runs every step in order",
      kinds == ["say", "panel", "powercfg", "hotkey", "launch_beamng", "mode", "say"], kinds)
check("power plan = high performance", ("powercfg", "SCHEME_MIN") in calls_log)
m = pr.RUN_RE.match("Initiate gaming protocol.")
check("voice phrase 'Initiate gaming protocol' recognised", m and pr.find(m.group(1)) == "gaming")
check("'stand down' recognised", bool(pr.END_RE.match("Stand down.")))
check("unknown protocol handled", "no banana protocol" in runner.run("banana").lower())

# ------------------------------------------------------- modes (focus holds)
import queue
import jarvis.assistant as A
a = A.Assistant.__new__(A.Assistant)
a.cfg, a.standby, a.away, a.away_log, a.focus_log = dict(DEFAULTS), False, False, [], []
a.mode, a._async_q = "normal", queue.Queue()
a.hud = types.SimpleNamespace(set_visible=lambda v: calls_log.append(("visible", v)),
                              panel=lambda *x, **k: None, status=lambda *x: None)
a.set_mode("focus")
a.say_async("Sir, download finished.")
check("focus mode holds announcements", a._async_q.empty() and a.focus_log)
a.set_mode("normal")
check("ending focus delivers them", a._async_q.qsize() == 2)
a.set_mode("game")
check("game mode hides the HUD", ("visible", False) in calls_log)
spoken = []
a.say_async = lambda x: spoken.append(x)
a.alert("ram", "Memory", "Memory is high.", [])
a.alert("gpu_temp", "GPU", "GPU is hot.", [])
check("game mode: only urgent alerts", len(spoken) == 1 and "GPU" in spoken[0], spoken)

# ------------------------------------------------------------ waveform draw
from jarvis.hud import HUD
hud = HUD(dict(DEFAULTS))
sr = 24000
tone = (0.3 * np.sin(2 * np.pi * 180 * np.arange(sr) / sr) * np.abs(np.sin(np.arange(sr) / 900))).astype(np.float32)
pos = [0]
hud.voice_source = lambda: tone[pos[0] % (sr - 2000):pos[0] % (sr - 2000) + 1536]
errs = []


def drive():
    try:
        time.sleep(0.5)
        hud.set_state("speaking")
        for _ in range(30):
            pos[0] += 800
            time.sleep(0.033)
        n = len(hud.c.find_all())
        check("waveform bars drawn while speaking", n > 60, f"({n} canvas items)")
    except Exception as e:
        errs.append(repr(e))
    finally:
        hud.quit()


threading.Thread(target=drive, daemon=True).start()
hud.run()
check("HUD ran without errors", not errs, errs)

# -------------------------------------------------------------- suggestions
import jarvis.suggestions as sg
sg.USAGE_PATH = tmp / "usage.json"
said, offers = [], []
fa = types.SimpleNamespace(cfg=dict(DEFAULTS), away=False, standby=False, mode="normal",
                           hud=types.SimpleNamespace(state="idle"), say_async=said.append,
                           offer=lambda q, act: offers.append(q), protocols=None, toolbox=None)
clock = [1_000_000.0]
eng = sg.SuggestionEngine(fa, clock=lambda: clock[0])
now = dt.datetime(2026, 11, 20, 19, 10)
for back in (1, 2, 4, 6):
    eng.record("BeamNG.drive", "beamng.drive.x64.exe", now - dt.timedelta(days=back))
check("habit learned (3+ days at this hour)", eng.habit_for(now) == "BeamNG.drive")
check("no habit at a different hour", eng.habit_for(now.replace(hour=9)) is None)
eng.running = lambda app: False
eng.active_since = None
eng.consider(now)
check("offers to launch at the usual time", offers and "BeamNG" in offers[-1], offers)
eng.consider(now)
check("rate-limited (no second nudge)", len(offers) == 1)
clock[0] += 46 * 60
eng.active_since = clock[0] - 3.2 * 3600
eng.last_break = 0
eng.consider(now.replace(hour=20))
check("break reminder after 3 hours", said and "three hours" in said[-1], said)
fa.mode = "game"
clock[0] += 46 * 60
n = len(said) + len(offers)
eng.consider(now.replace(hour=2))
check("quiet in game mode", len(said) + len(offers) == n)

print(f"\n{sum(results)}/{len(results)} passed")
