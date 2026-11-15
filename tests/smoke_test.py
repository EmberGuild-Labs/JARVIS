"""Quick component check: python tests/smoke_test.py  (no mic input or speech needed)."""
import inspect
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import anthropic
from anthropic.resources.beta.messages import Messages as BetaMessages

from jarvis.audio import Player
from jarvis.speech import SpeakerVerifier, SpeechToText, TextToSpeech, WakeWord

print("sdk", anthropic.__version__, "fallbacks param:",
      "fallbacks" in inspect.signature(BetaMessages.create).parameters)

t = time.time(); w = WakeWord(0.5)
print("wake word ok", w.detect(np.zeros(1280, np.int16)), f"{time.time() - t:.1f}s")

t = time.time(); v = SpeakerVerifier(0.55)
e = v.embed((np.random.randn(32000) * 1000).astype(np.int16))
print("speaker model ok", e.shape, f"{time.time() - t:.1f}s")

t = time.time(); s = SpeechToText("base.en", 15)
print("stt ok", repr(s.transcribe(np.zeros(16000, np.int16))), f"{time.time() - t:.1f}s")

tts = TextToSpeech("en-GB-RyanNeural", "+8%", Player())
a, sr = tts._synth_edge("Systems online.")
print("tts ok", f"{len(a) / sr:.1f}s of audio")

import sounddevice as sd
print("mic:", sd.query_devices(kind="input")["name"])
