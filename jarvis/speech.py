"""Wake word, speech-to-text, speaker verification and text-to-speech."""
from __future__ import annotations

import asyncio
import io
import logging
import queue
import re
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

from .audio import SAMPLE_RATE, Player
from .config import MODELS_DIR, VOICEPRINT_PATH

log = logging.getLogger("jarvis")

SPEAKER_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx"
)
SPEAKER_MODEL_PATH = MODELS_DIR / "wespeaker_en_voxceleb_resnet34_LM.onnx"
BASE_VOICEPRINT_PATH = VOICEPRINT_PATH.with_name("voiceprint_base.npy")
CALIB_PATH = VOICEPRINT_PATH.with_name("voice_calib.json")
COHORT_PATH = MODELS_DIR / "speaker_cohort.npy"


# --------------------------------------------------------------------- wake word
class WakeWord:
    def __init__(self, threshold: float):
        import openwakeword
        from openwakeword.model import Model

        try:
            openwakeword.utils.download_models(model_names=["hey_jarvis"])
        except Exception as e:  # already present or offline
            log.debug("wakeword download skipped: %s", e)
        self.model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        self.threshold = threshold

    def detect(self, frame: np.ndarray, threshold: float | None = None) -> bool:
        scores = self.model.predict(frame)
        th = self.threshold if threshold is None else threshold
        return any(v >= th for v in scores.values())

    def reset(self):
        self.model.reset()


# -------------------------------------------------------------- speech-to-text
class SpeechToText:
    """faster-whisper, loaded lazily and unloaded after a period of idleness to
    keep RAM use low while JARVIS sits in the background."""

    def __init__(self, model_name: str, unload_after_min: float):
        self.model_name = model_name
        self.unload_after = unload_after_min * 60
        self._model = None
        self._last_used = 0.0
        self._lock = threading.Lock()
        threading.Thread(target=self._reaper, daemon=True).start()

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            log.info("loading whisper model %s", self.model_name)
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8",
                                       cpu_threads=4, download_root=str(MODELS_DIR / "whisper"))
        return self._model

    def warm(self):
        with self._lock:
            self._load()
            self._last_used = time.time()

    def transcribe(self, audio: np.ndarray) -> str:
        with self._lock:
            model = self._load()
            self._last_used = time.time()
            f = audio.astype(np.float32) / 32768.0
            segments, _ = model.transcribe(f, language="en", beam_size=1, vad_filter=True,
                                           initial_prompt="Jarvis, open, close, search, volume, standby, pull up, protocol, KiCad, Blender, BeamNG, Steam.")
            return " ".join(s.text.strip() for s in segments).strip()

    def _reaper(self):
        while True:
            time.sleep(60)
            with self._lock:
                if self._model is not None and time.time() - self._last_used > self.unload_after:
                    log.info("unloading whisper (idle)")
                    self._model = None


# --------------------------------------------------------- speaker verification
def ensure_speaker_model() -> Path:
    if not SPEAKER_MODEL_PATH.exists():
        log.info("downloading speaker model")
        SPEAKER_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SPEAKER_MODEL_PATH.with_suffix(".part")
        urllib.request.urlretrieve(SPEAKER_MODEL_URL, tmp)
        tmp.replace(SPEAKER_MODEL_PATH)
    return SPEAKER_MODEL_PATH


