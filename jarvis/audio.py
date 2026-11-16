"""Microphone capture, utterance recording and audio playback."""
from __future__ import annotations

import collections
import queue
import threading
import time

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
FRAME = 1280  # 80 ms, the frame size openWakeWord expects


class Microphone:
    """One always-open 16 kHz mono stream. Frames go to a queue; the last few
    seconds are also kept in a ring buffer so the wake word audio can be reused
    for speaker verification."""

    def __init__(self, preroll_seconds: float = 2.0):
        self.q: queue.Queue[np.ndarray] = queue.Queue(maxsize=400)
        self.ring = collections.deque(maxlen=int(preroll_seconds * SAMPLE_RATE / FRAME))
        self.noise_floor = 300.0
        self._muted = threading.Event()
        self.enabled = True          # False = mic switched off: the stream is closed
        self._lock = threading.Lock()
        self.stream = self._open()

    def _open(self):
        return sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=FRAME, callback=self._cb,
        )

    def _cb(self, indata, frames, t, status):
        frame = indata[:, 0].copy()
        self.peak = max(getattr(self, "peak", 0), int(np.abs(frame).max()) if frame.size else 0)
        self.ring.append(frame)
        if self._muted.is_set():
            return
        try:
            self.q.put_nowait(frame)
        except queue.Full:
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass
            self.q.put_nowait(frame)

    def start(self):
        with self._lock:
            if self.enabled and self.stream is not None:
                self.stream.start()

    def stop(self):
        with self._lock:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
                self.stream = None

    def set_enabled(self, on: bool):
        """Switch the microphone on or off. Off really closes the input stream, so
        nothing is captured and the OS mic indicator goes out."""
        with self._lock:
            if on == self.enabled:
                return
            self.enabled = on
            if on:
                self.stream = self._open()
                self.stream.start()
            else:
                if self.stream is not None:
                    self.stream.stop()
                    self.stream.close()
                    self.stream = None
                self.ring.clear()
        self.flush()

    def mute(self, on: bool):
        """Ignore the mic while JARVIS is speaking so it doesn't hear itself."""
        if on:
            self._muted.set()
        else:
            self.flush()
            self._muted.clear()

    def flush(self):
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        if not self.enabled:
            time.sleep(timeout)
            return None
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def preroll(self) -> np.ndarray:
        return np.concatenate(list(self.ring)) if self.ring else np.zeros(0, np.int16)

    def update_noise(self, frame: np.ndarray):
        level = rms(frame)
        # slow-moving estimate of background noise, only adapts downward fast
        if level < self.noise_floor:
            self.noise_floor = 0.7 * self.noise_floor + 0.3 * level
        else:
            self.noise_floor = 0.995 * self.noise_floor + 0.005 * level
        self.noise_floor = max(self.noise_floor, 80.0)


def rms(frame: np.ndarray) -> float:
    f = frame.astype(np.float32)
    return float(np.sqrt(np.mean(f * f))) if f.size else 0.0


def record_utterance(mic: Microphone, start_timeout: float, silence_end_ms: int,
                     max_seconds: float, on_level=None) -> np.ndarray | None:
    """Record one spoken utterance. Returns int16 audio, or None if nobody spoke
    within start_timeout seconds."""
    threshold = max(mic.noise_floor * 2.8, 450.0)
    frames: list[np.ndarray] = []
    started = False
    silent_ms = 0
    t0 = time.time()
    while True:
        frame = mic.read(timeout=0.5)
        if frame is None:
            if not started and time.time() - t0 > start_timeout:
                return None
            continue
        level = rms(frame)
        if on_level:
            on_level(min(1.0, level / 4000))
        if not started:
            frames.append(frame)
            frames = frames[-4:]  # keep ~320ms before speech onset
            if level > threshold:
                started = True
                t0 = time.time()
            elif time.time() - t0 > start_timeout:
                return None
            continue
        frames.append(frame)
        silent_ms = silent_ms + 80 if level < threshold else 0
        if silent_ms >= silence_end_ms or time.time() - t0 > max_seconds:
            break
    audio = np.concatenate(frames)
    return audio if len(audio) > SAMPLE_RATE * 0.4 else None


class Player:
    """Plays float32 PCM, interruptible."""

    def __init__(self):
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()
        self.current = None
        sd.stop()

    def window(self, n: int = 1536):
        """The samples playing right now (for the HUD's voice waveform), or None."""
        cur = getattr(self, "current", None)
        if cur is None:
            return None
        samples, sr, t0 = cur
        i = int((time.time() - t0) * sr)
        if i >= len(samples):
            return None
        return samples[max(0, i - n // 2): i + n // 2]

    def play(self, samples: np.ndarray, sr: int):
        self._stop.clear()
        self.current = (samples, sr, time.time())
        sd.play(samples, sr)
        dur = len(samples) / sr
        t0 = time.time()
        while time.time() - t0 < dur + 0.1:
            if self._stop.is_set():
                sd.stop()
                return
            time.sleep(0.05)
        sd.wait()


def chime(player: Player, kind: str = "wake"):
    """Short synthesized UI sounds (no audio files needed)."""
    sr = 24000
    if kind == "wake":
        notes = [(880, 0.07), (1320, 0.09)]
    elif kind == "deny":
        notes = [(330, 0.12), (220, 0.18)]
    else:
        notes = [(1320, 0.06), (990, 0.08)]
    parts = []
    for f, d in notes:
        t = np.linspace(0, d, int(sr * d), endpoint=False)
        env = np.minimum(1, np.minimum(t / 0.01, (d - t) / 0.03))
        parts.append(0.18 * np.sin(2 * np.pi * f * t) * env)
    player.play(np.concatenate(parts).astype(np.float32), sr)
