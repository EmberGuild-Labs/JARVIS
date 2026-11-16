"""macOS window management: list, find, focus and arrange windows.

Same public API as winfront_win.py. A "window handle" is the window server's
kCGWindowNumber (an int). Listing uses Quartz; moving, resizing, minimising and
raising use the Accessibility API (needs Accessibility permission for the app that
runs JARVIS). Window titles need Screen Recording permission; without it we fall
back to the owning app's name so a title is never blank.
"""
from __future__ import annotations

import difflib
import logging
import os
import time

from . import osutil

log = logging.getLogger("jarvis")

_SKIP_OWNERS = {"window server", "dock", "control centre", "control center",
                "notification centre", "notification center", "systemuiserver",
                "spotlight", "loginwindow"}
_ALIASES = {"google chrome": "chrome", "visual studio code": "code"}

_NO_AX = ("I need Accessibility permission: System Settings > Privacy & Security > "
          "Accessibility, enable the app running JARVIS.")

# window number -> {"pid", "owner", "title", "bounds": (x, y, w, h)} from the last listing
_cache: dict[int, dict] = {}
_warned = False


# ------------------------------------------------------------------ helpers
def _norm(name: str) -> str:
    """'chrome.exe' / 'Google Chrome' / 'chrome' -> 'chrome'."""
    n = osutil.app_name(name or "").strip()
    return _ALIASES.get(n, n)


def _trusted() -> bool:
    global _warned
    try:
        from ApplicationServices import AXIsProcessTrusted
        ok = bool(AXIsProcessTrusted())
    except Exception:
        log.debug("AXIsProcessTrusted failed", exc_info=True)
        return False
    if not ok and not _warned:
        _warned = True
        log.warning("macfront: Accessibility permission missing - cannot move/raise windows. %s", _NO_AX)
    return ok


def _listing() -> list[dict]:
    """Eligible windows, front to back, and refresh the cache."""
    import Quartz
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    me = os.getpid()
    out: list[dict] = []
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []:
        try:
            if w.get("kCGWindowLayer", 0) != 0 or w.get("kCGWindowOwnerPID") == me:
                continue
            owner = w.get("kCGWindowOwnerName") or ""
            if owner.lower() in _SKIP_OWNERS:
                continue
            b = w.get("kCGWindowBounds") or {}
            if b.get("Width", 0) < 50 or b.get("Height", 0) < 50:
                continue
            out.append({"num": int(w["kCGWindowNumber"]), "pid": int(w["kCGWindowOwnerPID"]),
                        "owner": owner, "title": w.get("kCGWindowName") or "",
                        "bounds": (float(b["X"]), float(b["Y"]), float(b["Width"]), float(b["Height"]))})
        except Exception:
            log.debug("bad window entry", exc_info=True)
    for e in out:
        _cache[e["num"]] = e
    return out


def windows() -> list[int]:
    """Normal on-screen windows in z-order (frontmost first)."""
    try:
        return [e["num"] for e in _listing()]
    except Exception:
        log.warning("macfront.windows failed", exc_info=True)
        return []


def _info(h) -> dict | None:
    if h not in _cache:
        windows()
    return _cache.get(h)


def _title(h) -> str:
    try:
        e = _info(h)
        if not e:
            return ""
        return e["title"] or e["owner"]
    except Exception:
        return ""


def _exe(h) -> str:
    try:
        e = _info(h)
        return _norm(e["owner"]) if e else ""
    except Exception:
        return ""


# ------------------------------------------------------------------ Accessibility
def _ax_get(el, name):
    from ApplicationServices import AXUIElementCopyAttributeValue
    try:
        err, val = AXUIElementCopyAttributeValue(el, name, None)
    except Exception:
        return None
    return val if err == 0 else None


def _ax_point(el, name):
    from ApplicationServices import AXValueGetValue, kAXValueCGPointType
    v = _ax_get(el, name)
    if v is None:
        return None
    ok, p = AXValueGetValue(v, kAXValueCGPointType, None)
    return (float(p.x), float(p.y)) if ok else None


def _ax_size(el, name):
    from ApplicationServices import AXValueGetValue, kAXValueCGSizeType
    v = _ax_get(el, name)
    if v is None:
        return None
    ok, s = AXValueGetValue(v, kAXValueCGSizeType, None)
    return (float(s.width), float(s.height)) if ok else None


def _ax_window(h):
    """The AX window element for CG window h (matched by pid + title, else nearest bounds)."""
    e = _info(h)
    if not e or not _trusted():
        return None
    from ApplicationServices import AXUIElementCreateApplication
    app = AXUIElementCreateApplication(e["pid"])
    wins = _ax_get(app, "AXWindows") or []
    if not wins:
        return None
    x, y, w, hh = e["bounds"]
    best, best_score = None, 1e18
    for win in wins:
        pos, size = _ax_point(win, "AXPosition"), _ax_size(win, "AXSize")
        score = 0.0
        if pos and size:
            score = abs(pos[0] - x) + abs(pos[1] - y) + abs(size[0] - w) + abs(size[1] - hh)
        else:
            score = 1e6
        t = _ax_get(win, "AXTitle") or ""
        if e["title"] and t == e["title"]:
            score -= 1e5
        if score < best_score:
            best, best_score = win, score
    return best


