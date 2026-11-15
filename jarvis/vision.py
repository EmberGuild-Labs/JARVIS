"""On-demand face *detection* (where faces are, never who they are).

Uses OpenCV's YuNet model (~230 KB, CPU, roughly 30-80 ms per 1080p frame).
It only loads the first time a scan is requested and costs nothing at idle.
"""
from __future__ import annotations

import logging
import threading
import urllib.request

import numpy as np

from .config import MODELS_DIR

log = logging.getLogger("jarvis")

YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
YUNET_PATH = MODELS_DIR / "face_detection_yunet_2023mar.onnx"

_detector = None
_lock = threading.Lock()


def _get_detector():
    global _detector
    if _detector is None:
        import cv2
        if not YUNET_PATH.exists():
            YUNET_PATH.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(YUNET_URL, YUNET_PATH)
        _detector = cv2.FaceDetectorYN.create(str(YUNET_PATH), "", (320, 320),
                                              score_threshold=0.7, nms_threshold=0.3, top_k=300)
    return _detector


def detect_faces(rgb: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Return (x, y, w, h) boxes for faces in an RGB image."""
    import cv2
    with _lock:
        det = _get_detector()
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]
        det.setInputSize((w, h))
        _, faces = det.detect(bgr)
    boxes = []
    if faces is not None:
        for f in faces:
            x, y, bw, bh = (int(v) for v in f[:4])
            if bw >= 12 and bh >= 12:
                boxes.append((max(0, x), max(0, y), bw, bh))
    return boxes


def detect_faces_raw(rgb: np.ndarray) -> np.ndarray:
    """Full YuNet rows (box, 5 landmarks, score) - needed to align a face for
    owner verification. Returns an (N, 15) array, possibly empty."""
    import cv2
    with _lock:
        det = _get_detector()
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]
        det.setInputSize((w, h))
        _, faces = det.detect(bgr)
    return faces if faces is not None else np.zeros((0, 15), np.float32)


def describe_positions(boxes, sw: int, sh: int) -> str:
    def where(b):
        cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
        v = "top" if cy < sh / 3 else "bottom" if cy > 2 * sh / 3 else "middle"
        hz = "left" if cx < sw / 3 else "right" if cx > 2 * sw / 3 else "centre"
        return "centre" if (v, hz) == ("middle", "centre") else f"{v}-{hz}"
    return ", ".join(where(b) for b in boxes)
