"""Entry point: python -m jarvis   (use pythonw for no console window)."""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
import time

from . import osutil
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
    # Windows: named mutex; macOS: flock on a lock file. A second copy (e.g. autostart + manual
    # launch) exits quietly.
    return osutil.single_instance(LOG_PATH.parent)


STATE_COLORS = {"idle": (40, 150, 190), "standby": (18, 70, 88), "listening": (63, 216, 255),
                "thinking": (255, 179, 71), "speaking": (120, 235, 255), "denied": (255, 77, 94),
                "error": (255, 77, 94), "micoff": (130, 130, 130)}


def _tray_image(state: str, size: int = 64):
    from PIL import Image, ImageDraw
    col = STATE_COLORS.get(state, STATE_COLORS["idle"]) + (255,)
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), outline=col, width=6)
    for a in (0, 120, 240):
        d.arc((14, 14, 50, 50), a, a + 80, fill=col, width=4)
    d.ellipse((26, 26, 38, 38), fill=col)
    if state == "micoff":   # slash through the ring: microphone switched off
        d.line((10, 54, 54, 10), fill=(255, 77, 94, 255), width=7)
    return img if size == 64 else img.resize((size, size), Image.LANCZOS)


def _tray_state(hud, assistant) -> str:
    """What the tray / menu-bar icon shows: mic-off wins over the HUD state."""
    return "micoff" if not getattr(assistant, "mic_on", True) else hud.state


# ------------------------------------------------------------------ start at login (macOS)
def _autostart_plist():
    from pathlib import Path
    return Path.home() / "Library" / "LaunchAgents" / "local.jarvis.assistant.plist"


def _autostart_on() -> bool:
    return _autostart_plist().exists()


def _toggle_autostart():
    """Write/remove the LaunchAgent via autostart.sh. Only registers or unregisters it
    for the next login (no bootstrap/bootout), so the running copy keeps going."""
    import subprocess
    from .config import ROOT
    args = ["/bin/bash", str(ROOT / "autostart.sh"), "--no-load"]
    if _autostart_on():
        args.insert(2, "--remove")
    p = subprocess.run(args, capture_output=True, text=True, timeout=20)
    logging.getLogger("jarvis").info("autostart: %s %s", p.stdout.strip(), p.stderr.strip())


def _menu_spec(hud, assistant, stop_tray):
    """The tray / menu-bar menu as plain data, rendered by pystray (Windows) or AppKit (macOS).
    Item: dict(title=str|callable, cb, checked=callable, enabled, default, children=[...]);
    the string "-" is a separator."""

    def quit_app():
        def go():
            assistant.running = False
            stop_tray()
            hud.sweep("shutdown")          # holographic power-down before the HUD closes
            time.sleep(1.9)
            hud.quit()
        threading.Thread(target=go, daemon=True).start()

    def bg(fn, *a, **kw):
        return lambda: threading.Thread(target=fn, args=a, kwargs=kw, daemon=True).start()

    def toggle_voice_lock():
        assistant.cfg["require_voice_match"] = not assistant.cfg["require_voice_match"]

    def show_memory():
        from .tools import load_memory
        mem = load_memory()
        hud.panel("Memory", [f"- {m}" for m in mem] or ["Nothing stored yet."], 20)

    def scan():
        threading.Thread(target=lambda: assistant.toolbox.run("scan_faces", {"delay_seconds": 0}),
                         daemon=True).start()

    def corners(mode):
        def set_mode():
            assistant.cfg["screen_corners"] = mode
            save_setting("screen_corners", mode)
        return set_mode

    def toggle_presence():
        assistant.cfg["presence_enabled"] = not assistant.cfg.get("presence_enabled", True)
        save_setting("presence_enabled", assistant.cfg["presence_enabled"])

    def toggle_guest():
        assistant.cfg["_guest_until"] = 0 if time.time() < assistant.cfg.get("_guest_until", 0) else time.time() + 3600

    def sentry():
        (LOG_PATH.parent / "sentry").mkdir(exist_ok=True)
        osutil.open_path(LOG_PATH.parent / "sentry")

    def toggle_mic():
        threading.Thread(target=assistant.set_mic, args=(not assistant.mic_on,), daemon=True).start()

    items = [
        dict(title=lambda: ("Status: Microphone off" if not getattr(assistant, "mic_on", True)
                            else f"Status: {hud.state.capitalize()}"), cb=None, enabled=False),
        dict(title="Microphone", cb=toggle_mic, checked=lambda: getattr(assistant, "mic_on", True)),
        "-",
        dict(title="Talk to JARVIS", cb=assistant.trigger.set, default=True),
        dict(title="Type to JARVIS…", cb=hud.open_typebar),
        dict(title="Standby", cb=bg(lambda: assistant.set_standby(not assistant.standby)),
             checked=lambda: getattr(assistant, "standby", False)),
        dict(title="Show / hide HUD", cb=hud.toggle),
        dict(title="Play boot sequence", cb=bg(assistant.boot, replay=True)),
        dict(title="Scan screen for faces", cb=scan),
        dict(title="Webcam presence", cb=toggle_presence,
             checked=lambda: assistant.cfg.get("presence_enabled", True)),
        dict(title="Screen corners", children=[
            dict(title=m.capitalize(), cb=corners(m), radio=True,
                 checked=lambda m=m: assistant.cfg.get("screen_corners") == m)
            for m in ("active", "always", "off")]),
        dict(title="Show memory", cb=show_memory),
        dict(title="Guest mode (1 hour)", cb=toggle_guest,
             checked=lambda: time.time() < assistant.cfg.get("_guest_until", 0)),
        dict(title="Learn my face", cb=bg(lambda: assistant.say_async(assistant.enroll_face()))),
        dict(title="Sentry photos", cb=sentry),
        dict(title="Require my voice", cb=toggle_voice_lock,
             checked=lambda: assistant.cfg["require_voice_match"]),
        dict(title="New conversation", cb=lambda: assistant.brain.reset()),
        dict(title="Open log", cb=lambda: osutil.open_path(LOG_PATH)),
    ]
    if osutil.IS_MAC:
        items.append(dict(title="Start at login", cb=_toggle_autostart, checked=_autostart_on))
    items += ["-", dict(title="Quit", cb=quit_app)]
    return items


