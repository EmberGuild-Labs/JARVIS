"""Small cross-platform layer (Windows + macOS).

Everything the rest of JARVIS needs from the operating system that isn't window
management (that's winfront.py / macfront.py). Each function picks the right
implementation for the OS it runs on and fails soft: if something isn't available
it returns a neutral value instead of raising.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("jarvis")

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

OS_NAME = "macOS" if IS_MAC else ("Windows" if IS_WIN else sys.platform)
SHELL_NAME = "zsh" if IS_MAC else "PowerShell"
PRIMARY_MOD = "command" if IS_MAC else "ctrl"   # copy/paste/select-all modifier
SYSTEM_DRIVE = "/" if not IS_WIN else os.environ.get("SystemDrive", "C:") + "\\"


def no_window() -> dict:
    """subprocess kwargs that keep a console window from flashing up (Windows only)."""
    if IS_WIN:
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def default_code_dir() -> str:
    return "C:\\PROJECTS" if IS_WIN else str(Path.home() / "PROJECTS")


def app_name(name_or_path: str) -> str:
    """'C:\\...\\Chrome.exe' / 'Google Chrome' / '/Applications/X.app/...' -> lower-case short name."""
    n = os.path.basename(name_or_path or "").lower()
    for suffix in (".exe", ".app"):
        n = n.removesuffix(suffix)
    return n


# ------------------------------------------------------------------ input / idle
def idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input."""
    try:
        if IS_WIN:
            import ctypes
            from ctypes import wintypes

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
            info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                return 0.0
            return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0
        if IS_MAC:
            import Quartz
            return float(Quartz.CGEventSourceSecondsSinceLastEventType(
                Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGAnyInputEventType))
    except Exception:
        log.debug("idle_seconds failed", exc_info=True)
    return 0.0


# ------------------------------------------------------------------ session lock
def screen_locked() -> bool:
    try:
        if IS_WIN:
            import psutil
            return any(p.info["name"] == "LogonUI.exe" for p in psutil.process_iter(["name"]))
        if IS_MAC:
            import Quartz
            d = Quartz.CGSessionCopyCurrentDictionary() or {}
            return bool(d.get("CGSSessionScreenIsLocked", False))
    except Exception:
        log.debug("screen_locked failed", exc_info=True)
    return False


def lock_screen() -> None:
    if IS_WIN:
        import ctypes
        ctypes.windll.user32.LockWorkStation()
        return
    if IS_MAC:
        try:   # same call as the Control Centre "Lock Screen" item
            import ctypes
            login = ctypes.CDLL("/System/Library/PrivateFrameworks/login.framework/Versions/Current/login")
            login.SACLockScreenImmediate()
            return
        except Exception:
            log.debug("SACLockScreenImmediate failed; sleeping the display", exc_info=True)
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True, timeout=5)


# ------------------------------------------------------------------ foreground app
def _mac_front_window() -> dict | None:
    """Frontmost normal window (layer 0) that isn't JARVIS's own, from the window server."""
    import Quartz
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    me = os.getpid()
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []:
        if w.get("kCGWindowLayer", 0) != 0 or w.get("kCGWindowOwnerPID") == me:
            continue
        b = w.get("kCGWindowBounds") or {}
        if b.get("Width", 0) < 50 or b.get("Height", 0) < 50:
            continue
        return w
    return None


def foreground_app() -> str:
    """Short lower-case name of the app you're using ('chrome', 'claude', 'blender'...)."""
    try:
        if IS_WIN:
            import ctypes
            from ctypes import wintypes
            import psutil
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == os.getpid():
                return ""
            return app_name(psutil.Process(pid.value).exe())
        if IS_MAC:
            w = _mac_front_window()
            if w:
                return _mac_short(w.get("kCGWindowOwnerName", ""))
    except Exception:
        log.debug("foreground_app failed", exc_info=True)
    return ""


def _mac_short(owner: str) -> str:
    n = app_name(owner)
    return {"google chrome": "chrome", "visual studio code": "code", "code": "code"}.get(n, n)


def foreground_pid() -> int:
    try:
        if IS_WIN:
            import ctypes
            from ctypes import wintypes
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(ctypes.windll.user32.GetForegroundWindow(),
                                                          ctypes.byref(pid))
            return pid.value
        if IS_MAC:
            w = _mac_front_window()
            return int(w.get("kCGWindowOwnerPID", 0)) if w else 0
    except Exception:
        pass
    return 0


def foreground_title() -> str:
    """Title of the window you're using ('' if unknown; macOS needs Screen Recording access)."""
    try:
        if IS_WIN:
            import ctypes
            u = ctypes.windll.user32
            h = u.GetForegroundWindow()
            n = u.GetWindowTextLengthW(h)
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, buf, n + 1)
            return buf.value
        if IS_MAC:
            w = _mac_front_window()
            if w:
                t = w.get("kCGWindowName") or ""
                return f"{w.get('kCGWindowOwnerName', '')} - {t}" if t else w.get("kCGWindowOwnerName", "")
    except Exception:
        log.debug("foreground_title failed", exc_info=True)
    return ""