class SpeakerVerifier:
    def __init__(self, threshold: float):
        import sherpa_onnx

        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(ensure_speaker_model()), num_threads=1)
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        self.threshold = threshold
        self.base_threshold = threshold
        self.wake_threshold = None      # set by calibration
        self.mu = None                  # cohort mean (centred scoring) once calibrated
        self.voiceprint = np.load(VOICEPRINT_PATH) if VOICEPRINT_PATH.exists() else None
        self._load_calibration()

    @property
    def enrolled(self) -> bool:
        return self.voiceprint is not None

    def refresh(self) -> bool:
        """Reload the voice print if enrollment (re)wrote it. Returns True if newly loaded."""
        try:
            mtime = VOICEPRINT_PATH.stat().st_mtime
            if CALIB_PATH.exists():   # calibration lands a little after the voice print
                mtime += CALIB_PATH.stat().st_mtime
        except FileNotFoundError:
            return False
        if mtime != getattr(self, "_mtime", None):
            self._mtime = mtime
            self.voiceprint = np.load(VOICEPRINT_PATH)
            self._load_calibration()
            return True
        return False

    def embed(self, audio: np.ndarray) -> np.ndarray:
        stream = self.extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, audio.astype(np.float32) / 32768.0)
        stream.input_finished()
        e = np.array(self.extractor.compute(stream), dtype=np.float32)
        return e / (np.linalg.norm(e) + 1e-9)

    def score(self, audio: np.ndarray) -> float:
        self._last = None
        if self.voiceprint is None or len(audio) < SAMPLE_RATE:
            return 0.0
        e = self.embed(audio)
        self._last = (e, len(audio))
        return self.sim(e, self.voiceprint)

    # ---- centred scoring + calibration
    @property
    def calibrated(self) -> bool:
        return self.mu is not None

    def sim(self, a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity; once calibrated, of the cohort-mean-centred embeddings."""
        if self.mu is None:
            return float(np.dot(a, b))
        a, b = a - self.mu, b - self.mu
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

    def _load_calibration(self):
        import json
        self.mu, self.threshold, self.wake_threshold = None, self.base_threshold, None
        try:
            c = json.loads(CALIB_PATH.read_text(encoding='utf-8'))
            if c.get('voiceprint_mtime') and VOICEPRINT_PATH.exists() and (
                    abs(c['voiceprint_mtime'] - BASE_VOICEPRINT_PATH.stat().st_mtime) > 5):
                return   # calibration belongs to an older enrollment
            self.mu = np.load(COHORT_PATH).mean(axis=0)
            self.threshold = float(c['threshold'])
            self.wake_threshold = float(c['wake_threshold'])
        except Exception:
            self.mu = None

    def calibrate(self, embeddings: list[np.ndarray]) -> dict:
        """From your enrollment phrases and the cohort of other voices, pick the accept
        threshold that sits between 'you' and 'everyone else'."""
        import json
        if not COHORT_PATH.exists():
            from .make_cohort import build
            build(self, log=log.info)
        M = np.load(COHORT_PATH)
        self.mu = M.mean(axis=0)
        E = np.stack(embeddings)
        own = []
        for i in range(len(E)):   # each phrase against a print made from the others
            rest = np.delete(E, i, 0).mean(axis=0)
            own.append(self.sim(E[i], rest))
        vp = E.mean(axis=0)
        others = sorted(self.sim(m, vp) for m in M)
        own_low = float(np.percentile(own, 10)) if len(own) > 3 else min(own)
        imp_high = float(others[int(0.98 * (len(others) - 1))])
        if own_low > imp_high + 0.06:
            thr = (own_low + imp_high) / 2
        else:   # weak separation: favour letting you in, the request check still applies
            thr = own_low - 0.04
        thr = float(min(max(thr, 0.20), 0.75))
        # "Hey Jarvis" is short, so it scores lower for you too: a looser gate that still
        # filters most other voices; the full request check is the real lock.
        wake = float(max(thr - 0.22, 0.15))
        wake = min(wake, thr)
        c = {'threshold': round(thr, 3), 'wake_threshold': round(wake, 3),
             'own_scores': [round(x, 3) for x in own], 'impostor_max': round(others[-1], 3),
             'impostor_p98': round(imp_high, 3), 'cohort_size': int(len(M)),
             'voiceprint_mtime': BASE_VOICEPRINT_PATH.stat().st_mtime if BASE_VOICEPRINT_PATH.exists() else 0}
        CALIB_PATH.write_text(json.dumps(c, indent=1), encoding='utf-8')
        self.threshold, self.wake_threshold = thr, wake
        return c

    def verify(self, audio: np.ndarray) -> tuple[bool, float]:
        s = self.score(audio)
        return s >= self.threshold, s

    def save_voiceprint(self, embeddings: list[np.ndarray]):
        v = np.mean(np.stack(embeddings), axis=0)
        v = v / (np.linalg.norm(v) + 1e-9)
        VOICEPRINT_PATH.parent.mkdir(parents=True, exist_ok=True)
        np.save(VOICEPRINT_PATH, v)
        np.save(BASE_VOICEPRINT_PATH, v)  # anchor for adaptation
        self.voiceprint = v
        self._mtime = VOICEPRINT_PATH.stat().st_mtime

    def adapt(self, score: float, rate: float = 0.04, margin: float = 0.12) -> bool:
        """Learn from the last verified sample, but only when it was clearly you
        (score well above the threshold, 2 s+ of speech) and still close to your
        original enrollment, so the print can't drift towards someone else."""
        last = getattr(self, "_last", None)
        if last is None or self.voiceprint is None:
            return False
        e, n = last
        if score < self.threshold + margin or n < SAMPLE_RATE * 2:
            return False
        base = np.load(BASE_VOICEPRINT_PATH) if BASE_VOICEPRINT_PATH.exists() else self.voiceprint
        if self.sim(e, base) < self.threshold + margin / 2:
            return False
        v = (1 - rate) * self.voiceprint + rate * e
        v = v / (np.linalg.norm(v) + 1e-9)
        if float(np.dot(v, base)) < 0.85:  # never wander far from the enrolled voice
            v = 0.5 * v + 0.5 * base
            v = v / (np.linalg.norm(v) + 1e-9)
        self.voiceprint = v
        self._updates = getattr(self, "_updates", 0) + 1
        if self._updates % 5 == 0:  # persist every few updates
            if not BASE_VOICEPRINT_PATH.exists():
                np.save(BASE_VOICEPRINT_PATH, np.load(VOICEPRINT_PATH))
            np.save(VOICEPRINT_PATH, v)
            self._mtime = VOICEPRINT_PATH.stat().st_mtime
            log.info("voice print adapted (%d updates)", self._updates)
        return True


# ---------------------------------------------------------------- text-to-speech
class TextToSpeech:
    """Neural voice via edge-tts (online); falls back to the offline Windows
    voice (pyttsx3) when there's no connection."""

    def __init__(self, voice: str, rate: str, player: Player):
        self.voice, self.rate, self.player = voice, rate, player
        self._loop = asyncio.new_event_loop()

    def _synth_edge(self, text: str) -> tuple[np.ndarray, int]:
        import edge_tts
        import miniaudio

        async def run():
            buf = io.BytesIO()
            comm = edge_tts.Communicate(text, self.voice, rate=self.rate)
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()

        mp3 = self._loop.run_until_complete(asyncio.wait_for(run(), timeout=15))
        dec = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.FLOAT32,
                               nchannels=1, sample_rate=24000)
        return np.frombuffer(dec.samples, dtype=np.float32), 24000

    def _synth(self, text: str):
        try:
            return self._synth_edge(text)
        except Exception as e:
            log.warning("edge-tts failed (%s); using offline voice", e)
            return ("offline", text)

    def _play(self, item):
        if isinstance(item[0], str) and item[0] == "offline":
            import pyttsx3
            eng = pyttsx3.init()
            eng.say(item[1])
            eng.runAndWait()
        else:
            self.player.play(*item)

    def stream(self) -> "SpeechStream":
        return SpeechStream(self)

    def speak(self, text: str) -> bool:
        """Speak text (pipelined sentence by sentence). Returns False if stopped early."""
        s = self.stream()
        s.feed(text)
        s.close()
        return s.wait()

    def stop(self):
        self.player.stop()


