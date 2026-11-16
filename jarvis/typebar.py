"""'Type to JARVIS': a small floating input bar for typed requests.

Opened from the menu bar / tray or the type hotkey. It's the one JARVIS window that takes
keyboard focus, and only while it's open: Enter sends, Esc (or clicking elsewhere) closes,
and focus goes back to the app you were using. Runs on the Tk main thread (HUD.run).
"""
from __future__ import annotations

import logging
import tkinter as tk

from . import osutil
from .config import save_setting

log = logging.getLogger("jarvis")

TITLE = "Type to JARVIS"
BG = "#07131a"
FIELD = "#0b1d26"
CYAN = "#3fd8ff"
CYAN_DIM = "#126a85"
DIM = "#6f8f9a"
WHITE = "#e8fbff"
FONT = "Helvetica Neue" if osutil.IS_MAC else "Segoe UI"
MONO = "Menlo" if osutil.IS_MAC else "Consolas"
PLACEHOLDER = "Ask JARVIS anything…"
WIDTH = 620


class TypeBar:
    def __init__(self, hud, cfg: dict, on_submit):
        self.hud = hud
        self.cfg = cfg
        self.on_submit = on_submit          # on_submit(text, speak)
        self.win: tk.Toplevel | None = None
        self.is_open = False
        self.history: list[str] = []
        self._hist_i = 0

    # ------------------------------------------------------------------ build
    def _build(self):
        root = self.hud.root
        self.win = win = tk.Toplevel(root)
        win.title(TITLE)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=CYAN_DIM)                       # 1 px frame colour
        body = tk.Frame(win, bg=BG, padx=18, pady=12)
        body.pack(fill="both", expand=True, padx=1, pady=1)

        top = tk.Frame(body, bg=BG)
        top.pack(fill="x")
        tk.Label(top, text="J.A.R.V.I.S  //  TYPE", bg=BG, fg=CYAN, font=(MONO, 9, "bold")).pack(side="left")
        tk.Label(top, text="Enter to send  ·  Esc to close", bg=BG, fg=DIM, font=(FONT, 9)).pack(side="right")

        field = tk.Frame(body, bg=FIELD, padx=12, pady=8)
        field.pack(fill="x", pady=(10, 8))
        self.entry = tk.Entry(field, bg=FIELD, fg=WHITE, insertbackground=CYAN, relief="flat",
                              highlightthickness=0, borderwidth=0, font=(FONT, 16))
        self.entry.pack(fill="x")

        bottom = tk.Frame(body, bg=BG)
        bottom.pack(fill="x")
        self.speak_lbl = tk.Label(bottom, bg=BG, fg=DIM, font=(FONT, 9), cursor="hand2")
        self.speak_lbl.pack(side="left")
        self.speak_lbl.bind("<Button-1>", lambda e: self._toggle_speak())
        self.mod = "⌘" if osutil.IS_MAC else "Ctrl+"
        tk.Label(bottom, text=f"↑ ↓ history  ·  {self.mod}S speak on/off", bg=BG, fg=DIM,
                 font=(FONT, 9)).pack(side="right")
        self._paint_speak()

        e = self.entry
        e.bind("<Return>", lambda ev: self._send())
        e.bind("<KP_Enter>", lambda ev: self._send())
        e.bind("<Escape>", lambda ev: self.close())
        e.bind("<Up>", lambda ev: self._recall(-1))
        e.bind("<Down>", lambda ev: self._recall(1))
        e.bind("<Command-s>" if osutil.IS_MAC else "<Control-s>", lambda ev: self._toggle_speak())
        e.bind("<FocusIn>", lambda ev: self._placeholder(False))
        win.bind("<FocusOut>", lambda ev: win.after(150, self._maybe_close))

    def _paint_speak(self):
        on = self.cfg.get("typed_reply_voice", False)
        self.speak_lbl.configure(text=("●  Speak reply: on" if on else "○  Speak reply: off  (shown on the HUD)"),
                                 fg=CYAN if on else DIM)

    def _toggle_speak(self):
        self.cfg["typed_reply_voice"] = not self.cfg.get("typed_reply_voice", False)
        save_setting("typed_reply_voice", self.cfg["typed_reply_voice"])
        self._paint_speak()
        return "break"

    def _placeholder(self, show: bool):
        e = self.entry
        if show and not e.get():
            e.insert(0, PLACEHOLDER)
            e.configure(fg=DIM)
        elif not show and e.get() == PLACEHOLDER and e.cget("fg") == DIM:
            e.delete(0, "end")
            e.configure(fg=WHITE)

    def _recall(self, step: int):
        if not self.history:
            return "break"
        self._hist_i = max(0, min(len(self.history), self._hist_i + step))
        self._placeholder(False)
        self.entry.delete(0, "end")
        if self._hist_i < len(self.history):
            self.entry.insert(0, self.history[self._hist_i])
        return "break"

    # -------------------------------------------------------------- open/close
    def open(self):
        """Main thread only."""
        if self.win is None:
            self._build()
        if self.is_open:
            self._focus()
            return
        if osutil.IS_MAC:
            self.hud._mac_track_front()      # where focus goes back to afterwards
        self.is_open = True
        self.hud.typing_open = True
        root = self.hud.root
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.win.update_idletasks()
        h = self.win.winfo_reqheight()
        self.win.geometry(f"{WIDTH}x{h}+{(sw - WIDTH) // 2}+{int(sh * 0.22)}")
        self.entry.delete(0, "end")
        self.entry.configure(fg=WHITE)
        self._hist_i = len(self.history)
        self._paint_speak()
        self.win.deiconify()
        self.win.lift()
        self._focus()

    def _focus(self):
        """Take keyboard focus for the bar (the user asked for it by opening it)."""
        try:
            if osutil.IS_MAC:
                import AppKit
                AppKit.NSApp.activateIgnoringOtherApps_(True)
                for w in AppKit.NSApp.windows():
                    if str(w.title()) == TITLE:
                        w.setLevel_(AppKit.NSStatusWindowLevel)
                        w.makeKeyAndOrderFront_(None)
            elif osutil.IS_WIN:
                import ctypes
                from .winfront import force_front
                self.win.update_idletasks()
                force_front(ctypes.windll.user32.GetParent(self.win.winfo_id()))
        except Exception:
            log.debug("type bar focus failed", exc_info=True)
        self.win.focus_force()
        self.entry.focus_set()

    def _maybe_close(self):
        """Clicked into another app: just close (don't steal focus back to the old one)."""
        if self.is_open and self.win.focus_get() is None:
            self.close(give_back=False)

    def close(self, give_back: bool = True):
        if not self.is_open:
            return "break"
        self.is_open = False
        self.win.withdraw()
        self.hud.typing_open = False
        if give_back and osutil.IS_MAC:
            self.hud._mac_give_back_focus()
        return "break"

    def _send(self):
        text = self.entry.get().strip()
        if text == PLACEHOLDER:
            text = ""
        if not text:
            return "break"
        if not self.history or self.history[-1] != text:
            self.history = (self.history + [text])[-50:]
        speak = bool(self.cfg.get("typed_reply_voice", False))
        self.close()
        try:
            self.on_submit(text, speak)
        except Exception:
            log.exception("typed request failed to queue")
        return "break"
