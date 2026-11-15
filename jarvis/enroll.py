"""Record your voice so JARVIS only obeys you.   python -m jarvis.enroll"""
from __future__ import annotations

import sys
import time

import numpy as np

from .audio import SAMPLE_RATE, Microphone, record_utterance
from .config import load_config
from .speech import SpeakerVerifier

PHRASES = [
    "Hey Jarvis, what's the weather looking like today?",
    "Hey Jarvis, open Spotify and play something relaxing.",
    "Hey Jarvis, how much memory is my computer using right now?",
    "Hey Jarvis, remind me in ten minutes to check the oven.",
    "Hey Jarvis, take a look at my screen and tell me what you see.",
    "The quick brown fox jumps over the lazy dog, then naps in the warm afternoon sun.",
]


def main():
    cfg = load_config()
    print("=" * 64)
    print("  J.A.R.V.I.S  //  VOICE ENROLLMENT")
    print("=" * 64)
    print("Loading speaker model (first run downloads ~25 MB)...")
    v = SpeakerVerifier(cfg["speaker_threshold"])
    mic = Microphone()
    mic.start()

    tts = None
    try:
        from .audio import Player
        from .speech import TextToSpeech
        tts = TextToSpeech(cfg["tts_voice"], cfg["tts_rate"], Player())
    except Exception:
        pass

    def say(text):
        """Speak an instruction with the mic muted so it doesn't record itself."""
        if tts is None:
            return
        mic.mute(True)
        try:
            tts.speak(text)
        except Exception:
            pass
        time.sleep(0.2)
        mic.mute(False)

    say("Let's learn your voice. First, two seconds of quiet please.")
    print("\nCalibrating background noise - stay quiet for 2 seconds...")
    t0 = time.time()
    while time.time() - t0 < 2:
        f = mic.read()
        if f is not None:
            mic.update_noise(f)
    mic.flush()

    embeddings = []
    print("\nRead each phrase aloud in your normal voice, at your usual distance from the mic.\n")
    say(f"Thank you. Now read each of the {len(PHRASES)} phrases in the window aloud, "
        "in your normal voice, after I say go.")
    for i, phrase in enumerate(PHRASES, 1):
        while True:
            print(f"[{i}/{len(PHRASES)}]  \"{phrase}\"")
            say(f"Phrase {i}. Go." if i > 1 else "Phrase one. Go.")
            audio = record_utterance(mic, start_timeout=10, silence_end_ms=1000, max_seconds=15)
            if audio is None or len(audio) < SAMPLE_RATE * 1.5:
                print("   Didn't catch enough audio - let's try that one again.")
                say("I didn't quite catch that. Once more, please.")
                continue
            embeddings.append(v.embed(audio))
            print(f"   Got it ({len(audio) / SAMPLE_RATE:.1f}s).")
            break

    v.save_voiceprint(embeddings)
    say("Excellent. Your voice print is saved. You can close this window.")
    mic.stop()
    print("\nCalibrating against other voices (first time: downloads ~90 voice samples)...")
    try:
        c = v.calibrate(embeddings)
        print(f"Voice print saved. Your phrases scored {min(c['own_scores']):.2f}-{max(c['own_scores']):.2f}; "
              f"the closest other voice scored {c['impostor_max']:.2f}.")
        print(f"Accept threshold {c['threshold']:.2f} (wake word {c['wake_threshold']:.2f}).")
        if min(c['own_scores']) <= c['impostor_p98'] + 0.06:
            print("Note: your voice and some others were close. Re-enrolling in a quieter room, "
                  "speaking as you normally talk to JARVIS, usually helps.")
    except Exception as e:
        print(f"Voice print saved (calibration failed: {e}; using the basic check).")
    print("Run  python -m jarvis.enroll test  to check recognition.")
    try:
        input("\nPress Enter to close this window.")
    except EOFError:
        pass


def test():
    cfg = load_config()
    v = SpeakerVerifier(cfg["speaker_threshold"])
    if not v.enrolled:
        sys.exit("Not enrolled yet.")
    mic = Microphone()
    mic.start()
    print("Say something (Ctrl+C to stop). Try having someone else speak too.")
    try:
        while True:
            audio = record_utterance(mic, start_timeout=30, silence_end_ms=900, max_seconds=10)
            if audio is None:
                continue
            ok, s = v.verify(audio)
            print(f"  score {s:.2f} -> {'ACCEPTED' if ok else 'rejected'}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    test() if len(sys.argv) > 1 and sys.argv[1] == "test" else main()
