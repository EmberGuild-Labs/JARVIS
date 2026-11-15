"""Build the speaker 'cohort': embeddings of many other voices (neural TTS voices).

The speaker model's raw embeddings all share a big common component, so any two voices
score ~0.6. Subtracting the cohort mean (standard WeSpeaker practice) fixes that, and the
cohort also tells enrollment how similar *other* people get to your voice print, which is
how the accept threshold is calibrated.   python -m jarvis.make_cohort
"""
import asyncio
import sys
import time

import numpy as np

from .config import MODELS_DIR

COHORT_PATH = MODELS_DIR / "speaker_cohort.npy"
TEXTS = ["Hey Jarvis, what's the weather like in London today?",
         "Could you open my email and turn the volume down a little, please?"]


async def _voices():
    import edge_tts
    vs = await edge_tts.list_voices()
    return [v["ShortName"] for v in vs if v["Locale"].startswith("en-")]


async def _synth(voice, text):
    import edge_tts
    b = b""
    async for c in edge_tts.Communicate(text, voice).stream():
        if c["type"] == "audio":
            b += c["data"]
    return b


def build(verifier=None, log=print) -> np.ndarray:
    import miniaudio
    from .speech import SAMPLE_RATE, SpeakerVerifier
    v = verifier or SpeakerVerifier(0.55)
    voices = asyncio.run(_voices())
    out = []
    for name in voices:
        for text in TEXTS:
            for attempt in range(2):
                try:
                    raw = asyncio.run(_synth(name, text))
                    d = miniaudio.decode(raw, output_format=miniaudio.SampleFormat.SIGNED16,
                                         nchannels=1, sample_rate=SAMPLE_RATE)
                    out.append(v.embed(np.frombuffer(d.samples, dtype=np.int16)))
                    break
                except Exception:
                    time.sleep(0.5)
    M = np.stack(out).astype(np.float32)
    COHORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.save(COHORT_PATH, M)
    log(f"cohort: {len(M)} samples from {len(voices)} voices -> {COHORT_PATH}")
    return M


if __name__ == "__main__":
    build()
    sys.exit(0)