def foreground_rect() -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the window you're using, in screen coordinates."""
    try:
        if IS_WIN:
            import ctypes
            from ctypes import wintypes
            r = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(ctypes.windll.user32.GetForegroundWindow(), ctypes.byref(r))
            return r.left, r.top, r.right, r.bottom
        if IS_MAC:
            w = _mac_front_window()
            if w:
                b = w["kCGWindowBounds"]
                x, y = int(b["X"]), int(b["Y"])
                return x, y, x + int(b["Width"]), y + int(b["Height"])
    except Exception:
        log.debug("foreground_rect failed", exc_info=True)
    return None


# ------------------------------------------------------------------ launching
def open_path(target: str | os.PathLike) -> None:
    """Open a file, folder or URL with its default app."""
    if IS_WIN:
        os.startfile(target)  # type: ignore[attr-defined]
    elif IS_MAC:
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])


def open_in_console(args: list[str], cwd: str) -> None:
    """Run a command in a new visible terminal window (used for voice enrollment)."""
    if IS_WIN:
        subprocess.Popen(args, cwd=cwd, creationflags=subprocess.CREATE_NEW_CONSOLE)
        return
    import shlex
    cmd = f"cd {shlex.quote(cwd)} && " + " ".join(shlex.quote(a) for a in args)
    esc = cmd.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{esc}"',
                      "-e", 'tell application "Terminal" to activate'])


def run_shell(command: str, timeout: float = 60) -> subprocess.CompletedProcess:
    """Run a command in the user's shell (PowerShell on Windows, zsh on macOS)."""
    if IS_WIN:
        args = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    else:   # login shell so Homebrew etc. are on PATH even when started by launchd
        args = ["/bin/zsh", "-lc", command]
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace", **no_window())


def osascript(script: str, timeout: float = 10) -> str:
    """Run AppleScript (macOS) and return its stdout ('' on failure)."""
    if not IS_MAC:
        return ""
    try:
        p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip()
    except Exception:
        log.debug("osascript failed", exc_info=True)
        return ""


# ------------------------------------------------------------------ media / volume
_MAC_KEYS = {"play_pause": 16, "next": 17, "previous": 18,   # NX_KEYTYPE_*
             "volume_up": 0, "volume_down": 1, "mute": 7}


def _mac_media_key(code: int) -> None:
    import Quartz
    from AppKit import NSEvent
    for down in (True, False):
        flags = 0xA00 if down else 0xB00
        ev = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            14, (0, 0), flags, 0, 0, None, 8, (code << 16) | (flags & 0xFF00), -1)
        Quartz.CGEventPost(0, ev.CGEvent())


def media_control(action: str, steps: int = 1) -> None:
    """play_pause | next | previous | volume_up | volume_down | mute."""
    n = max(1, min(steps, 50)) if action.startswith("volume_") else 1
    if IS_MAC:
        if action in ("volume_up", "volume_down"):
            delta = 2 * n * (1 if action == "volume_up" else -1)
            osascript(f"set volume output volume ((output volume of (get volume settings)) + {delta})")
            return
        if action == "mute":
            osascript("set volume output muted (not (output muted of (get volume settings)))")
            return
        _mac_media_key(_MAC_KEYS[action])
        return
    import pyautogui
    key = {"play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack",
           "volume_up": "volumeup", "volume_down": "volumedown", "mute": "volumemute"}[action]
    pyautogui.press(key, presses=n, interval=0.02)


def set_volume(percent: int) -> None:
    percent = max(0, min(100, int(percent)))
    if IS_MAC:
        osascript(f"set volume output volume {percent}")
    else:  # Windows: step from zero with media keys (each ~2%)
        media_control("volume_down", 50)
        if percent:
            media_control("volume_up", percent // 2)


def pause_media() -> None:
    """Pause (not toggle) whatever's playing, where the OS lets us."""
    if IS_MAC:
        for app in ("Spotify", "Music"):
            osascript(f'if application "{app}" is running then tell application "{app}" to pause')
        return
    media_control("play_pause")


# ------------------------------------------------------------------ process
def set_low_priority() -> None:
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if IS_WIN else 10)
    except Exception:
        pass


_lock_handle = None


def single_instance(lock_dir: Path) -> bool:
    """True if this is the only JARVIS running (a second copy should exit quietly)."""
    global _lock_handle
    if IS_WIN:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\JARVIS_ASSISTANT_MUTEX")
        return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    import fcntl
    lock_dir.mkdir(parents=True, exist_ok=True)
    _lock_handle = open(lock_dir / "jarvis.lock", "w")
    try:
        fcntl.flock(_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def python_exe(gui: bool = False) -> str:
    """The venv's python (pythonw on Windows when gui=True)."""
    exe = Path(sys.executable)
    if IS_WIN:
        name = "pythonw.exe" if gui else "python.exe"
        return str(exe.with_name(name))
    return str(exe)
