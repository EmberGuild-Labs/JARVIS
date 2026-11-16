"""Transparent, click-through, always-on-top HUD overlay (Tkinter, no extra deps).

All public methods are thread-safe: they enqueue work for the Tk thread.
Animation only runs while JARVIS is active, so the idle cost is ~0% CPU.
"""
from __future__ import annotations

import ctypes
import os
import logging
import math
import queue
import textwrap
import threading
import time
import tkinter as tk

from . import osutil

IS_MAC, IS_WIN = osutil.IS_MAC, osutil.IS_WIN

KEY = "#010203"            # transparent colour key
CYAN = "#3fd8ff"
CYAN_DIM = "#126a85"
AMBER = "#ffb347"
RED = "#ff4d5e"
WHITE = "#e8fbff"
FONT = "Helvetica Neue" if IS_MAC else "Segoe UI"
MONO = "Menlo" if IS_MAC else "Consolas"
TK_SCALING = 1.25 if IS_MAC else None   # Tk point sizes render small on macOS
BG = "systemTransparent" if IS_MAC else KEY   # canvas/window background that is see-through

W, H = 440, 640
FX_LEN = {"boot": 2.9, "shutdown": 1.8, "scan": 1.0}   # seconds each full-screen sweep lasts
STATE_COLORS = {"idle": CYAN_DIM, "listening": CYAN, "thinking": AMBER,
                "speaking": CYAN, "denied": RED, "error": RED, "boot": CYAN,
                "standby": "#0c3d4d"}


_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                  "Windows.UI.Core.CoreWindow", "TkTopLevel"}

# Everyday apps that are often full-screen but are not games: the HUD stays visible over them.
_NOT_GAMES = {
    "claude", "chrome", "firefox", "msedge", "opera", "brave", "code", "cursor", "devenv",
    "explorer", "windowsterminal", "wt", "powershell", "pwsh", "cmd", "conhost", "notepad",
    "notepad++", "discord", "spotify", "slack", "teams", "ms-teams", "zoom", "obs64", "vlc",
    "mpc-hc64", "winword", "excel", "powerpnt", "onenote", "outlook", "olk", "acrobat",
    "acrord32", "blender", "applicationframehost", "steam", "steamwebhelper", "photos",
    "snippingtool", "mspaint", "figma", "notion", "obsidian", "pythonw", "python",
    "lghub", "nvidia overlay", "systemsettings", "taskmgr", "textinputhost", "unity hub",
    "unity",
    # macOS app names (osutil.app_name of the owner name)
    "finder", "safari", "terminal", "iterm2", "iterm", "preview", "notes", "mail", "messages",
    "music", "textedit", "xcode", "system settings", "system preferences", "activity monitor",
    "quicktime player", "facetime", "calendar", "pages", "numbers", "keynote", "arc", "ghostty",
    "warp", "alacritty", "kitty", "wezterm", "dock", "windowserver", "control center",
}
_extra_not_games: set[str] = set()   # filled from config "hud_not_games"


def _mac_game_in_foreground() -> bool:
    fg = osutil.foreground_app()
    if not fg or fg in ("python", "python3", "jarvis"):
        return False
    if fg in _NOT_GAMES or fg in _extra_not_games:
        return False
    try:
        import psutil
        exe = psutil.Process(osutil.foreground_pid()).exe().lower()
    except Exception:
        exe = ""
    if "/steamapps/common/" in exe:
        return True
    rect = osutil.foreground_rect()
    if not rect:
        return False
    l, t, r, b = rect
    try:
        import mss
        with mss.MSS() as m:
            mons = [dict(x) for x in m.monitors[1:]]
    except Exception:
        return False
    for d in mons:
        ml, mt, mr, mb = d["left"], d["top"], d["left"] + d["width"], d["top"] + d["height"]
        # centre of the window decides which display it is on
        if not (ml <= (l + r) // 2 < mr and mt <= (t + b) // 2 < mb):
            continue
        # a true full-screen window also covers the menu-bar strip (maximised windows don't)
        return l <= ml and t <= mt and r >= mr and b >= mb
    return False


def game_in_foreground() -> bool:
    """True when the foreground app is a game: anything installed under Steam's
    steamapps folder, or any window covering its whole monitor (full-screen or
    borderless). The desktop, taskbar and JARVIS's own windows don't count."""
    if IS_MAC:
        return _mac_game_in_foreground()

    from ctypes import wintypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    cls = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, cls, 128)
    if cls.value in _SHELL_CLASSES:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return False
    exe = ""
    try:
        import psutil
        exe = psutil.Process(pid.value).exe().lower()
    except Exception:
        pass
    name = os.path.basename(exe).removesuffix(".exe")
    # Apps that aren't games, even when installed through Steam (Steam's Blender, Wallpaper Engine...)
    if name in _NOT_GAMES or name in _extra_not_games:
        return False
    if "\\steamapps\\common\\" in exe:
        return True
    if user32.IsZoomed(hwnd):  # a maximized app (e.g. Chrome) is not a game
        return False
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    mon = user32.MonitorFromWindow(hwnd, 2)
    if not user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return False
    m = mi.rcMonitor
    return (rect.left <= m.left and rect.top <= m.top and
            rect.right >= m.right and rect.bottom >= m.bottom)


