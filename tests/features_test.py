"""Offline checks for the new features (no audio is played, no API calls)."""
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np

results = []


def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {info}")


# ---------------------------------------------------------------- speech stream
from jarvis.speech import SpeechStream


class FakeTTS:
    def __init__(self):
        self.player = type("P", (), {"stop": lambda s: None})()
        self.played, self.synth_start = [], {}

    def _synth(self, s):
        self.synth_start[s] = time.time()
        time.sleep(0.3)  # pretend synthesis takes 300 ms
        return ("audio", s)

    def _play(self, item):
        self.played.append((item[1], time.time()))
        time.sleep(0.4)  # pretend each sentence plays for 400 ms


tts = FakeTTS()
st = SpeechStream(tts)
t0 = time.time()
for chunk in ["Right, sir. I've opened ", "BeamNG for you. The game ", "should be ready in about a minute. ",
              "Shall I spawn a car when it loads?"]:
    st.feed(chunk)
    time.sleep(0.05)
st.close()
st.wait(10)
said = [p[0] for p in tts.played]
check("stream splits into sentences in order", said == [
    "Right, sir. I've opened BeamNG for you.", "The game should be ready in about a minute.",
    "Shall I spawn a car when it loads?"], said)
first = tts.played[0][1] - t0
check("first sentence starts before the reply is finished", first < 0.6, f"({first:.2f}s)")
s2 = tts.synth_start.get(said[1], 99) if len(said) > 1 else 99
check("next sentence synthesizes while the first plays", s2 < tts.played[0][1] + 0.4)

tts2 = FakeTTS()
st2 = SpeechStream(tts2)
st2.feed("One sentence here to start. Two sentences now follow it. Three of them in total here. ")
time.sleep(0.5)
st2.stop()
st2.close()
check("stop() cuts speech off", st2.wait(2) is False and len(tts2.played) <= 1, f"played {len(tts2.played)}")

# ----------------------------------------------------------- voiceprint adaptation
import jarvis.speech as sp

tmp = Path(tempfile.mkdtemp())
sp.VOICEPRINT_PATH = tmp / "voiceprint.npy"
sp.BASE_VOICEPRINT_PATH = tmp / "voiceprint_base.npy"
v = sp.SpeakerVerifier.__new__(sp.SpeakerVerifier)
v.threshold = 0.55
v.mu = None   # no cohort calibration in this test
rng = np.random.default_rng(0)
me = rng.normal(size=256); me /= np.linalg.norm(me)
v.save_voiceprint([me])
near = me + 0.25 * rng.normal(size=256) / 16; near /= np.linalg.norm(near)
other = rng.normal(size=256); other /= np.linalg.norm(other)
v._last = (near, sp.SAMPLE_RATE * 3)
check("adapts on a confident match", v.adapt(float(near @ v.voiceprint)))
v._last = (near, sp.SAMPLE_RATE * 1)
check("ignores short samples", not v.adapt(0.95))
v._last = (other, sp.SAMPLE_RATE * 3)
check("refuses samples far from enrollment", not v.adapt(0.95))
for _ in range(200):
    v._last = (near, sp.SAMPLE_RATE * 3)
    v.adapt(0.95)
check("stays anchored to the enrolled voice", float(v.voiceprint @ me) > 0.85, f"({float(v.voiceprint @ me):.3f})")
check("persists updates", sp.VOICEPRINT_PATH.exists() and sp.BASE_VOICEPRINT_PATH.exists())

# --------------------------------------------------------------------- alerts
from jarvis.alerts import SystemMonitor
from jarvis.config import DEFAULTS

got = []
cfg = dict(DEFAULTS, alert_gpu_temp=1, alert_disk_free_gb=10**6, alert_process_ram_share=0.0001)
m = SystemMonitor(lambda *a: got.append(a), cfg)
m.downloads = tmp
m.seen = set()
(tmp / "movie.mp4").write_text("x")
(tmp / "big.iso.crdownload").write_text("x")
t = time.time(); m.check(); dt_ms = (time.time() - t) * 1000
kinds = [g[0] for g in got]
check("GPU temperature alert", "gpu_temp" in kinds, got and [g[2] for g in got if g[0] == "gpu_temp"])
check("disk space alert", "disk" in kinds)
check("memory hog alert", any(k.startswith("hog:") for k in kinds))
check("finished download alert (ignores partial files)",
      any(k == "dl:movie.mp4" for k in kinds) and not any("crdownload" in k for k in kinds))
pass  # (removed: test dir shared with voiceprint test)
got.clear(); m.check()
check("cooldowns stop repeats", not any(k in ("gpu_temp", "disk") for k in [g[0] for g in got]))
check("monitor pass is cheap", dt_ms < 1500, f"({dt_ms:.0f} ms incl. nvidia-smi)")

# ---------------------------------------------------------------------- media
from jarvis.media import now_playing

t = time.time(); info = now_playing(); ms = (time.time() - t) * 1000
check("media session query works", info is None or "title" in info, f"{info} ({ms:.0f} ms)")

# ------------------------------------------------------------- beamng helpers
from jarvis.brain_code import beamng_running
from jarvis.tools import find_beamng

check("finds BeamNG.drive.exe", find_beamng() is not None, str(find_beamng()))
check("BeamNG MCP probe is fast when the game is closed",
      (lambda t: (beamng_running(DEFAULTS), time.time() - t)[1] < 0.5)(time.time()))

# ------------------------------------------------------------------ notes tool
import jarvis.tools as tl

tl.NOTES_PATH = tmp / "notes.md"
tb = tl.ToolBox(lambda q: False, type("H", (), {"panel": lambda *a, **k: None})(), print, DEFAULTS)
tb.run("take_note", {"text": "buy coffee"})
tb.run("take_note", {"text": "call Sam about the car"})
out, err = tb.run("read_notes", {"count": 5})
check("notes save and read back", "buy coffee" in out and "call Sam" in out and not err)
tb.run("start_dictation", {})
check("dictation flag set", tb.dictation_requested)

# --------------------------------------------------------------- HUD boot draw
from jarvis.hud import HUD

hud = HUD(dict(DEFAULTS))
errors = []


def drive():
    try:
        time.sleep(0.5)
        hud.boot([("Audio input: test", True), ("Voice authentication", False), ("Neural link", True)])
        time.sleep(2.2)
        check("HUD boot sequence draws", hud.state == "boot" and hud.layer_shown)
        hud.set_state("idle")
        time.sleep(0.8)
    except Exception as e:
        errors.append(repr(e))
    finally:
        hud.quit()


threading.Thread(target=drive, daemon=True).start()
hud.run()
check("HUD ran without errors", not errors, errors)

print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