def make_tray(hud, assistant):
    """Windows system-tray icon (pystray, its own thread)."""
    import pystray

    icons = {s: _tray_image(s) for s in STATE_COLORS}
    holder: dict = {}

    # pystray counts every positional parameter (defaults included) and rejects
    # callbacks with more than two, so wrap with closures instead of default args.
    def act(cb):
        return lambda icon, item: cb()

    def check(f):
        return lambda item: f()

    def label(t):
        return lambda item: t()

    def build(spec):
        items = []
        for it in spec:
            if it == "-":
                items.append(pystray.Menu.SEPARATOR)
                continue
            t = it["title"]
            if it.get("children"):
                items.append(pystray.MenuItem(t, pystray.Menu(*build(it["children"]))))
                continue
            cb = it.get("cb")
            kw = {}
            if "checked" in it:
                kw["checked"] = check(it["checked"])
            if it.get("radio"):
                kw["radio"] = True
            if it.get("default"):
                kw["default"] = True
            items.append(pystray.MenuItem(label(t) if callable(t) else t, act(cb) if cb else None,
                                          enabled=it.get("enabled", True), **kw))
        return items

    spec = _menu_spec(hud, assistant, lambda: holder["icon"].stop())
    icon = pystray.Icon("jarvis", icons["idle"], "JARVIS - standby", pystray.Menu(*build(spec)))
    holder["icon"] = icon
    icon.run_detached()

    def follow_state():  # recolour the tray icon with JARVIS's state (2 checks/sec)
        last = None
        while True:
            s = _tray_state(hud, assistant)
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


