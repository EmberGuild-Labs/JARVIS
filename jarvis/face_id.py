"""Owner face verification: answers only "is this the owner?".

You enroll your own face once (a few seconds in front of the webcam). After that,
faces seen by the webcam are compared against *your* faceprint only: the answer is
"owner" or "not the owner". Nobody else is ever enrolled, named or identified, and
no other faces are stored (except the sentry photo when someone else uses your PC
while you're away, which stays on your disk).

Uses OpenCV's SFace model (~37 MB, CPU, a few ms per face) with YuNet alignment.
"""
from __future__ import annotations

import logging
import threading
import urllib.request

import numpy as np

from .config import DATA_DIR, MODELS_DIR

log = logging.getLogger("jarvis")
SFACE_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_recognition_sface/face_recognition_sface_2021dec.onnx")
SFACE_PATH = MODELS_DIR / "face_recognition_sface_2021dec.onnx"
FACEPRINT_PATH = DATA_DIR / "faceprint.npy"
_lock = threading.Lock()
_rec = None


def _recognizer():
    global _rec
    if _rec is None:
        import cv2
        if not SFACE_PATH.exists():
            urllib.request.urlretrieve(SFACE_URL, SFACE_PATH)
        _rec = cv2.FaceRecognizerSF.create(str(SFACE_PATH), "")
    return _rec


def largest_face(rows: np.ndarray):
    if rows is None or len(rows) == 0:
        return None
    return max(rows, key=lambda r: r[2] * r[3])


def embed(rgb: np.ndarray, row) -> np.ndarray:
    """Normalised 128-d feature for one detected face."""
    import cv2
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    with _lock:
        rec = _recognizer()
        crop = rec.alignCrop(bgr, row)
        f = rec.feature(crop).flatten().astype(np.float32)
    return f / (np.linalg.norm(f) + 1e-9)


class FaceID:
    def __init__(self, threshold: float = 0.42):
        self.threshold = threshold  # cosine; OpenCV's suggested SFace cut-off is ~0.36
        self.print = np.load(FACEPRINT_PATH) if FACEPRINT_PATH.exists() else None

    @property
    def enrolled(self) -> bool:
        return self.print is not None

    def score(self, rgb: np.ndarray, row) -> float:
        if self.print is None:
            return 0.0
        return float(np.dot(embed(rgb, row), self.print))

    def is_owner(self, rgb: np.ndarray, row) -> tuple[bool, float]:
        s = self.score(rgb, row)
        return s >= self.threshold, s

    def save(self, features: list[np.ndarray]):
        v = np.mean(np.stack(features), axis=0)
        v = v / (np.linalg.norm(v) + 1e-9)
        FACEPRINT_PATH.parent.mkdir(parents=True, exist_ok=True)
        np.save(FACEPRINT_PATH, v)
        self.print = v

    def forget(self):
        FACEPRINT_PATH.unlink(missing_ok=True)
        self.print = None


def enroll_from_camera(read_rgb, say=print, samples: int = 15, timeout: float = 40) -> bool:
    """Capture `samples` good frames of the person at the camera and save the faceprint.
    read_rgb() returns a frame (or None). say(text) gives spoken/printed guidance."""
    import time
    from .vision import detect_faces_raw
    feats, prompts = [], ["Look straight at the camera.", "Now turn your head slightly left.",
                          "And slightly right.", "Tilt your chin up a little.", "That's it, hold still."]
    say(prompts[0])
    t0, last_prompt = time.time(), time.time()
    step = 1
    while len(feats) < samples and time.time() - t0 < timeout:
        frame = read_rgb()
        if frame is None:
            time.sleep(0.1)
            continue
        rows = detect_faces_raw(frame)
        if len(rows) != 1:  # exactly one person in view while enrolling
            time.sleep(0.15)
            continue
        row = rows[0]
        if row[2] < 80 or row[-1] < 0.85:  # too small / unsure
            time.sleep(0.15)
            continue
        feats.append(embed(frame, row))
        if time.time() - last_prompt > 2.5 and step < len(prompts):
            say(prompts[step])
            step += 1
            last_prompt = time.time()
        time.sleep(0.25)
    if len(feats) < max(5, samples // 2):
        return False
    FaceID().save(feats)
    return True