_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+(?=[A-Z0-9\"'(])")
_SOURCES = re.compile(r"(^|\n|\s)(sources?|references?|citations?)\s*:", re.I)
_URL = re.compile(r"\(?https?://\S+\)?|\bwww\.\S+")


class SpeechStream:
    """Speak text as it arrives: complete sentences are synthesized on one thread
    while the previous sentence plays on another, so speech starts after the
    first sentence instead of after the whole reply. stop() cuts it off."""

    def __init__(self, tts: TextToSpeech):
        self.tts = tts
        self._text_q: queue.Queue = queue.Queue()
        self._audio_q: queue.Queue = queue.Queue(maxsize=3)
        self._buf = ""
        self._pending = 0
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._done = threading.Event()
        self.stopped = threading.Event()
        self.spoke = False
        threading.Thread(target=self._synth_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    def feed(self, chunk: str):
        if self.stopped.is_set() or not chunk:
            return
        self._buf += chunk
        parts = _SENTENCE_END.split(self._buf)
        ready, self._buf = parts[:-1], parts[-1]
        # merge very short fragments ("Right.") into the next sentence
        merged, acc = [], ""
        for p in ready:
            acc = f"{acc} {p}".strip()
            if len(acc) >= 25:
                merged.append(acc)
                acc = ""
        self._buf = f"{acc} {self._buf.lstrip()}" if acc else self._buf
        for sentence in merged:
            self._enqueue(sentence)

    def _enqueue(self, sentence: str):
        if self.stopped.is_set() or getattr(self, "_drop_rest", False):
            return
        # Citations / source lists are for the HUD, never read aloud.
        m = _SOURCES.search(sentence)
        if m:
            self._drop_rest = True
            sentence = sentence[:m.start()]
        sentence = _URL.sub("", sentence)
        sentence = re.sub(r"[*_`#>|]+", "", sentence)       # stray markdown
        sentence = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", sentence)
        sentence = sentence.strip()
        if not sentence:
            return
        with self._lock:
            self._pending += 1
            self._idle.clear()
        self.spoke = True
        self._text_q.put(sentence)

    def flush(self):
        """Speak whatever is buffered (end of a message)."""
        buf, self._buf = self._buf, ""
        self._enqueue(buf)

    def close(self):
        self.flush()
        self._text_q.put(None)

    def wait(self, timeout: float | None = None) -> bool:
        self._done.wait(timeout)
        return not self.stopped.is_set()

    def wait_idle(self, timeout: float = 60):
        """Block until everything queued so far has been spoken."""
        self._idle.wait(timeout)

    def stop(self):
        self.stopped.set()
        self.tts.player.stop()
        for q in (self._text_q, self._audio_q):
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
        self._text_q.put(None)
        try:
            self._audio_q.put_nowait(None)
        except queue.Full:
            pass
        self._idle.set()
        self._done.set()

    def _synth_loop(self):
        while True:
            s = self._text_q.get()
            if s is None:
                if not self.stopped.is_set():
                    self._audio_q.put(None)
                return
            if self.stopped.is_set():
                continue
            try:
                item = self.tts._synth(s)
            except Exception:
                log.exception("speech synthesis failed")
                item = ("offline", s)
            if not self.stopped.is_set():
                self._audio_q.put(item)

    def _play_loop(self):
        while True:
            item = self._audio_q.get()
            if item is None or self.stopped.is_set():
                self._idle.set()
                self._done.set()
                return
            try:
                self.tts._play(item)
            except Exception:  # never leave the stream hanging on a playback error
                log.exception("speech playback failed")
            with self._lock:
                self._pending -= 1
                if self._pending <= 0:
                    self._idle.set()