def _ax_set_bool(el, name, value: bool) -> bool:
    from ApplicationServices import AXUIElementSetAttributeValue
    try:
        return AXUIElementSetAttributeValue(el, name, bool(value)) == 0
    except Exception:
        log.debug("AX set %s failed", name, exc_info=True)
        return False


def _is_minimized(el) -> bool:
    return bool(_ax_get(el, "AXMinimized"))


def _set_frame(win, x, y, w, h) -> bool:
    """Position then size then position again (size can be clamped by the screen edge)."""
    import Quartz
    from ApplicationServices import (AXUIElementSetAttributeValue, AXValueCreate,
                                     kAXValueCGPointType, kAXValueCGSizeType)
    try:
        def pos():
            return AXUIElementSetAttributeValue(
                win, "AXPosition", AXValueCreate(kAXValueCGPointType, Quartz.CGPoint(x, y)))

        def size():
            return AXUIElementSetAttributeValue(
                win, "AXSize", AXValueCreate(kAXValueCGSizeType, Quartz.CGSize(w, h)))
        e1 = pos()
        e2 = size()
        pos()
        return e1 == 0 and e2 == 0
    except Exception:
        log.debug("set frame failed", exc_info=True)
        return False


def _place(h, x, y, w, hh) -> bool:
    win = _ax_window(h)
    if win is None:
        return False
    if _is_minimized(win):
        _ax_set_bool(win, "AXMinimized", False)
    ok = _set_frame(win, int(x), int(y), int(w), int(hh))
    _cache.pop(h, None)
    return ok


# ------------------------------------------------------------------ focus
def force_front(h) -> bool:
    if not h:
        return False
    try:
        e = _info(h)
        if not e:
            return False
        from AppKit import NSRunningApplication
        win = _ax_window(h)
        if win is not None and _is_minimized(win):
            _ax_set_bool(win, "AXMinimized", False)
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(e["pid"])
        if app is not None:
            try:
                app.activateWithOptions_(2)   # NSApplicationActivateIgnoringOtherApps
            except Exception:
                try:
                    app.activate()
                except Exception:
                    pass
        if win is not None:
            from ApplicationServices import AXUIElementPerformAction
            _ax_set_bool(win, "AXMain", True)
            AXUIElementPerformAction(win, "AXRaise")
        time.sleep(0.12)
        front = _listing()
        return bool(front) and front[0]["pid"] == e["pid"]
    except Exception:
        log.warning("macfront.force_front failed", exc_info=True)
        return False


def find_window(query: str) -> int | None:
    """'' = the window you're using; else best match on title or app name."""
    try:
        wins = windows()
        if not wins:
            return None
        if not (query or "").strip():
            return wins[0]
        q = query.lower().strip()
        nq = _norm(q)
        for h in wins:
            if q in _title(h).lower():
                return h
        for h in wins:
            ex = _exe(h)
            if ex and (nq == ex or nq.replace(" ", "") in ex.replace(" ", "")):
                return h
        for h in wins:
            if q in (_info(h) or {}).get("owner", "").lower():
                return h
        titles = {_title(h).lower(): h for h in wins}
        close = difflib.get_close_matches(q, titles.keys(), n=1, cutoff=0.35)
        return titles[close[0]] if close else None
    except Exception:
        log.warning("macfront.find_window failed", exc_info=True)
        return None


# ------------------------------------------------------------------ monitors
def monitors() -> list[tuple[int, int, int, int]]:
    """Work areas (left, top, right, bottom) in top-left-origin points, left to right."""
    try:
        from AppKit import NSScreen
        screens = list(NSScreen.screens())
        if not screens:
            return [(0, 0, 1440, 900)]
        H = screens[0].frame().size.height   # primary screen defines the flip
        out = []
        for s in screens:
            f = s.visibleFrame()
            left, top = f.origin.x, H - (f.origin.y + f.size.height)
            out.append((int(round(left)), int(round(top)),
                        int(round(left + f.size.width)), int(round(top + f.size.height))))
        return sorted(out)
    except Exception:
        log.warning("macfront.monitors failed", exc_info=True)
        return [(0, 0, 1440, 900)]


def _rect(h):
    e = _info(h)
    if not e:
        return None
    x, y, w, hh = e["bounds"]
    return x, y, w, hh


def _monitor_of(h) -> int:
    r = _rect(h)
    if not r:
        return 0
    cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
    for i, (l, t, rr, b) in enumerate(monitors()):
        if l <= cx < rr and t <= cy < b:
            return i
    return 0


