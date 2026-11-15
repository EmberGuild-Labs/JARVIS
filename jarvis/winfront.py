"""Bring a window to the front reliably.

Windows normally refuses SetForegroundWindow from a background process (JARVIS runs
under pythonw with no window of its own), so new windows open behind whatever you're
using. The usual workaround: attach to the foreground thread's input and tap Alt.
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _exe(hwnd) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        import psutil
        return os.path.basename(psutil.Process(pid.value).exe()).lower()
    except Exception:
        return ""


def windows() -> list[int]:
    """Visible, titled, unowned top-level windows in z-order (topmost first)."""
    out: list[int] = []

    def cb(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4)  # GW_OWNER
                and user32.GetWindowTextLengthW(hwnd) > 0):
            cloaked = wintypes.DWORD()
            try:   # suspended Store apps are "visible" but cloaked (not really on screen)
                ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
            except Exception:
                pass
            ex = user32.GetWindowLongW(hwnd, -20)   # skip click-through overlays (HUDs)
            if not cloaked.value and not ex & 0x20:
                out.append(hwnd)
        return True
    user32.EnumWindows(EnumWindowsProc(cb), 0)
    me = os.getpid()

    def pid(h):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(p))
        return p.value
    # not JARVIS's own HUD windows, and not cloaked/system shells
    return [h for h in out if pid(h) != me and _title(h) not in ("Program Manager",)]


def force_front(hwnd) -> bool:
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = user32.GetForegroundWindow()
    cur = kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_thread and fg_thread != cur and user32.AttachThreadInput(cur, fg_thread, True))
    try:
        user32.keybd_event(0x12, 0, 0, 0)       # Alt down/up unlocks SetForegroundWindow
        user32.keybd_event(0x12, 0, 2, 0)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur, fg_thread, False)
    time.sleep(0.05)
    return user32.GetForegroundWindow() == hwnd


def find_window(query: str) -> int | None:
    """'' = the window you're using; else best match on title or app (exe) name."""
    wins = windows()
    if not query.strip():
        fg = user32.GetForegroundWindow()
        return fg if fg in wins else (wins[0] if wins else None)
    q = query.lower().strip()
    for h in wins:
        if q in _title(h).lower():
            return h
    for h in wins:
        if q.replace(" ", "") in _exe(h).removesuffix(".exe"):
            return h
    import difflib
    titles = {_title(h).lower(): h for h in wins}
    close = difflib.get_close_matches(q, titles.keys(), n=1, cutoff=0.35)
    return titles[close[0]] if close else None


def monitors() -> list[tuple[int, int, int, int]]:
    """Work areas (left, top, right, bottom) of each monitor, left to right."""
    out = []
    MonitorEnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                         ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    def cb(hmon, hdc, rect, lp):
        mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        w = mi.rcWork
        out.append((w.left, w.top, w.right, w.bottom))
        return True
    user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(cb), 0)
    return sorted(out)


def _monitor_of(hwnd) -> int:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    mons = monitors()
    for i, (l, t, rr, b) in enumerate(mons):
        if l <= cx < rr and t <= cy < b:
            return i
    return 0


def _place(hwnd, x, y, w, h):
    """Move so the *visible* frame lands on x,y,w,h (Windows 10/11 windows have invisible
    resize borders that SetWindowPos counts)."""
    user32.ShowWindow(hwnd, 9)   # SW_RESTORE (un-maximize first)
    r, f = wintypes.RECT(), wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(f), ctypes.sizeof(f))
        dl, dt_, dr, db = f.left - r.left, f.top - r.top, r.right - f.right, r.bottom - f.bottom
    except Exception:
        dl = dt_ = dr = db = 0
    user32.SetWindowPos(hwnd, 0, x - dl, y - dt_, w + dl + dr, h + dt_ + db, 0x0004 | 0x0040)


_cleared: list[int] = []


