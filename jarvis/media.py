"""What's playing: Windows' media session API, or Spotify / Music via AppleScript on macOS."""
from __future__ import annotations

import asyncio
import logging
import threading
import time

from . import osutil

log = logging.getLogger("jarvis")
_STATUS = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}


async def _query() -> dict | None:
    from winrt.windows.media.control import \
        GlobalSystemMediaTransportControlsSessionManager as Manager
    mgr = await Manager.request_async()
    s = mgr.get_current_session()
    if s is None:
        return None
    props = await s.try_get_media_properties_async()
    status = s.get_playback_info().playback_status
    app = s.source_app_user_model_id or ""
    app = app.split("!")[-1].removesuffix(".exe").replace("SpotifyAB.SpotifyMusic_zpdnekdrzrea0", "Spotify")
    return {"title": props.title or "", "artist": props.artist or "",
            "album": props.album_title or "", "app": app or "unknown",
            "status": _STATUS.get(int(status), str(status))}


def _mac_now_playing() -> dict | None:
    """Spotify or Music via AppleScript; only asks apps that are already running."""
    sep = "\u241f"
    for app, state_word in (("Spotify", "player state"), ("Music", "player state")):
        if osutil.osascript(f'application "{app}" is running') != "true":
            continue
        script = (f'tell application "{app}"\n'
                  f'  set st to (get {state_word}) as text\n'
                  f'  if st is "stopped" then return st\n'
                  f'  set t to current track\n'
                  f'  return st & "{sep}" & (name of t) & "{sep}" & (artist of t) & "{sep}" & (album of t)\n'
                  f'end tell')
        out = osutil.osascript(script)
        if not out or out == "stopped":
            continue
        st, _, rest = out.partition(sep)
        parts = rest.split(sep) + ["", "", ""]
        if st == "paused" or st == "playing":
            return {"title": parts[0], "artist": parts[1], "album": parts[2],
                    "app": app, "status": st}
    return None


def now_playing(timeout: float = 4.0) -> dict | None:
    """Query on a fresh multi-threaded-apartment thread (the media API rejects calls
    from single-threaded COM apartments such as the Tk/main thread)."""
    global _busy
    if osutil.IS_MAC:
        return _mac_now_playing()
    if _busy is not None and _busy.is_alive():  # previous query still stuck (e.g. PC locked)
        return None
    out: dict = {}

    def worker():
        try:
            from winrt.runtime import MTA, init_apartment
            try:
                init_apartment(MTA)
            except Exception:
                pass
            out["v"] = asyncio.run(_query())
        except Exception as e:
            log.debug("media query failed: %s", e)

    t = threading.Thread(target=worker, daemon=True)
    _busy = t
    t.start()
    t.join(timeout)
    return out.get("v")


_busy: threading.Thread | None = None


class TrackWatcher:
    """Shows a small now-playing panel on the HUD when the track changes.
    Polls every 5 s (a single cheap OS call)."""

    def __init__(self, hud, cfg):
        self.hud, self.cfg = hud, cfg
        self.last = None

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="media").start()

    def _loop(self):
        fails = 0
        while True:
            time.sleep(5 if fails < 3 else 60)  # back off while the API is unavailable
            if not self.cfg.get("media_panel", True) or self.cfg.get("_standby"):
                continue
            info = now_playing()
            fails = fails + 1 if info is None else 0
            key = (info["title"], info["artist"]) if info and info["status"] == "playing" else None
            if key and key != self.last and self.last is not None:
                lines = [info["title"]] + ([info["artist"]] if info["artist"] else [])
                self.hud.panel("Now playing", lines, 6)
            if key:
                self.last = key
            elif self.last is None:
                self.last = ("",)