def make_menubar(hud, assistant):
    """macOS menu-bar item (AppKit). Must be called on the main thread, after Tk's root exists
    (Tk runs the NSApplication loop). Titles / check marks refresh each time the menu opens;
    the icon is recoloured on the main thread via hud.call_main."""
    import io

    import AppKit
    import Foundation
    import objc

    def ns_image(state):
        buf = io.BytesIO()
        _tray_image(state, 36).save(buf, "PNG")
        data = Foundation.NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
        img = AppKit.NSImage.alloc().initWithData_(data)
        img.setSize_((18, 18))
        return img

    images = {s: ns_image(s) for s in STATE_COLORS}
    actions: dict[int, object] = {}
    refresh: list = []          # (nsitem, spec) pairs updated in menuNeedsUpdate_
    status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)

    class Target(AppKit.NSObject):
        def fire_(self, sender):
            cb = actions.get(sender.tag())
            if cb:
                try:
                    cb()
                except Exception:
                    logging.getLogger("jarvis").exception("menu action failed")

        def menuNeedsUpdate_(self, menu):
            for nsitem, it in refresh:
                try:
                    t = it["title"]
                    nsitem.setTitle_(t() if callable(t) else t)
                    if "checked" in it:
                        nsitem.setState_(AppKit.NSControlStateValueOn if it["checked"]()
                                         else AppKit.NSControlStateValueOff)
                except Exception:
                    pass

    target = Target.alloc().init()
    counter = [0]

    def build(spec):
        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        menu.setDelegate_(target)
        for it in spec:
            if it == "-":
                menu.addItem_(AppKit.NSMenuItem.separatorItem())
                continue
            t = it["title"]
            title = t() if callable(t) else t
            nsitem = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
            if it.get("children"):
                nsitem.setSubmenu_(build(it["children"]))
            elif it.get("cb"):
                counter[0] += 1
                nsitem.setTag_(counter[0])
                actions[counter[0]] = it["cb"]
                nsitem.setTarget_(target)
                nsitem.setAction_(objc.selector(None, selector=b"fire:", signature=b"v@:@"))
            nsitem.setEnabled_(it.get("enabled", True))
            refresh.append((nsitem, it))
            menu.addItem_(nsitem)
        return menu

    def stop_tray():
        hud.call_main(lambda: AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(status_item))

    status_item.setMenu_(build(_menu_spec(hud, assistant, stop_tray)))
    button = status_item.button()
    button.setImage_(images["idle"])
    button.setToolTip_("JARVIS - standby")

    state = {"last": None}

    def follow_state():
        s = _tray_state(hud, assistant)
        if s != state["last"]:
            state["last"] = s
            button.setImage_(images.get(s, images["idle"]))
            button.setToolTip_("JARVIS - microphone off" if s == "micoff" else f"JARVIS - {s}")

    def poll():  # main thread, 2 checks/sec
        try:
            follow_state()
        except Exception:
            pass
        hud.root.after(500, poll)

    poll()
    return status_item


def _pynput_hotkey(spec: str) -> str:
    """'ctrl+alt+j' -> '<ctrl>+<alt>+j' (pynput GlobalHotKeys syntax)."""
    alias = {"win": "cmd", "windows": "cmd", "super": "cmd", "command": "cmd", "option": "alt",
             "control": "ctrl", "return": "enter", "esc": "esc"}
    parts = []
    for p in spec.lower().replace(" ", "").split("+"):
        p = alias.get(p, p)
        parts.append(p if len(p) == 1 else f"<{p}>")
    return "+".join(parts)


def start_hotkey(cfg, callback, extra: dict | None = None):
    """Global hotkeys: cfg["hotkey"] -> callback, plus {spec: callback} in extra."""
    log = logging.getLogger("jarvis")
    binds = {cfg["hotkey"]: callback, **{k: v for k, v in (extra or {}).items() if k}}
    if osutil.IS_MAC:
        try:
            import Quartz
            if hasattr(Quartz, "CGPreflightListenEventAccess") and not Quartz.CGPreflightListenEventAccess():
                log.warning("global hotkey needs Input Monitoring permission "
                            "(System Settings > Privacy & Security > Input Monitoring)")
            from pynput import keyboard as pk
            listener = pk.GlobalHotKeys({_pynput_hotkey(k): v for k, v in binds.items()})
            listener.daemon = True
            listener.start()
            return listener
        except Exception as e:
            log.warning("hotkey unavailable: %s", e)
        return None
    try:
        import keyboard
        for k, v in binds.items():
            keyboard.add_hotkey(k, v)
    except Exception as e:
        log.warning("hotkey unavailable: %s", e)
    return None


def main():
    if osutil.IS_WIN:
        try:  # real pixel coordinates on scaled displays (overlay, clicks, screenshots agree)
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
    if not single_instance():
        return
    setup_logging()
    log = logging.getLogger("jarvis")
    cfg = load_config()

    # Stay out of the way of games and other foreground work.
    osutil.set_low_priority()

    from .hud import HUD
    hud = HUD(cfg)

    if cfg.get("backend") == "api" and not cfg["api_key_present"]:
        log.error("ANTHROPIC_API_KEY missing; add it to .env or use backend 'claude_code'")

    from .assistant import Assistant
    assistant = Assistant(cfg, hud)

    hud.attach_typebar(assistant.submit_typed)
    start_hotkey(cfg, assistant.trigger.set, {cfg.get("type_hotkey", ""): hud.open_typebar})

    if osutil.IS_MAC:
        on_ready = lambda: make_menubar(hud, assistant)   # needs the Tk root / NSApp on the main thread
    else:
        make_tray(hud, assistant)
        on_ready = None
    worker = threading.Thread(target=assistant.run, daemon=True)
    worker.start()
    log.info("JARVIS started")
    hud.run(on_ready)  # Tk main loop on the main thread; returns on Quit
    assistant.running = False
    os._exit(0)


if __name__ == "__main__":
    main()