class HUD:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        _extra_not_games.update(n.lower().removesuffix(".exe") for n in cfg.get("hud_not_games", []))
        self.enabled = cfg.get("hud_enabled", True)
        self.q: queue.Queue = queue.Queue()
        self.state = "idle"
        self.level = 0.0
        self.transcript = ""
        self.reply = ""
        self.status_text = ""
        self.panel_data: tuple[str, list[str], float] | None = None
        self.text_until = 0.0
        self.visible = True
        self._t0 = time.time()
        self.root: tk.Tk | None = None
        self.vx = self.vy = 0                       # virtual-desktop origin
        self.screens: list[tuple[int, int, int, int]] = []  # per monitor, overlay coords
        self.boot_checks: list[tuple[str, bool]] = []
        self.voice_source = None   # callable -> samples JARVIS is speaking right now
        self.game_hidden = False   # auto-hidden while a game / full-screen app is in front
        self._game_check = 0.0
        self.boot_t0 = 0.0
        self.fx: tuple[str, float] | None = None       # full-screen sweep (kind, start)
        self.timers: dict[str, tuple[str, float, float]] = {}
        self.scan: dict | None = None                    # diagnostic readout
        self.wire: dict | None = None                    # rotating wireframe
        self.img: dict | None = None                     # picture panel
        self.typing_open = False                         # "Type to JARVIS" bar has focus
        self.typebar = None

    def _desktop(self) -> tuple[int, int, int, int]:
        """Virtual desktop spanning every monitor; also records each screen's rectangle."""
        try:
            import mss
            with mss.MSS() as m:
                mons = [dict(x) for x in m.monitors]
        except Exception:
            w, h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            mons = [{"left": 0, "top": 0, "width": w, "height": h}] * 2
        full = mons[0]
        self.vx, self.vy = full["left"], full["top"]
        self.screens = [(d["left"] - self.vx, d["top"] - self.vy, d["width"], d["height"])
                        for d in mons[1:]]
        return full["left"], full["top"], full["width"], full["height"]

    # ------------------------------------------------------- thread-safe API
    def set_state(self, state: str):
        self.q.put(("state", state))

    def set_level(self, level: float):
        self.level = level  # read by the animation loop; no queue needed

    def heard(self, text: str):
        self.q.put(("heard", text))

    def say(self, text: str):
        self.q.put(("reply", text))

    def status(self, text: str):
        self.q.put(("status", text))

    def panel(self, title: str, lines: list[str], seconds: int):
        self.q.put(("panel", (title, lines, time.time() + seconds)))

    def set_visible(self, on: bool):
        self.q.put(("visible", on))

    def toggle(self):
        self.q.put(("toggle", None))

    def hide_for_capture(self, hide: bool):
        """Hide during screenshots so Claude sees the real screen. Blocks briefly."""
        if self.root is None:
            return
        done = threading.Event()
        self.q.put(("capture", (hide, done)))
        done.wait(1.0)

    def boot(self, checks: list[tuple[str, bool]]):
        """Start-up sequence: rings spin up while system checks tick off."""
        self.q.put(("boot", checks))

    def quit(self):
        self.q.put(("quit", None))

    def mark_faces(self, boxes: list[tuple[int, int, int, int]], seconds: float = 8):
        """Lock-on brackets around faces. Boxes are (x, y, w, h) in screen pixels.
        Deliberately no labels: detection only, no identification."""
        self.q.put(("layer", ([{"x": x - self.vx, "y": y - self.vy, "w": w, "h": h, "kind": "face"}
                               for x, y, w, h in boxes], seconds)))

    def annotate(self, marks: list[dict], seconds: float = 10):
        """Point things out on screen: marks are {x, y, w, h, label} in screen pixels."""
        self.q.put(("layer", ([dict(m, x=m["x"] - self.vx, y=m["y"] - self.vy, kind="note")
                               for m in marks], seconds)))

    def pointer(self, x: int, y: int, label: str = "", kind: str = "move", hold: float = 1.6):
        """Show JARVIS's own cursor gliding to (x, y) in screen pixels.
        kind: click (ripple), type, keys, scroll, focus, move."""
        self.q.put(("ptr", (int(x) - self.vx, int(y) - self.vy, label, kind, hold)))

    def clear_marks(self):
        self.q.put(("layer", ([], 0)))

    def sweep(self, kind: str = "boot"):
        """Full-screen holographic effect on every monitor: boot | shutdown | scan."""
        if self.cfg.get("hud_sweeps", True):
            self.q.put(("sweep", kind))

    def add_timer(self, tid: str, label: str, end: float, total: float):
        self.q.put(("timer", (tid, label, end, total)))

    def remove_timer(self, tid: str):
        self.q.put(("timer_off", tid))

    def scan_start(self, title: str):
        self.q.put(("scan", ("start", title)))

    def scan_row(self, label: str, value: str, status: str = "ok"):
        """status: ok | warn | bad."""
        self.q.put(("scan", ("row", (label, value, status))))

    def scan_end(self, verdict: str, seconds: float = 30):
        self.q.put(("scan", ("end", (verdict, seconds))))

    def wireframe(self, verts, edges, label: str, seconds: float = 30, mode: str = "full"):
        """Rotating holographic wireframe. verts: Nx3, edges: Mx2 index pairs.
        mode: full (centre of the main screen) or side (compact, right-hand side)."""
        self.q.put(("wire", (verts, edges, label, seconds, mode)))

    def clear_wireframe(self):
        self.q.put(("wire", None))

    def image(self, path: str, title: str, seconds: float = 20):
        """Show a picture (render, reference image) in a framed panel on the HUD."""
        self.q.put(("image", (path, title, seconds)))

    # ----------------------------------------------------------------- Tk side
    def attach_typebar(self, on_submit):
        """on_submit(text, speak) receives typed requests from the 'Type to JARVIS' bar."""
        self._typed_submit = on_submit

    def open_typebar(self):
        """Open the 'Type to JARVIS' bar (any thread)."""
        def go():
            if getattr(self, "typebar", None) is None:
                from .typebar import TypeBar
                self.typebar = TypeBar(self, self.cfg, getattr(self, "_typed_submit", lambda t, s: None))
            self.typebar.open()
        self.call_main(go)

    def call_main(self, fn):
        """Run fn() on the Tk/main thread (needed for AppKit calls on macOS)."""
        self.q.put(("call", fn))

    def run(self, on_ready=None):
        """Run on the main thread. Blocks until quit(). on_ready() is called once, on the
        main thread, after the Tk root exists (used to create the macOS menu-bar item)."""
        self.root = root = tk.Tk()
        root.title("JARVIS HUD")
        if TK_SCALING:
            root.tk.call("tk", "scaling", TK_SCALING)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=BG)
        self._transparent(root)
        root.attributes("-alpha", 0.94)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        global H
        H = max(640, sh - 100)   # tall enough for long replies; the empty part is see-through
        pos = self.cfg.get("hud_position", "top-right")
        x = 20 if "left" in pos else sw - W - 20
        y = 20 if "top" in pos else sh - H - 60
        import tkinter.font as tkfont
        self._text_font = tkfont.Font(root=root, family=FONT, size=10)
        self._panel_font = tkfont.Font(root=root, family=FONT, size=9)
        root.geometry(f"{W}x{H}+{x}+{y}")
        self.c = tk.Canvas(root, width=W, height=H, bg=BG, highlightthickness=0)
        self.c.pack()
        root.update_idletasks()
        if IS_MAC:
            self._mac_app_setup()
        self._click_through(root)

        # Full-screen layer for face brackets / annotations. Withdrawn (zero cost)
        # unless something is being shown.
        self.layer = lay = tk.Toplevel(root)
        lay.overrideredirect(True)
        lay.attributes("-topmost", True)
        lay.title("JARVIS HUD layer")
        lay.configure(bg=BG)
        self._transparent(lay)
        vx, vy, vw, vh = self._desktop()
        lay.geometry(f"{vw}x{vh}+{vx}+{vy}")
        self.lc = tk.Canvas(lay, width=vw, height=vh, bg=BG, highlightthickness=0)
        self.lc.pack()
        lay.update_idletasks()
        self._click_through(lay)
        lay.withdraw()
        self.marks: list[dict] = []
        self.marks_t0 = 0.0
        self.marks_until = 0.0
        self.layer_shown = False
        self.corners_t0 = 0.0
        self.ptr: dict | None = None
        if not self.enabled:
            root.withdraw()
            self.visible = False
        self._redraw()
        root.after(50, self._pump)
        if on_ready:
            def _ready():
                try:
                    on_ready()
                except Exception:
                    import logging
                    logging.getLogger("jarvis").exception("HUD on_ready failed")
            root.after(100, _ready)
        root.mainloop()

    def _transparent(self, win):
        if IS_MAC:
            win.attributes("-transparent", True)
        else:
            win.attributes("-transparentcolor", KEY)

    def _mac_app_setup(self):
        """Menu-bar-style app: no Dock icon, and the overlay never takes keyboard focus.

        Tk's windows normally become the key window when shown, which pulls focus away
        from whatever you're typing in. The HUD is display-only, so its windows refuse
        key/main status, and if macOS activates JARVIS anyway (Tk does on start-up and
        on deiconify) focus is handed straight back to the app you were using."""
        try:
            import AppKit
            import objc
            AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
            tkwin = objc.lookUpClass("TKWindow")
            if not getattr(HUD, "_mac_patched", False):
                # Only the "Type to JARVIS" bar may take keyboard focus (while it's open);
                # every HUD/overlay window stays display-only.
                def canBecomeKeyWindow(self):
                    return str(self.title()) == "Type to JARVIS"

                def canBecomeMainWindow(self):
                    return False
                objc.classAddMethods(tkwin, [
                    objc.selector(canBecomeKeyWindow, selector=b"canBecomeKeyWindow", signature=b"Z@:"),
                    objc.selector(canBecomeMainWindow, selector=b"canBecomeMainWindow", signature=b"Z@:")])
                HUD._mac_patched = True
        except Exception:
            logging.getLogger("jarvis").debug("mac focus patch failed", exc_info=True)
        self._front_pid = 0
        self._mac_give_back_focus()

    def _mac_track_front(self):
        """Remember the last app you used that isn't JARVIS (cheap; runs in the pump)."""
        try:
            import AppKit
            app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is not None and app.processIdentifier() != os.getpid():
                self._front_pid = app.processIdentifier()
        except Exception:
            pass

    def _mac_give_back_focus(self):
        """If JARVIS became the active app, re-activate the app you were using."""
        if getattr(self, "typing_open", False):   # the type bar has focus on purpose
            return
        try:
            import AppKit
            if not AppKit.NSApp.isActive():
                return
            app = (AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(self._front_pid)
                   if getattr(self, "_front_pid", 0) else None)
            if app is not None and not app.isTerminated():
                app.activateWithOptions_(0)
            else:
                AppKit.NSApp.deactivate()
        except Exception:
            pass

    def _show(self, win):
        if IS_MAC:
            self._mac_track_front()
        win.deiconify()
        if IS_MAC:   # Tk may have recreated the NSWindow: re-apply level / click-through
            win.update_idletasks()
            self._click_through(win)
            self._mac_give_back_focus()

    def _mac_windows(self, win=None):
        """NSWindows of our Tk toplevels (matched by title), or all of them when unmatched."""
        import AppKit
        wins = list(AppKit.NSApp.windows())
        if win is not None:
            title = win.title()
            hit = [w for w in wins if str(w.title()) == title]
            if hit:
                return hit
        return [w for w in wins if str(w.title()).startswith("JARVIS HUD")
                or str(w.className()) == "TKWindow"]

    def _click_through(self, win):
        if IS_MAC:
            try:
                import AppKit
                import Quartz
                for w in self._mac_windows(win):
                    w.setIgnoresMouseEvents_(True)
                    w.setLevel_(Quartz.CGShieldingWindowLevel())   # above full-screen apps / menu bar
                    w.setCollectionBehavior_(
                        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                        | AppKit.NSWindowCollectionBehaviorStationary
                        | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
                        | AppKit.NSWindowCollectionBehaviorIgnoresCycle)
                    w.setHasShadow_(False)
                    w.setOpaque_(False)
                    w.setBackgroundColor_(AppKit.NSColor.clearColor())
                    w.setHidesOnDeactivate_(False)
            except Exception:
                pass
            return
        try:
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            GWL_EXSTYLE = -20
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            # layered | transparent (click-through) | toolwindow (no taskbar) | noactivate
            style |= 0x80000 | 0x20 | 0x80 | 0x08000000
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    def _pump(self):
        changed = False
        if IS_MAC:   # never keep keyboard focus: the HUD is display-only
            self._mac_give_back_focus()
            self._mac_track_front()
        try:
            while True:
                kind, val = self.q.get_nowait()
                changed = True
                if kind == "state":
                    self.state = val
                    if val in ("listening", "thinking", "speaking", "boot"):
                        self.text_until = time.time() + 3600
                    elif val in ("idle", "standby"):
                        # long replies stay up long enough to read (and page through)
                        words = len((self.reply or "").split()) + len((self.transcript or "").split())
                        self.text_until = time.time() + max(8, min(90, words / 2.5))
                        self._page_t0 = time.time()
                        self.status_text = ""
                elif kind == "boot":
                    self.boot_checks, self.boot_t0 = val, time.time()
                    self.state = "boot"
                    self.text_until = time.time() + 3600
                    self.transcript = self.reply = ""
                elif kind == "heard":
                    self.transcript, self.reply = val, ""
                elif kind == "reply":
                    self.reply = val
                elif kind == "status":
                    self.status_text = val
                elif kind == "panel":
                    self.panel_data = val
                elif kind == "visible":
                    if val != self.visible:
                        self.visible = val
                        self._show(self.root) if val and not self.game_hidden else self.root.withdraw()
                elif kind == "toggle":
                    self.visible = not self.visible
                    self._show(self.root) if self.visible else self.root.withdraw()
                elif kind == "capture":
                    hide, done = val
                    if self.visible and not self.game_hidden:
                        self.root.withdraw() if hide else self._show(self.root)
                    if self.layer_shown:
                        self.layer.withdraw() if hide else self._show(self.layer)
                    self.root.update()
                    done.set()
                elif kind == "ptr":
                    x, y, label, pkind, hold = val
                    now = time.time()
                    if self.ptr and now < self.ptr["until"]:
                        x0, y0 = self._ptr_pos(now)
                    else:
                        x0, y0 = x + 140, y + 100   # glide in from below-right
                    self.ptr = {"x0": x0, "y0": y0, "x": x, "y": y, "t0": now, "label": label,
                                "kind": pkind, "until": now + 0.35 + hold}
                elif kind == "layer":
                    self.marks, secs = val
                    self.marks_t0 = time.time()
                    self.marks_until = self.marks_t0 + secs
                elif kind == "sweep":
                    self.fx = (val, time.time())
                elif kind == "timer":
                    tid, label, end, total = val
                    self.timers[tid] = (label, end, total)
                elif kind == "timer_off":
                    self.timers.pop(val, None)
                elif kind == "scan":
                    what, arg = val
                    if what == "start":
                        self.scan = {"title": arg, "rows": [], "t0": time.time(), "verdict": "",
                                     "until": time.time() + 120}
                        self.fx = ("scan", time.time())
                    elif self.scan is not None and what == "row":
                        self.scan["rows"].append((*arg, time.time()))
                    elif self.scan is not None and what == "end":
                        self.scan["verdict"], secs = arg
                        self.scan["until"] = time.time() + secs
                elif kind == "wire":
                    self._set_wire(val)
                elif kind == "image":
                    self._set_image(*val)
                elif kind == "call":
                    try:
                        val()
                    except Exception:
                        import logging
                        logging.getLogger("jarvis").exception("call_main failed")
                elif kind == "quit":
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        self._check_game()
        self._sync_layer()
        active = (self.state in ("listening", "thinking", "speaking", "boot")
                  or bool(self.scan and not self.scan["verdict"])
                  or bool(self.img and time.time() - self.img["t0"] < 0.7))
        now = time.time()
        expired = (self.panel_data and now > self.panel_data[2]) or \
                  ((self.transcript or self.reply) and now > self.text_until)
        if expired:
            if self.panel_data and now > self.panel_data[2]:
                self.panel_data = None
            if now > self.text_until:
                self.transcript = self.reply = ""
            changed = True
        if self.scan and now > self.scan["until"]:
            self.scan, changed = None, True
        if self.img and now > self.img["until"]:
            self.img, changed = None, True
        # reply: page flips; timers/scan: countdowns and reveals
        if (active or changed or self.reply or self.timers or self.scan) and self.visible:
            self._redraw()
        # ~30 fps while active, 2 Hz polling while idle
        self.root.after(33 if active else 500, self._pump)

    # ----------------------------------------------------------------- drawing
    def _redraw(self):
        c = self.c
        c.delete("all")
        t = time.time() - self._t0
        color = STATE_COLORS.get(self.state, CYAN)
        idle = self.state in ("idle", "standby")
        cx, cy = W - 90, 90
        has_text = bool(self.transcript or self.reply or self.panel_data or self.scan or self.img)
        self._draw_timers()

        if idle and not has_text:
            if self.cfg.get("hud_idle_indicator", True):
                self._ring(cx, cy, 14, color, width=2)
                c.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=color, outline="")
            return

        # ---- reactor rings
        pulse = 1 + (0.12 * self.level if self.state == "listening" else
                     0.05 * math.sin(t * 6) if self.state == "speaking" else 0)
        if self.state == "boot":  # spin-up: fast and slowing to cruise
            bt = time.time() - self.boot_t0
            spin = 40 * bt + 360 * (1 - math.exp(-bt * 1.5)) * 2
            pulse = 0.6 + 0.4 * min(1.0, bt / 0.8)
        else:
            spin = t * (220 if self.state == "thinking" else 40)
        r = 62 * pulse
        self._ring(cx, cy, r + 14, CYAN_DIM, width=1)
        for i in range(3):
            start = spin + i * 120
            c.create_arc(cx - r, cy - r, cx + r, cy + r, start=start, extent=80,
                         style="arc", outline=color, width=4)
        r2 = 44
        for i in range(12):
            a = math.radians(-spin * 0.6 + i * 30)
            c.create_line(cx + r2 * math.cos(a), cy + r2 * math.sin(a),
                          cx + (r2 + 7) * math.cos(a), cy + (r2 + 7) * math.sin(a),
                          fill=color, width=2)
        core = 20 + (10 * self.level if self.state == "listening" else 0)
        c.create_oval(cx - core, cy - core, cx + core, cy + core, outline=color, width=2)
        c.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, fill=color, outline="")
        if self.state == "speaking" and self.voice_source is not None:
            self._voice_wave(cx, cy, r)

        # ---- labels
        label = {"idle": "STANDBY", "listening": "LISTENING", "thinking": "PROCESSING",
                 "speaking": "RESPONDING", "denied": "VOICE NOT RECOGNISED",
                 "error": "ERROR", "boot": "INITIALISING"}.get(self.state, self.state.upper())
        c.create_text(W - 20, 178, text=f"J.A.R.V.I.S  //  {label}", anchor="e",
                      fill=color, font=(MONO, 10, "bold"))
        if self.status_text:
            c.create_text(W - 20, 196, text=self.status_text[:60], anchor="e",
                          fill=CYAN_DIM, font=(MONO, 9))

        y = 222
        if self.state == "boot":
            self._boot_list(y)
            return
        if self.transcript:
            y = self._block(y, "YOU", self.transcript, WHITE, 10)
        if self.reply:
            room = (H - y - 40) // 18 - 2
            if self.panel_data:
                room -= 6
            y = self._block(y, "JARVIS", self.reply, CYAN, max(6, room), page=True)
        if self.scan:
            y = self._draw_scan(y + 8)
        if self.img:
            y = self._draw_image(y + 8)
        if self.panel_data:
            self._panel(y + 8, *self.panel_data[:2])

    def _set_image(self, path, title, seconds):
        try:
            from PIL import Image, ImageTk
            im = Image.open(path).convert("RGB")
            im.thumbnail((W - 52, 300))
            self.img = {"photo": ImageTk.PhotoImage(im, master=self.root), "title": title,
                        "h": im.height, "w": im.width, "until": time.time() + seconds, "t0": time.time()}
        except Exception:
            self.img = None

    def _draw_image(self, y):
        c, im = self.c, self.img
        h = im["h"] + 40
        self._frame(20, y, W - 20, y + h, accent=True)
        c.create_text(32, y + 12, text=im["title"].upper()[:40], anchor="w", fill=AMBER,
                      font=(MONO, 9, "bold"))
        c.create_image(W // 2, y + 26 + im["h"] // 2, image=im["photo"])
        age = time.time() - im["t0"]
        if age < 0.6:   # a scan line wipes over the picture as it appears
            sy = y + 26 + im["h"] * age / 0.6
            c.create_line(26, sy, W - 26, sy, fill=CYAN, width=2)
        return y + h + 4

    # ------------------------------------------------------- timers / scan
    def _draw_timers(self):
        """Countdown rings to the left of the reactor; they pulse in the last 10 seconds."""
        c, now = self.c, time.time()
        for i, (tid, (label, end, total)) in enumerate(sorted(self.timers.items(), key=lambda kv: kv[1][1])[:3]):
            left = max(0.0, end - now)
            x, y, r = 205 - i * 80, 64, 27
            urgent = left <= 10
            col = (AMBER if int(now * 4) % 2 else WHITE) if urgent else CYAN
            c.create_oval(x - r, y - r, x + r, y + r, outline=CYAN_DIM, width=1)
            frac = left / total if total else 0
            if frac > 0.001:
                c.create_arc(x - r, y - r, x + r, y + r, start=90, extent=359.9 * frac,
                             style="arc", outline=col, width=4)
            for k in range(12):   # tick marks
                a = math.radians(k * 30)
                c.create_line(x + (r + 4) * math.cos(a), y + (r + 4) * math.sin(a),
                              x + (r + 7) * math.cos(a), y + (r + 7) * math.sin(a), fill=CYAN_DIM)
            m, s = divmod(int(left + 0.999), 60)
            h, m = divmod(m, 60)
            txt = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
            c.create_text(x, y, text=txt, fill=WHITE, font=(MONO, 9 if h else 10, "bold"))
            c.create_text(x, y + r + 16, text=label, fill=col if urgent else CYAN_DIM, font=(MONO, 8, "bold"))

    def _draw_scan(self, y):
        """Diagnostic readout: rows type in as checks finish, with a scanning bar."""
        c, sc, now = self.c, self.scan, time.time()
        rows = sc["rows"]
        h = 34 + 18 * len(rows) + (26 if sc["verdict"] else 18)
        self._frame(20, y, W - 20, y + h, accent=True)
        c.create_text(32, y + 12, text=sc["title"].upper()[:40], anchor="w", fill=AMBER,
                      font=(MONO, 9, "bold"))
        cols = {"ok": CYAN, "warn": AMBER, "bad": RED}
        tags = {"ok": "[ OK ]", "warn": "[WARN]", "bad": "[FAIL]"}
        for i, (label, value, status, t) in enumerate(rows):
            yy = y + 32 + 18 * i
            age = now - t
            n = int(age * 80) + 1
            c.create_text(32, yy, text=label.upper()[:14], anchor="w", fill=CYAN_DIM, font=(MONO, 8, "bold"))
            c.create_text(140, yy, text=str(value)[:n][:34], anchor="w", fill=WHITE, font=(MONO, 9))
            if age > 0.25:
                c.create_text(W - 32, yy, text=tags.get(status, "[ OK ]"), anchor="e",
                              fill=cols.get(status, CYAN), font=(MONO, 9, "bold"))
        by = y + 32 + 18 * len(rows)
        if sc["verdict"]:
            c.create_text(32, by + 4, text=sc["verdict"].upper()[:48], anchor="w", fill=AMBER,
                          font=(MONO, 9, "bold"))
        else:   # still scanning: a bar sweeping back and forth
            p = (math.sin((now - sc["t0"]) * 3) + 1) / 2
            x = 32 + p * (W - 104)
            c.create_line(32, by, W - 32, by, fill=CYAN_DIM)
            c.create_rectangle(x, by - 2, x + 40, by + 2, fill=CYAN, outline="")
        return y + h + 4

    def _wrap(self, text: str, font, width: int) -> list[str]:
        """Wrap by real pixel width, keeping the reply's own line breaks."""
        out: list[str] = []
        for para in str(text).splitlines() or [""]:
            words, line = para.split(), ""
            if not words:
                if out and out[-1] != "":
                    out.append("")
                continue
            for w in words:
                cand = f"{line} {w}".strip()
                if line and font.measure(cand) > width:
                    out.append(line)
                    line = w
                else:
                    line = cand
            out.append(line)
        while out and out[-1] == "":
            out.pop()
        return out

    # ------------------------------------------------------ full-screen layer
    def _corners_wanted(self) -> bool:
        mode = self.cfg.get("screen_corners", "active")  # always | active | off
        if self.state == "standby" or self.cfg.get("_mode") == "game":
            return False
        if mode == "always":
            return self.visible
        if mode == "active":
            return self.state in ("listening", "thinking", "speaking", "denied", "boot")
        return False

    def _check_game(self):
        now = time.time()
        if now - self._game_check < 2:
            return
        self._game_check = now
        try:
            game = self.cfg.get("hide_in_games", True) and game_in_foreground()
        except Exception:
            game = False
        if game == self.game_hidden:
            return
        self.game_hidden = game
        if game:
            self.root.withdraw()
            if self.layer_shown:
                self.lc.delete("all")
                self.layer.withdraw()
                self.layer_shown = False
        elif self.visible:
            self._show(self.root)

    def _layer_wanted(self) -> bool:
        if self.game_hidden:
            return False
        if self.marks and time.time() > self.marks_until:
            self.marks = []
        if self.ptr and time.time() > self.ptr["until"]:
            self.ptr = None
        if self.fx and time.time() - self.fx[1] > FX_LEN.get(self.fx[0], 1.5):
            self.fx = None
        if self.wire and time.time() > self.wire["until"]:
            self.wire = None
        return (bool(self.marks) or bool(self.ptr) or bool(self.fx) or bool(self.wire)
                or self._corners_wanted())

    def _sync_layer(self):
        """Called from the pump: show/hide the full-screen layer as needed."""
        if self._layer_wanted() and not self.layer_shown:
            self.corners_t0 = time.time()
            self._show(self.layer)
            self.layer_shown = True
            self._layer_tick()

    def _layer_tick(self):
        if not self._layer_wanted():
            self.lc.delete("all")
            self.layer.withdraw()
            self.layer_shown = False
            return
        self._draw_layer(time.time() - self.marks_t0)
        animating = self.marks or self.ptr or self.fx or time.time() - self.corners_t0 < 0.5
        self.root.after(33 if animating else 50 if self.wire else 250, self._layer_tick)

    def _draw_corners(self, sw, sh, ox=0, oy=0):
        """Right-angle brackets in the four screen corners."""
        c = self.lc
        p = min(1.0, (time.time() - self.corners_t0) / 0.4)
        ease = 1 - (1 - p) ** 3
        m, k = 14, 70 * ease
        col = STATE_COLORS.get(self.state, CYAN) if self.state != "idle" else CYAN_DIM
        for (x, y, dx, dy) in ((ox + m, oy + m, 1, 1), (ox + sw - m, oy + m, -1, 1), (ox + m, oy + sh - m, 1, -1),
                               (sw - m, sh - m, -1, -1)):
            c.create_line(x, y, x + k * dx, y, fill=col, width=3)
            c.create_line(x, y, x, y + k * dy, fill=col, width=3)
            c.create_line(x + 8 * dx, y + 8 * dy, x + 8 * dx + k * 0.4 * dx, y + 8 * dy,
                          fill=CYAN_DIM, width=1)
            c.create_line(x + 8 * dx, y + 8 * dy, x + 8 * dx, y + 8 * dy + k * 0.4 * dy,
                          fill=CYAN_DIM, width=1)

    def _draw_layer(self, t: float):
        c = self.lc
        c.delete("all")
        sw, sh = self.layer.winfo_width(), self.layer.winfo_height()
        if self._corners_wanted():
            for (ox, oy, w, h) in self.screens:
                self._draw_corners(w, h, ox, oy)
        if self.wire:
            self._draw_wire(time.time())
        if self.fx:
            for scr in self.screens:
                self._draw_fx(*scr)
        if self.ptr:
            self._draw_pointer(time.time())
        if not self.marks:
            return
        # one-time sweep line as the "scan" happens
        if t < 0.6:
            y = int(sh * t / 0.6)
            c.create_line(0, y, sw, y, fill=CYAN, width=2)
            c.create_line(0, y - 6, sw, y - 6, fill=CYAN_DIM, width=1)
        for i, m in enumerate(self.marks):
            p = min(1.0, max(0.0, (t - 0.25 - i * 0.09) / 0.35))
            if p <= 0:
                continue
            if m["kind"] == "face":
                self._face_box(m, p, t)
            else:
                self._note_box(m, p, t)

    # ------------------------------------------------- holographic effects
    def _draw_fx(self, ox, oy, w, h):
        """Boot / shutdown / scan sweeps. Lines only: everything else stays see-through."""
        c = self.lc
        kind, t0 = self.fx
        t = time.time() - t0
        primary = (ox, oy, w, h) == (self.screens[0] if self.screens else None)
        cx, cy = ox + w / 2, oy + h / 2
        ease = lambda p: 1 - (1 - min(1.0, max(0.0, p))) ** 3
        if kind in ("boot", "scan"):
            dur = 1.1 if kind == "boot" else 0.8
            if t < dur + 0.5:
                y = oy + h * ease(t / dur)
                if kind == "boot" and t < dur + 0.3:     # grid left behind by the scan line
                    grid = "#0b3a4a" if t < dur else "#072630"
                    for gy in range(int(oy), int(min(y, oy + h)), 90):
                        c.create_line(ox, gy, ox + w, gy, fill=grid)
                    for gx in range(int(ox), int(ox + w), 90):
                        c.create_line(gx, oy, gx, min(y, oy + h), fill=grid)
                if t < dur:
                    c.create_line(ox, y, ox + w, y, fill=CYAN, width=2)
                    c.create_line(ox, y - 7, ox + w, y - 7, fill=CYAN_DIM, width=1)
                    c.create_line(ox, y - 16, ox + w, y - 16, fill="#0b3a4a", width=1)
            if kind == "boot" and primary and t > 0.5:
                tt = t - 0.5
                fading = tt > 1.6
                col, dim = (CYAN_DIM, "#0b3a4a") if fading else (CYAN, CYAN_DIM)
                R = min(w, h) * 0.13 * ease(tt / 0.7)
                for i in range(3):
                    r = R * (1 - i * 0.22)
                    c.create_arc(cx - r, cy - r, cx + r, cy + r, start=tt * (160 - i * 90) + i * 120,
                                 extent=70 + i * 25, style="arc", outline=col if i != 1 else dim, width=3 - i)
                c.create_oval(cx - R * 0.08, cy - R * 0.08, cx + R * 0.08, cy + R * 0.08, outline=col, width=2)
                name = "J.A.R.V.I.S"
                c.create_text(cx, cy + R + 34, text=name[:int(tt * 14) + 1], fill=col if not fading else dim,
                              font=(MONO, 22, "bold"))
                if tt > 0.9:
                    sub = "ALL SYSTEMS ONLINE"
                    c.create_text(cx, cy + R + 68, text=sub[:int((tt - 0.9) * 30) + 1], fill=WHITE if not fading else dim,
                                  font=(MONO, 11, "bold"))
        elif kind == "shutdown":
            p = ease(t / 0.8)
            if t < 0.8:   # two lines close in from top and bottom
                for y in (oy + h / 2 * p, oy + h - h / 2 * p):
                    c.create_line(ox, y, ox + w, y, fill=CYAN, width=2)
            elif t < 1.3:   # the seam shrinks to a point
                q = ease((t - 0.8) / 0.5)
                half = w / 2 * (1 - q)
                c.create_line(cx - half, cy, cx + half, cy, fill=WHITE, width=2)
            elif t < 1.7:
                r = 6 * (1 - (t - 1.3) / 0.4) + 1
                c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=CYAN, outline="")
            if primary and t < 1.2:
                c.create_text(cx, cy - 40, text="POWERING DOWN", fill=CYAN, font=(MONO, 14, "bold"))

    def _set_wire(self, val):
        if val is None:
            self.wire = None
            return
        import numpy as np
        verts, edges, label, seconds, mode = val
        v = np.asarray(verts, dtype=float).reshape(-1, 3)
        e = np.asarray(edges, dtype=int).reshape(-1, 2)
        e = e[(e >= 0).all(1) & (e < len(v)).all(1)]
        if len(e) > 1800:   # keep it smooth: draw a fixed sample of the edges
            e = e[np.random.default_rng(0).choice(len(e), 1800, replace=False)]
        lo, hi = v.min(0), v.max(0)
        v = v - (lo + hi) / 2
        v /= max(1e-9, float(np.linalg.norm(v, axis=1).max()))
        self.wire = {"v": v, "e": e, "label": label, "t0": time.time(),
                     "until": time.time() + seconds, "zmin": float(v[:, 2].min()),
                     "zmax": float(v[:, 2].max()), "n": (len(verts), len(edges)), "mode": mode}

    def _draw_wire(self, now):
        """A slowly turning wireframe in the middle of the main screen, built up by a
        rising scan line, with a holo-pad ring underneath."""
        import numpy as np
        c, wd = self.lc, self.wire
        ox, oy, sw, sh = self.screens[0] if self.screens else (0, 0, 1920, 1080)
        side = wd["mode"] == "side"
        if side:   # compact, out of the way: right-hand side, below the reactor
            S = min(sw, sh) * 0.12
            cx, cy = ox + sw - S * 1.45 - 20, oy + sh * 0.66
        else:
            S = min(sw, sh) * 0.3
            cx, cy = ox + sw / 2, oy + sh / 2
        t = now - wd["t0"]
        a, tilt = t * 0.5, math.radians(18)
        v = wd["v"]
        x = v[:, 0] * math.cos(a) - v[:, 1] * math.sin(a)
        y = v[:, 0] * math.sin(a) + v[:, 1] * math.cos(a)
        z = v[:, 2]
        depth = y * math.cos(tilt) - z * math.sin(tilt)          # + = away from viewer
        zz = z * math.cos(tilt) + y * math.sin(tilt)
        persp = 1 / (1 + 0.25 * depth)
        px, py = cx + x * S * persp, cy - zz * S * persp
        # holo-pad: an ellipse under the model
        rr = S * 1.05
        base = cy - wd["zmin"] * math.cos(tilt) * S + S * 0.15   # just under the model's base
        c.create_oval(cx - rr, base - rr * 0.18, cx + rr, base + rr * 0.18, outline=CYAN_DIM, width=1)
        c.create_oval(cx - rr * 0.7, base - rr * 0.12, cx + rr * 0.7, base + rr * 0.12, outline="#0b3a4a")
        reveal = wd["zmin"] + (wd["zmax"] - wd["zmin"] + 0.01) * min(1.0, t / 1.4)
        e = wd["e"]
        shown = e[(z[e[:, 0]] <= reveal) & (z[e[:, 1]] <= reveal)]
        dm = (depth[shown[:, 0]] + depth[shown[:, 1]]) / 2
        for (i, j), d in zip(shown.tolist(), dm.tolist()):
            c.create_line(px[i], py[i], px[j], py[j], fill=CYAN if d < 0.15 else CYAN_DIM, width=1)
        if t < 1.5:   # the build-up scan line
            ly = cy - (reveal * math.cos(tilt)) * S
            c.create_line(cx - S * 1.2, ly, cx + S * 1.2, ly, fill=WHITE, width=1)
        title = wd["label"].upper()[:24 if side else 40]
        c.create_text(cx - S * 1.3, cy - S * 1.3, anchor="w", fill=CYAN,
                      text=title if side else f"HOLOGRAPHIC PROJECTION  //  {title}",
                      font=(MONO, 9 if side else 11, "bold"))
        c.create_text(cx - S * 1.3, cy - S * 1.3 + (15 if side else 20), anchor="w", fill=CYAN_DIM,
                      text=f"{wd['n'][0]:,} VERTICES  {wd['n'][1]:,} EDGES", font=(MONO, 8 if side else 9))

    def _face_box(self, m, p, t):
        c = self.lc
        ease = 1 - (1 - p) ** 3
        scale = 1.8 - 0.8 * ease
        cx, cy = m["x"] + m["w"] / 2, m["y"] + m["h"] / 2
        w, h = m["w"] * scale * 1.15, m["h"] * scale * 1.15
        x1, y1, x2, y2 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        locked = p >= 1
        col = CYAN if not locked or int(t * 3) % 2 else WHITE
        k = max(8, min(w, h) * 0.22)
        wd = 2 if min(w, h) < 60 else 3
        for (x, y, dx, dy) in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
            c.create_line(x, y, x + k * dx, y, fill=col, width=wd)
            c.create_line(x, y, x, y + k * dy, fill=col, width=wd)
        if locked:
            tick = max(4, k * 0.35)
            for (x, y, dx, dy) in ((cx, y1, 0, -1), (cx, y2, 0, 1), (x1, cy, -1, 0), (x2, cy, 1, 0)):
                c.create_line(x, y, x + tick * dx, y + tick * dy, fill=CYAN_DIM, width=1)
            r = 3
            c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=CYAN_DIM)

    def _ptr_pos(self, now):
        pt = self.ptr
        p = min(1.0, (now - pt["t0"]) / 0.35)
        e = 1 - (1 - p) ** 3
        return pt["x0"] + (pt["x"] - pt["x0"]) * e, pt["y0"] + (pt["y"] - pt["y0"]) * e

    def _draw_pointer(self, now):
        """JARVIS's cursor: a rotating reticle with a label tag; ripples on click."""
        c, pt = self.lc, self.ptr
        x, y = self._ptr_pos(now)
        age = now - pt["t0"]
        arrived = age >= 0.35
        col = AMBER if pt["kind"] in ("click", "keys") and arrived else CYAN
        r = 15
        spin = age * 300
        for i in range(2):
            c.create_arc(x - r - 6, y - r - 6, x + r + 6, y + r + 6, start=spin + i * 180,
                         extent=110, style="arc", outline=col, width=2)
        c.create_oval(x - r, y - r, x + r, y + r, outline=CYAN_DIM, width=1)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            c.create_line(x + dx * 5, y + dy * 5, x + dx * (r + 2), y + dy * (r + 2), fill=col, width=2)
        c.create_oval(x - 2, y - 2, x + 2, y + 2, fill=WHITE, outline="")
        if arrived and pt["kind"] == "click":
            k = min(1.0, (age - 0.35) / 0.45)
            if k < 1:
                rr = r + 34 * k
                c.create_oval(x - rr, y - rr, x + rr, y + rr, outline=AMBER, width=max(1, int(3 * (1 - k)) + 1))
        if pt["kind"] == "type" and arrived and int(now * 3) % 2:
            c.create_line(x + r + 8, y - 8, x + r + 8, y + 8, fill=WHITE, width=2)  # blinking caret
        if pt["label"]:
            label = pt["label"].upper()[:40]
            tx, ty = x + r + 16, y + r + 10
            tw = 7 * len(label) + 16
            c.create_line(x + r * 0.7, y + r * 0.7, tx, ty - 9, fill=col, width=1)
            c.create_rectangle(tx, ty - 9, tx + tw, ty + 9, fill="#04141c", outline=col)
            c.create_text(tx + 8, ty, text=label, anchor="w", fill=WHITE, font=(MONO, 8, "bold"))

    def _note_box(self, m, p, t):
        c = self.lc
        pad = 6
        x1, y1 = m["x"] - pad, m["y"] - pad
        x2, y2 = m["x"] + m["w"] + pad, m["y"] + m["h"] + pad
        k = max(10, min(x2 - x1, y2 - y1) * 0.2)
        col = AMBER
        for (x, y, dx, dy) in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
            c.create_line(x, y, x + k * dx, y, fill=col, width=3)
            c.create_line(x, y, x, y + k * dy, fill=col, width=3)
        if p < 1:
            c.create_rectangle(x1, y1, x2, y2, outline=CYAN_DIM, dash=(4, 4))
        label = str(m.get("label", ""))[:60]
        if not label:
            return
        n = min(len(label), int(len(label) * p * 1.5) + 1)  # typewriter reveal
        above = y1 > 60
        ly = y1 - 34 if above else y2 + 34
        lx = x1
        tw = 8 * len(label) + 20
        c.create_line(x1 + 12, y1 if above else y2, lx + 12, ly + (12 if above else -12), fill=col, width=2)
        c.create_rectangle(lx, ly - 12, lx + tw, ly + 12, fill="#04141c", outline=col)
        c.create_text(lx + 10, ly, text=label[:n], anchor="w", fill=WHITE, font=(MONO, 10, "bold"))

    def _boot_list(self, y):
        c = self.c
        bt = time.time() - self.boot_t0
        shown = [(i, txt, ok) for i, (txt, ok) in enumerate(self.boot_checks) if bt > 0.5 + 0.32 * i]
        if not shown:
            return
        h = 26 + 19 * len(shown)
        self._frame(20, y, W - 20, y + h)
        c.create_text(32, y + 10, text="SYSTEM CHECK", anchor="w", fill=CYAN_DIM, font=(MONO, 8, "bold"))
        for i, txt, ok in shown:
            age = bt - (0.5 + 0.32 * i)
            n = min(len(txt), int(age * 90) + 1)
            yy = y + 30 + 19 * i
            c.create_text(32, yy, text=txt[:n].upper(), anchor="w", fill=WHITE, font=(MONO, 9))
            if age > 0.22:
                c.create_text(W - 32, yy, text="[ OK ]" if ok else "[WARN]", anchor="e",
                              fill=CYAN if ok else AMBER, font=(MONO, 9, "bold"))

    def _voice_wave(self, cx, cy, r):
        """Radial waveform around the ring that moves with JARVIS's actual voice."""
        try:
            chunk = self.voice_source()
        except Exception:
            chunk = None
        if chunk is None or len(chunk) < 96:
            return
        import numpy as np
        bars = 48
        seg = np.abs(np.asarray(chunk[: len(chunk) // bars * bars], dtype=np.float32)).reshape(bars, -1)
        amp = np.sqrt((seg ** 2).mean(axis=1))
        amp = np.minimum(1.0, amp / (amp.max() + 1e-6) * min(1.0, float(amp.mean()) * 9 + 0.15))
        amp = np.convolve(np.concatenate([amp[-2:], amp, amp[:2]]), np.ones(5) / 5, mode="valid")
        base = r + 20
        for i, a in enumerate(amp):
            ang = math.radians(i * 360 / bars - 90)
            L = 3 + 30 * float(a)
            col = WHITE if a > 0.75 else CYAN
            self.c.create_line(cx + base * math.cos(ang), cy + base * math.sin(ang),
                               cx + (base + L) * math.cos(ang), cy + (base + L) * math.sin(ang),
                               fill=col, width=2)

    def _ring(self, cx, cy, r, color, width=1):
        self.c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=width)

    def _block(self, y, who, text, color, max_lines, page=False):
        lines = self._wrap(text, self._text_font, W - 64)
        tag = who
        if len(lines) > max_lines:
            if page and self.state in ("idle", "standby"):
                # finished: step through the whole reply a page at a time
                n = max_lines - 1
                pages = [lines[i:i + n] for i in range(0, len(lines), n)]
                k = int((time.time() - getattr(self, "_page_t0", 0)) / 7) % len(pages)
                lines = pages[k]
                tag = f"{who}   {k + 1}/{len(pages)}"
            elif page:
                lines = ["…"] + lines[-(max_lines - 1):]   # still talking: follow the newest text
            else:
                lines = lines[:max_lines - 1] + [lines[max_lines - 1] + " …"]
        h = 22 + 18 * len(lines)
        self._frame(20, y, W - 20, y + h)
        self.c.create_text(32, y + 10, text=tag, anchor="w", fill=CYAN_DIM, font=(MONO, 8, "bold"))
        for i, ln in enumerate(lines):
            self.c.create_text(32, y + 28 + 18 * i, text=ln, anchor="w", fill=color, font=(FONT, 10))
        return y + h + 10

    def _panel(self, y, title, lines):
        wrapped = []
        for ln in lines:
            wrapped += self._wrap(str(ln), self._panel_font, W - 64) or [""]
        wrapped = wrapped[: max(1, (H - y - 40) // 17)]
        h = 30 + 17 * len(wrapped)
        self._frame(20, y, W - 20, y + h, accent=True)
        self.c.create_text(32, y + 12, text=title.upper()[:40], anchor="w", fill=AMBER,
                           font=(MONO, 9, "bold"))
        for i, ln in enumerate(wrapped):
            self.c.create_text(32, y + 32 + 17 * i, text=ln, anchor="w", fill=WHITE, font=(FONT, 9))

    def _frame(self, x1, y1, x2, y2, accent=False):
        c = self.c
        # near-black fill (not the key colour) gives a readable glass panel
        c.create_rectangle(x1, y1, x2, y2, fill="#04141c", outline=CYAN_DIM)
        k = 10
        col = AMBER if accent else CYAN
        for (x, y, dx, dy) in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
            c.create_line(x, y, x + k * dx, y, fill=col, width=2)
            c.create_line(x, y, x, y + k * dy, fill=col, width=2)
