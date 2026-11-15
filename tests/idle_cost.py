"""Measure CPU/RAM of the idle listening loop (mic + wake word) for 20 s."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import psutil
from jarvis.audio import Microphone
from jarvis.speech import WakeWord, SpeakerVerifier
p = psutil.Process(); p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
w = WakeWord(0.5); SpeakerVerifier(0.55); m = Microphone(); m.start()
p.cpu_percent(); t = time.time(); hits = 0
while time.time() - t < 20:
    f = m.read()
    if f is not None:
        m.update_noise(f); hits += w.detect(f)
cpu = p.cpu_percent() / psutil.cpu_count()
print(f"idle CPU {cpu:.2f}% of total, RAM {p.memory_info().rss/2**20:.0f} MB, noise floor {m.noise_floor:.0f}, wake hits {hits}")