def arrange(title: str, action: str) -> str:
    action = action.lower().strip()
    if action == "clear_desk":
        _cleared.clear()
        for h in windows():
            if not user32.IsIconic(h):
                user32.ShowWindow(h, 6)   # SW_MINIMIZE
                _cleared.append(h)
        return f"Desk cleared: {len(_cleared)} windows minimized."
    if action == "restore_desk":
        n = 0
        for h in reversed(_cleared):
            if user32.IsWindow(h):
                user32.ShowWindow(h, 9)
                n += 1
        _cleared.clear()
        return f"Restored {n} windows."
    if action == "side_by_side":
        a, _, b = title.partition("|")
        ha, hb = find_window(a), find_window(b)
        if not ha or not hb:
            return f"Couldn't find {'both windows' if not ha and not hb else (a if not ha else b)}."
        mon = monitors()[_monitor_of(ha)]
        l, t, r, btm = mon
        half = (r - l) // 2
        _place(ha, l, t, half, btm - t)
        _place(hb, l + half, t, r - l - half, btm - t)
        force_front(hb)
        force_front(ha)
        return f"{_title(ha)[:40]} on the left, {_title(hb)[:40]} on the right."
    hwnd = find_window(title)
    if not hwnd:
        return f"No window matching '{title}'."
    mons = monitors()
    cur = _monitor_of(hwnd)
    if action == "next_monitor" or action.startswith("monitor_"):
        if len(mons) < 2:
            return "There's only one monitor."
        target = (cur + 1) % len(mons) if action == "next_monitor" else \
            max(0, min(len(mons) - 1, int(action.split("_")[1]) - 1))
        was_max = bool(user32.IsZoomed(hwnd))
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        (sl, st, sr, sb), (tl, tt, tr, tb) = mons[cur], mons[target]
        w, h = min(r.right - r.left, tr - tl), min(r.bottom - r.top, tb - tt)
        x = tl + int((r.left - sl) / max(1, sr - sl) * (tr - tl))
        y = tt + int((r.top - st) / max(1, sb - st) * (tb - tt))
        _place(hwnd, max(tl, min(x, tr - w)), max(tt, min(y, tb - h)), w, h)
        if was_max:
            user32.ShowWindow(hwnd, 3)
        force_front(hwnd)
        return f"Moved {_title(hwnd)[:50]} to screen {target + 1}."
    l, t, r, b = mons[cur]
    W, H = r - l, b - t
    spots = {"left": (l, t, W // 2, H), "right": (l + W // 2, t, W - W // 2, H),
             "top": (l, t, W, H // 2), "bottom": (l, t + H // 2, W, H - H // 2),
             "center": (l + W // 6, t + H // 8, W * 2 // 3, H * 3 // 4)}
    if action == "maximize":
        user32.ShowWindow(hwnd, 3)
    elif action in spots:
        _place(hwnd, *spots[action])
    else:
        return f"Unknown action {action}."
    force_front(hwnd)
    return f"{_title(hwnd)[:50]}: {action}."


def front_after_launch(before: set[int], exe: str | None = None, hint: str = "",
                       timeout: float = 20.0) -> str | None:
    """After launching something: wait for its window (a new window, else the newest
    window of `exe`, else one whose title contains `hint`) and bring it to the front.
    Returns the window title, or None if nothing showed up."""
    hint = hint.lower().strip()
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.35)
        wins = windows()
        new = [h for h in wins if h not in before]
        if exe:
            pick = next((h for h in new if _exe(h) == exe), None)
            if pick is None and time.time() - t0 > 1.2:   # reused an existing window (new tab)
                pick = next((h for h in wins if _exe(h) == exe), None)
        else:
            # prefer a new window named like the app; any other new window only after a
            # moment (an unrelated pop-up can appear at the same time)
            words = [w for w in hint.split() if len(w) > 2]
            named = [h for h in new if any(w in _title(h).lower() or w in _exe(h) for w in words)]
            pick = named[0] if named else (new[0] if new and time.time() - t0 > 4 else None)
            if pick is None and words and time.time() - t0 > 2.5:
                pick = next((h for h in wins if any(w in _title(h).lower() for w in words)), None)
        if pick:
            time.sleep(0.25)   # let it finish appearing
            force_front(pick)
            return _title(pick)
    return None