# ------------------------------------------------------------------ arrange
_cleared: list[tuple[int, object, str]] = []   # (pid, AX window element, title)


def arrange(title: str, action: str) -> str:
    try:
        return _arrange(title, action)
    except Exception as ex:
        log.warning("macfront.arrange failed", exc_info=True)
        return f"Couldn't do that: {ex}"


def _arrange(title: str, action: str) -> str:
    action = action.lower().strip()
    if not _trusted():
        return _NO_AX
    if action == "clear_desk":
        from AppKit import NSWorkspace  # noqa: F401  (ensures AppKit is importable)
        _cleared.clear()
        for h in windows():
            win = _ax_window(h)
            if win is None or _is_minimized(win):
                continue
            if _ax_set_bool(win, "AXMinimized", True):
                _cleared.append((_cache[h]["pid"], win, _title(h)))
        return f"Desk cleared: {len(_cleared)} windows minimized."
    if action == "restore_desk":
        n = 0
        for pid, win, t in reversed(_cleared):
            if _ax_set_bool(win, "AXMinimized", False):
                n += 1
        _cleared.clear()
        return f"Restored {n} windows."
    if action == "side_by_side":
        a, _, b = title.partition("|")
        ha, hb = find_window(a), find_window(b)
        if not ha or not hb:
            return f"Couldn't find {'both windows' if not ha and not hb else (a if not ha else b)}."
        l, t, r, btm = monitors()[_monitor_of(ha)]
        half = (r - l) // 2
        _place(ha, l, t, half, btm - t)
        _place(hb, l + half, t, r - l - half, btm - t)
        force_front(hb)
        force_front(ha)
        return f"{_title(ha)[:40]} on the left, {_title(hb)[:40]} on the right."
    h = find_window(title)
    if not h:
        return f"No window matching '{title}'."
    name = _title(h)[:50]
    mons = monitors()
    cur = _monitor_of(h)
    if action == "next_monitor" or action.startswith("monitor_"):
        if len(mons) < 2:
            return "There's only one monitor."
        if action == "next_monitor":
            target = (cur + 1) % len(mons)
        else:
            try:
                target = max(0, min(len(mons) - 1, int(action.split("_")[1]) - 1))
            except ValueError:
                return f"Unknown action {action}."
        x, y, w, hh = _rect(h)
        (sl, st, sr, sb), (tl, tt, tr, tb) = mons[cur], mons[target]
        w, hh = min(w, tr - tl), min(hh, tb - tt)
        nx = tl + int((x - sl) / max(1, sr - sl) * (tr - tl))
        ny = tt + int((y - st) / max(1, sb - st) * (tb - tt))
        ok = _place(h, max(tl, min(nx, tr - w)), max(tt, min(ny, tb - hh)), w, hh)
        force_front(h)
        return f"Moved {name} to screen {target + 1}." if ok else f"Couldn't move {name}."
    l, t, r, b = mons[cur]
    W, H = r - l, b - t
    spots = {"left": (l, t, W // 2, H), "right": (l + W // 2, t, W - W // 2, H),
             "top": (l, t, W, H // 2), "bottom": (l, t + H // 2, W, H - H // 2),
             "center": (l + W // 6, t + H // 8, W * 2 // 3, H * 3 // 4),
             "maximize": (l, t, W, H)}
    if action not in spots:
        return f"Unknown action {action}."
    ok = _place(h, *spots[action])
    force_front(h)
    return f"{name}: {action}." if ok else f"Couldn't resize {name} (it may not allow it)."


# ------------------------------------------------------------------ launch
def front_after_launch(before: set[int], exe: str | None = None, hint: str = "",
                       timeout: float = 20.0) -> str | None:
    """After launching something: wait for its window (a new window, else the newest
    window of `exe`, else one whose title contains `hint`) and bring it to the front.
    Returns the window title, or None if nothing showed up."""
    try:
        hint = (hint or "").lower().strip()
        nexe = _norm(exe) if exe else ""
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(0.35)
            wins = windows()
            new = [h for h in wins if h not in before]
            pick = None
            if nexe:
                pick = next((h for h in new if _exe(h) == nexe), None)
                if pick is None and time.time() - t0 > 1.2:   # reused an existing window
                    pick = next((h for h in wins if _exe(h) == nexe), None)
            else:
                words = [w for w in hint.split() if len(w) > 2]
                named = [h for h in new if any(w in _title(h).lower() or w in _exe(h) for w in words)]
                pick = named[0] if named else (new[0] if new and time.time() - t0 > 4 else None)
                if pick is None and words and time.time() - t0 > 2.5:
                    pick = next((h for h in wins if any(w in _title(h).lower() for w in words)), None)
            if pick:
                time.sleep(0.25)
                force_front(pick)
                return _title(pick)
    except Exception:
        log.warning("macfront.front_after_launch failed", exc_info=True)
    return None
