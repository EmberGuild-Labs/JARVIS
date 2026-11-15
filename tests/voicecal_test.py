"""Calibrated voice lock with a stand-in 'owner' (a TTS voice NOT in the cohort... re-synthesised)."""
import asyncio, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np, miniaudio
import jarvis.speech as sp
from jarvis.make_cohort import _synth
tmp = Path(tempfile.mkdtemp())
sp.VOICEPRINT_PATH = tmp / "voiceprint.npy"; sp.BASE_VOICEPRINT_PATH = tmp / "voiceprint_base.npy"; sp.CALIB_PATH = tmp / "voice_calib.json"
v = sp.SpeakerVerifier(0.55)
def audio(voice, text):
    for _ in range(3):
        try:
            d = miniaudio.decode(asyncio.run(_synth(voice, text)), output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=16000)
            return np.frombuffer(d.samples, dtype=np.int16)
        except Exception: time.sleep(1)
OWNER = "en-US-AndrewMultilingualNeural"
phr = ["Jarvis, what's on my calendar today?", "Open Chrome and search for the weather.", "Turn the volume down a bit please.",
       "Remind me to call my friend at five.", "What time is it in Tokyo right now?", "Play some music and dim the screen."]
E = [v.embed(a) for p in phr if (a := audio(OWNER, p)) is not None]
v.save_voiceprint(E); c = v.calibrate(E)
print("calibration:", {k: c[k] for k in ("threshold", "wake_threshold", "impostor_max")}, "own", c["own_scores"])
ok = []
s = v.score(audio(OWNER, "Hey Jarvis, can you check my email for me?")); ok.append(s >= v.threshold); print(f"owner new phrase {s:.2f} accept={s>=v.threshold}")
for vo in ["en-US-GuyNeural", "en-GB-RyanNeural", "en-US-JennyNeural", "en-AU-NatashaNeural"]:
    s = v.score(audio(vo, "Hey Jarvis, can you check my email for me?")); ok.append(s < v.threshold); print(f"{vo:24s} {s:.2f} reject={s<v.threshold}")
print(f"{sum(ok)}/{len(ok)} passed")
for vo in [OWNER, "en-US-GuyNeural", "en-GB-RyanNeural", "en-US-ChristopherNeural"]:
    a = audio(vo, "Hey Jarvis.")
    a = np.concatenate([np.zeros(6000, np.int16), a, np.zeros(3000, np.int16)])
    print(f"WAKE {vo:30s} {v.score(a):.2f} (wake thr {v.wake_threshold:.2f}) len {len(a)/16000:.1f}s")
