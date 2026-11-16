"""Webcam presence: knows when you sit down and when you walk away.

Detection only: it checks whether a face is in front of the camera, never whose.
Frames are processed in memory and never saved. With no webcam it just looks for
one every few minutes, so it costs nothing until you plug one in.

  - You leave (no face for `presence_away_sec`): JARVIS goes "away" and holds its
    announcements (alerts, finished tasks, reminders) for later.
  - Still gone after `presence_lock_min` minutes and no keyboard/mouse use: it locks
    the PC (if `presence_lock`).
  - You come back (and the PC is unlocked): "Welcome back", plus a briefing of
    anything that happened while you were gone.
"""
from __future__ import annotations

import logging
import threading
import time

from . import osutil

log = logging.getLogger("jarvis")


def idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input."""
    return osutil.idle_seconds()


def pc_locked() -> bool:
    return osutil.screen_locked()


def _frontal(row) -> bool:
    """Is this face roughly front-on? Uses YuNet's landmarks: eyes level, nose between
    the eyes, and a normal eye-to-nose distance (looking down/up squashes or stretches it)."""
    rex, rey, lex, ley, nx, ny = (float(v) for v in row[4:10])
    eye_d = abs(lex - rex)
    if eye_d < 1:
        return False
    level = abs(ley - rey) < 0.25 * eye_d
    centred = min(rex, lex) + 0.2 * eye_d < nx < max(rex, lex) - 0.2 * eye_d
    pitch = 0.35 < (ny - (rey + ley) / 2) / eye_d < 0.95
    return level and centred and pitch


def lock_pc():
    osutil.lock_screen()


class Camera:
    """Thin wrapper so tests can swap in a fake frame source."""

    def __init__(self, index: int):
        self.index = index
        self.cap = None

    def open(self) -> bool:
        import cv2
        backend = cv2.CAP_AVFOUNDATION if osutil.IS_MAC else cv2.CAP_DSHOW
        try:
            cap = cv2.VideoCapture(self.index, backend)
        except Exception:   # no camera / permission denied: fail soft
            log.debug("camera open failed", exc_info=True)
            return False
        if not cap.isOpened():
            cap.release()
            return False
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
        self.cap = cap
        return True

    def read_rgb(self):
        import cv2
        if self.cap is None:
            return None
        ok, frame = self.cap.read()
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) if ok else None

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class PresenceMonitor:
    def __init__(self, cfg: dict, on_away, on_return, on_camera=None, camera=None,
                 clock=time.time, sleep=time.sleep, locker=lock_pc, is_locked=pc_locked,
                 idle=idle_seconds, face_id=None, on_intruder=None):
        self.cfg = cfg
        self.on_away, self.on_return, self.on_camera = on_away, on_return, on_camera
        self.camera = camera or Camera(cfg.get("camera_index", 0))
        self.clock, self.sleep = clock, sleep
        self.locker, self.is_locked, self.idle = locker, is_locked, idle
        self.present = True
        self.last_seen = clock()
        self.left_at: float | None = None
        self.locked_by_us = False
        self.hits = 0
        self.has_camera = False
        self._stop = threading.Event()
        self.face_id = face_id
        self.on_intruder = on_intruder
        self.strange_hits = 0
        self.paused = False
        self.last_intruder = -1e9

    def _intruder(self, frame, now):
        """Save one photo locally, lock the PC, and report it."""
        import datetime as dt
        from PIL import Image
        from .config import DATA_DIR
        folder = DATA_DIR / "sentry"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = folder / f"{stamp}.jpg"
        try:
            Image.fromarray(frame).save(path, quality=85)
        except Exception:
            log.exception("sentry photo failed")
            path = None
        log.warning("sentry: someone else is at the PC (photo: %s)", path)
        if not self.is_locked():
            self.locker()
            self.locked_by_us = True
        if self.on_intruder:
            self.on_intruder(path, dt.datetime.now())

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="presence").start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            if not self.cfg.get("presence_enabled", True):
                self.camera.close()
                self.has_camera = False
                self.sleep(30)
                continue
            if not self.has_camera:
                self.has_camera = self.camera.open()
                if self.on_camera:
                    self.on_camera(self.has_camera)
                if not self.has_camera:
                    self.sleep(self.cfg.get("presence_camera_retry_sec", 300))
                    continue
                log.info("presence: webcam %s active", self.cfg.get("camera_index", 0))
                self.last_seen = self.clock()
            try:
                if not self.paused:
                    self.step()
            except Exception:
                log.exception("presence step failed")
                self.camera.close()
                self.has_camera = False
            self.sleep(self.cfg.get("presence_interval_sec", 1.5))

    def _classify(self, frame) -> tuple[bool, bool]:
        """(owner_visible, stranger_visible). Without an enrolled faceprint any face
        counts as the owner and the sentry stays off (it can't tell you apart)."""
        from .face_id import largest_face
        from .vision import detect_faces_raw
        rows = detect_faces_raw(frame)
        rows = [r for r in rows if r[2] >= 40]  # ignore tiny faces (posters, background)
        if not rows:
            return False, False
        if self.face_id is None or not self.face_id.enrolled:
            return True, False
        owner = stranger = False
        for r in sorted(rows, key=lambda r: -r[2] * r[3])[:3]:
            ok, s = self.face_id.is_owner(frame, r)
            owner |= ok
            # Only a clear, confident, front-on face may count as "someone else": a
            # tilted or partial view of *you* (looking down, turning away) scores low
            # too, and must never lock you out.
            stranger |= ((not ok) and s < self.face_id.threshold - 0.08
                         and _frontal(r) and r[-1] >= 0.9 and r[2] >= 60)
        return owner, stranger

    def step(self):
        """One camera check; drives the away / lock / return / sentry state machine."""
        frame = self.camera.read_rgb()
        if frame is None:
            raise RuntimeError("camera returned no frame")
        now = self.clock()
        owner, stranger = self._classify(frame)
        if owner:
            self.hits += 1
            self.last_seen = now
        else:
            self.hits = 0

        # Sentry: a face that isn't yours, with you not in view -> lock right away
        # (two frames in a row, ~3 s, so a single misread can't lock you out).
        guest = now < self.cfg.get("_guest_until", 0)
        self.strange_hits = self.strange_hits + 1 if (stranger and not owner) else 0
        # ~4.5 s of a clear stranger; ~9 s if you were in view moments ago (you most
        # likely just looked away), so a few odd frames of you can't trigger it.
        need = 6 if now - self.last_seen < 10 else 3
        if self.strange_hits >= need and self.cfg.get("sentry_enabled", True) and not guest:
            if not self.is_locked():
                log.warning("sentry: unrecognised face, locking PC")
                self.locker()
                self.locked_by_us = True
            if now - self.last_intruder >= self.cfg.get("sentry_cooldown_min", 5) * 60:
                self.last_intruder = now
                self._intruder(frame, now)   # one photo + report per cooldown
            if self.present:  # treat it like you being away (holds messages)
                self.present = False
                self.left_at = self.last_seen
                self.on_away()

        if self.present and now - self.last_seen >= self.cfg.get("presence_away_sec", 20):
            self.present = False
            self.left_at = self.last_seen
            log.info("presence: away")
            self.on_away()

        if not self.present:
            gone = now - (self.left_at or now)
            lock_after = self.cfg.get("presence_lock_sec", 20)
            # Nobody in view: lock - unless the keyboard/mouse is being used (you're there,
            # just out of shot or looking down). A stranger typing is caught by the sentry.
            idle_ok = (not self.cfg.get("presence_lock_requires_idle", True)
                       or self.idle() >= lock_after)
            if (self.cfg.get("presence_lock", True) and not guest and not self.locked_by_us
                    and gone >= lock_after and idle_ok and not self.is_locked()):
                log.info("presence: nobody in view, locking PC")
                self.locker()
                self.locked_by_us = True
            # back: two face frames in a row (no flicker), and only once unlocked
            if self.hits >= 2 and not self.is_locked():
                self.present = True
                self.locked_by_us = False
                log.info("presence: back after %.0f s", gone)
                self.on_return(gone)
