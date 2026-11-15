"""Entry point: python -m jarvis   (use pythonw for no console window)."""
from __future__ import annotations

import ctypes
import logging
import logging.handlers
import os
import sys
import threading
import time

import psutil

from .config import LOG_PATH, load_config, save_setting


def setup_logging():
    LOG_PATH.parent.mkdir(exist_ok=True)
    h = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2,
                                             encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)
    if sys.stdout and sys.stdout.isatty():
        root.addHandler(logging.StreamHandler(sys.stdout))
    for noisy in ("httpx", "httpx2", "anthropic", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def single_instance() -> bool:
    # Named mutex: a second copy (e.g. autostart + manual launch) exits quietly.
    ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\JARVIS_ASSISTANT_MUTEX")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def make_tray(hud, assistant):
    import pystray
    from PIL import Image, ImageDraw

    colors = {"idle": (40, 150, 190), "standby": (18, 70, 88), "listening": (63, 216, 255), "thinking": (255, 179, 71),
              "speaking": (120, 235, 255), "denied": (255, 77, 94), "error": (255, 77, 94)}

    def icon_for(state: str):
        col = colors.get(state, colors["idle"]) + (255,)
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, 60, 60), outline=col, width=6)
        for a in (0, 120, 240):
            d.arc((14, 14, 50, 50), a, a + 80, fill=col, width=4)
        d.ellipse((26, 26, 38, 38), fill=col)
        return img

    icons = {s: icon_for(s) for s in colors}
    img = icons["idle"]

    def quit_app(icon, _):
        assistant.running = False
        icon.stop()
        hud.sweep("shutdown")          # holographic power-down before the HUD closes
        import time
        time.sleep(1.9)
        hud.quit()

    def talk(icon, _):
        assistant.trigger.set()

    def toggle_voice_lock(icon, item):
        assistant.cfg["require_voice_match"] = not assistant.cfg["require_voice_match"]

    def show_memory(icon, _):
        from .tools import load_memory
        mem = load_memory()
        hud.panel("Memory", [f"- {m}" for m in mem] or ["Nothing stored yet."], 20)

    def scan(icon, _):
        threading.Thread(target=lambda: assistant.toolbox.run("scan_faces", {"delay_seconds": 0}),
                         daemon=True).start()

    def corners(mode):
        def set_mode(icon, _):
            assistant.cfg["screen_corners"] = mode
            save_setting("screen_corners", mode)
        return set_mode

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: f"Status: {hud.state.capitalize()}", None, enabled=False),
        pystray.MenuItem("Talk to JARVIS", talk, default=True),
        pystray.MenuItem("Standby", lambda i, _: threading.Thread(
            target=assistant.set_standby, args=(not assistant.standby,), daemon=True).start(),
            checked=lambda item: getattr(assistant, "standby", False)),
        pystray.MenuItem("Show / hide HUD", lambda i, _: hud.toggle()),
        pystray.MenuItem("Play boot sequence", lambda i, _: threading.Thread(
            target=assistant.boot, kwargs={"replay": True}, daemon=True).start()),
        pystray.MenuItem("Scan screen for faces", scan),
        pystray.MenuItem("Webcam presence", lambda i, _: (
            assistant.cfg.__setitem__("presence_enabled", not assistant.cfg.get("presence_enabled", True)),
            save_setting("presence_enabled", assistant.cfg["presence_enabled"])),
            checked=lambda item: assistant.cfg.get("presence_enabled", True)),
        pystray.MenuItem("Screen corners", pystray.Menu(
            *[pystray.MenuItem(m.capitalize(), corners(m), radio=True,
                               checked=lambda item, m=m: assistant.cfg.get("screen_corners") == m)
              for m in ("active", "always", "off")])),
        pystray.MenuItem("Show memory", show_memory),
        pystray.MenuItem("Guest mode (1 hour)", lambda i, _: assistant.cfg.__setitem__(
            "_guest_until", 0 if time.time() < assistant.cfg.get("_guest_until", 0) else time.time() + 3600),
            checked=lambda item: time.time() < assistant.cfg.get("_guest_until", 0)),
        pystray.MenuItem("Learn my face", lambda i, _: threading.Thread(
            target=lambda: assistant.say_async(assistant.enroll_face()), daemon=True).start()),
        pystray.MenuItem("Sentry photos", lambda i, _: (
            (LOG_PATH.parent / "sentry").mkdir(exist_ok=True), os.startfile(LOG_PATH.parent / "sentry"))),
        pystray.MenuItem("Require my voice", toggle_voice_lock,
                         checked=lambda item: assistant.cfg["require_voice_match"]),
        pystray.MenuItem("New conversation", lambda i, _: assistant.brain.reset()),
        pystray.MenuItem("Open log", lambda i, _: os.startfile(LOG_PATH)),
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("jarvis", img, "JARVIS - standby", menu)
    icon.run_detached()

    def follow_state():  # recolour the tray icon with JARVIS's state (2 checks/sec)
        last = None
        while True:
            s = hud.state
            if s != last:
                try:
                    icon.icon = icons.get(s, icons["idle"])
                    icon.title = f"JARVIS - {s}"
                except Exception:
                    pass
                last = s
            time.sleep(0.5)

    threading.Thread(target=follow_state, daemon=True).start()
    return icon


def main():
    try:  # real pixel coordinates on scaled displays (overlay, clicks, screenshots agree)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    if not single_instance():
        return
    setup_logging()
    log = logging.getLogger("jarvis")
    cfg = load_config()

    # Stay out of the way of games and other foreground work.
    try:
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass

    from .hud import HUD
    hud = HUD(cfg)

    if cfg.get("backend") == "api" and not cfg["api_key_present"]:
        log.error("ANTHROPIC_API_KEY missing; add it to .env or use backend 'claude_code'")

    from .assistant import Assistant
    assistant = Assistant(cfg, hud)

    try:
        import keyboard
        keyboard.add_hotkey(cfg["hotkey"], assistant.trigger.set)
    except Exception as e:
        log.warning("hotkey unavailable: %s", e)

    make_tray(hud, assistant)
    worker = threading.Thread(target=assistant.run, daemon=True)
    worker.start()
    log.info("JARVIS started")
    hud.run()  # Tk main loop on the main thread; returns on Quit
    assistant.running = False
    os._exit(0)


if __name__ == "__main__":
    main()
