"""PC-control tools that Claude can call."""
from __future__ import annotations

import base64
import datetime as dt
import difflib
import io
import json
import logging
import os
import re
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import psutil

from .config import MEMORY_PATH

log = logging.getLogger("jarvis")

# Commands matching these patterns need a spoken "yes" before they run.
RISKY = re.compile(
    r"\b(remove-item|rm|rmdir|del|erase|rd|format|diskpart|shutdown|restart-computer|"
    r"stop-computer|reg(\.exe)?\s+(add|delete)|set-itemproperty|remove-itemproperty|"
    r"new-itemproperty|bcdedit|cipher|takeown|icacls|set-executionpolicy|stop-process|"
    r"taskkill|uninstall|clear-content|move-item|invoke-webrequest|iwr|curl|wget|"
    r"start-bitstransfer|set-mppreference|netsh|net\s+user|schtasks|sc(\.exe)?\s+(delete|config))\b",
    re.I,
)


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


TOOL_DEFS = [
    {"name": "run_powershell",
     "description": "Run a PowerShell command on the user's Windows PC and return its output. "
                    "Use for anything not covered by a more specific tool (file search, settings "
                    "queries, launching things with arguments, etc.). Destructive commands "
                    "require spoken confirmation from the user, which this tool handles itself.",
     "input_schema": _schema({"command": {"type": "string"},
                              "reason": {"type": "string",
                                         "description": "One short sentence, spoken to the user "
                                                        "if confirmation is needed."}},
                             ["command", "reason"])},
    {"name": "open_application",
     "description": "Open an installed application by (approximate) name, e.g. 'spotify', "
                    "'discord', 'notepad'. Its window is brought to the front unless background "
                    "is true (only when the user says 'in the background' / 'don't switch').",
     "input_schema": _schema({"name": {"type": "string"},
                              "background": {"type": "boolean"}}, ["name", "background"])},
    {"name": "open_url",
     "description": "Open a URL in the user's own Google Chrome, signed in to their normal "
                    "profile, and bring it to the front. Use this for 'pull up', 'open', 'show "
                    "me', 'go to': anything the user wants to look at themselves. For a search, "
                    "open a Google search URL. background=true only when the user asks for it "
                    "to open in the background.",
     "input_schema": _schema({"url": {"type": "string"},
                              "background": {"type": "boolean"}}, ["url", "background"])},
    {"name": "take_screenshot",
     "description": "Capture a screen so you can see what the user sees. Coordinates in the "
                    "returned image can be passed to mouse_click / annotate_screen.",
     "input_schema": _schema({"monitor": {"type": "integer", "description":
                                          "1 = main screen (default), 2+ = other screens, 0 = all screens"}},
                             ["monitor"])},
    {"name": "scan_faces",
     "description": "Detect faces currently visible on the screen (webcam feeds, video, photos, "
                    "image search results) and draw targeting brackets around them on the overlay. "
                    "Returns only how many and roughly where. It does NOT and cannot identify "
                    "anyone. Use for 'is anybody on screen' or after opening an image search.",
     "input_schema": _schema({"delay_seconds": {"type": "integer",
                                                "description": "Wait before scanning, e.g. 3 after opening a page."}},
                             ["delay_seconds"])},
    {"name": "annotate_screen",
     "description": "Point things out on the user's screen: draws labelled brackets around regions. "
                    "Coordinates are in the space of your most recent take_screenshot image. Use "
                    "after take_screenshot when the user says 'look at this' or asks where something is. "
                    "Labels: a few words each.",
     "input_schema": _schema({"marks": {"type": "array", "items": _schema(
                                  {"x": {"type": "integer"}, "y": {"type": "integer"},
                                   "w": {"type": "integer"}, "h": {"type": "integer"},
                                   "label": {"type": "string"}}, ["x", "y", "w", "h", "label"])},
                              "seconds": {"type": "integer"}}, ["marks", "seconds"])},
    {"name": "mouse_click",
     "description": "Click at x,y given in the coordinate space of the most recent screenshot.",
     "input_schema": _schema({"x": {"type": "integer"}, "y": {"type": "integer"},
                              "button": {"type": "string", "enum": ["left", "right", "middle"]},
                              "double": {"type": "boolean"}}, ["x", "y", "button", "double"])},
    {"name": "type_text",
     "description": "Type text with the keyboard into the focused window.",
     "input_schema": _schema({"text": {"type": "string"}}, ["text"])},
    {"name": "press_keys",
     "description": "Press a key or chord, e.g. 'enter', 'ctrl+c', 'alt+tab', 'win+d'.",
     "input_schema": _schema({"keys": {"type": "string"}}, ["keys"])},
    {"name": "scroll",
     "description": "Scroll the mouse wheel. Positive = up, negative = down.",
     "input_schema": _schema({"amount": {"type": "integer"}}, ["amount"])},
    {"name": "list_windows",
     "description": "List titles of open top-level windows.",
     "input_schema": _schema({}, [])},
    {"name": "window_action",
     "description": "Focus, minimize, maximize, restore or close the window whose title best matches.",
     "input_schema": _schema({"title": {"type": "string"},
                              "action": {"type": "string",
                                         "enum": ["focus", "minimize", "maximize", "restore", "close"]}},
                             ["title", "action"])},
    {"name": "media_control",
     "description": "Control media playback and system volume.",
     "input_schema": _schema({"action": {"type": "string",
                                         "enum": ["play_pause", "next", "previous", "volume_up",
                                                  "volume_down", "mute"]},
                              "steps": {"type": "integer", "description": "Repeat count for volume (each ~2%)."}},
                             ["action", "steps"])},
    {"name": "system_status",
     "description": "CPU, memory, disk, battery, uptime and the top processes by CPU/RAM.",
     "input_schema": _schema({}, [])},
    {"name": "show_panel",
     "description": "Show an information panel on the HUD overlay (e.g. weather, a list, a summary). "
                    "Keep lines short. Use this whenever visual info would help.",
     "input_schema": _schema({"title": {"type": "string"},
                              "lines": {"type": "array", "items": {"type": "string"}},
                              "seconds": {"type": "integer", "description": "How long to show it (5-120)."}},
                             ["title", "lines", "seconds"])},
    {"name": "set_timer",
     "description": "Set a timer/reminder; JARVIS will speak the message when it fires.",
     "input_schema": _schema({"seconds": {"type": "integer"}, "message": {"type": "string"}},
                             ["seconds", "message"])},
    {"name": "schedule_task",
     "description": "Schedule something for a specific clock time (persists across restarts). "
                    "action: open_application (target = app name, e.g. 'BeamNG.drive'), open_url "
                    "(target = URL), remind (target = message to speak), or jarvis_request (target = "
                    "a full instruction for you to carry out then, e.g. 'check the weather and tell "
                    "me if I need a coat'). when: local ISO datetime, e.g. 2026-11-14T17:00, worked "
                    "out from the current date/time you were given. For relative delays under an "
                    "hour, set_timer is fine too.",
     "input_schema": _schema({"when": {"type": "string"},
                              "action": {"type": "string",
                                         "enum": ["open_application", "open_url", "remind", "jarvis_request"]},
                              "target": {"type": "string"},
                              "repeat": {"type": "string", "enum": ["none", "daily", "weekdays", "weekly"]},
                              "label": {"type": "string", "description": "Short name, e.g. 'Launch BeamNG.drive'."}},
                             ["when", "action", "target", "repeat", "label"])},
    {"name": "list_scheduled",
     "description": "List upcoming scheduled tasks (also shows them on the HUD).",
     "input_schema": _schema({}, [])},
    {"name": "cancel_scheduled",
     "description": "Cancel scheduled task(s) by id or by a word from the label; 'all' cancels everything.",
     "input_schema": _schema({"which": {"type": "string"}}, ["which"])},
    {"name": "now_playing",
     "description": "What media is playing right now (Spotify, YouTube in a browser, etc.): title, "
                    "artist, album, app, and whether it's playing. Also shows it on the HUD.",
     "input_schema": _schema({}, [])},
    {"name": "take_note",
     "description": "Save a note (timestamped) to the user's JARVIS notes file.",
     "input_schema": _schema({"text": {"type": "string"}}, ["text"])},
    {"name": "read_notes",
     "description": "Read back recent notes (most recent last).",
     "input_schema": _schema({"count": {"type": "integer"}}, ["count"])},
    {"name": "start_dictation",
     "description": "Start dictation mode: after your reply, everything the user says is typed into "
                    "the focused window until they say 'stop dictation'. Spoken 'new line', 'new "
                    "paragraph', 'delete that' are handled.",
     "input_schema": _schema({}, [])},
    {"name": "launch_beamng",
     "description": "Launch BeamNG.drive with its MCP server enabled (-enablemcp) so you can control "
                    "the game. Takes about a minute to come up.",
     "input_schema": _schema({}, [])},
    {"name": "start_background_task",
     "description": "Run a longer job in the background (its own Claude session) so the user can "
                    "keep talking to you meanwhile, e.g. 'build that in the background', 'research X "
                    "and tell me when you're done'. It can't use the mouse/keyboard or ask questions, "
                    "so give complete instructions. JARVIS announces the result when it finishes.",
     "input_schema": _schema({"title": {"type": "string", "description": "A few words, e.g. 'Weather app'."},
                              "instructions": {"type": "string",
                                               "description": "Everything the task needs to know: goal, "
                                                              "where to put results, constraints."},
                              "kind": {"type": "string", "enum": ["coding", "complex", "simple"]}},
                             ["title", "instructions", "kind"])},
    {"name": "list_background_tasks",
     "description": "List background tasks and their status.",
     "input_schema": _schema({}, [])},
    {"name": "cancel_background_task",
     "description": "Cancel running background task(s) by id or title word; 'all' cancels everything.",
     "input_schema": _schema({"which": {"type": "string"}}, ["which"])},
    {"name": "search_history",
     "description": "Search past conversations with the user (kept locally), e.g. 'what was that "
                    "script we made yesterday?'. query: keywords (empty = most recent). days: how far back.",
     "input_schema": _schema({"query": {"type": "string"}, "days": {"type": "integer"}},
                             ["query", "days"])},
    {"name": "run_protocol",
     "description": "Run a named protocol (multi-step routine), e.g. gaming, focus, night, lockdown, "
                    "normal (stand down). list_protocols shows them all.",
     "input_schema": _schema({"name": {"type": "string"}}, ["name"])},
    {"name": "list_protocols",
     "description": "List protocols with their steps.",
     "input_schema": _schema({}, [])},
    {"name": "save_protocol",
     "description": "Create or replace a protocol. steps_json is a JSON list of single-key step objects: "
                    "say, launch, open_url, close (list), minimize_all, power_plan (high|balanced|saver), "
                    "volume (int steps or 'mute'), media (play_pause|pause|next|previous), "
                    "mode (game|focus|normal|standby), lock, panel (list of lines), wait (seconds).",
     "input_schema": _schema({"name": {"type": "string"}, "description": {"type": "string"},
                              "steps_json": {"type": "string"}}, ["name", "description", "steps_json"])},
    {"name": "learn_my_face",
     "description": "Enroll the user's own face from the webcam (about 10 seconds, with spoken "
                    "guidance) so presence and the security sentry know it's them. Only the owner's "
                    "face is ever enrolled; never use this for anyone else.",
     "input_schema": _schema({}, [])},
    {"name": "security_settings",
     "description": "Check or change JARVIS's webcam security: 'status'; 'sentry_on'/'sentry_off' (lock "
                    "the PC when someone who isn't the user is at it); 'auto_lock_on'/'auto_lock_off' (also "
                    "lock after 20 s with nobody in view - off by default because looking down hides the "
                    "face); 'guest_on'/'guest_off' (pause the sentry for an hour for a friend).",
     "input_schema": _schema({"action": {"type": "string", "enum": [
         "status", "sentry_on", "sentry_off", "auto_lock_on", "auto_lock_off", "guest_on", "guest_off"]}},
         ["action"])},
    {"name": "clipboard",
     "description": "Read or write the clipboard.",
     "input_schema": _schema({"action": {"type": "string", "enum": ["read", "write"]},
                              "text": {"type": "string"}}, ["action", "text"])},
    {"name": "read_file",
     "description": "Read a text file (first 20k characters).",
     "input_schema": _schema({"path": {"type": "string"}}, ["path"])},
    {"name": "write_file",
     "description": "Create or overwrite a text file. Asks for spoken confirmation if it already exists.",
     "input_schema": _schema({"path": {"type": "string"}, "content": {"type": "string"}},
                             ["path", "content"])},
    {"name": "remember",
     "description": "Store a long-term fact or preference about the user (shown to you in every future conversation). "
                    "Use action 'forget' with the same text to remove one.",
     "input_schema": _schema({"action": {"type": "string", "enum": ["remember", "forget"]},
                              "fact": {"type": "string"}}, ["action", "fact"])},
    {"name": "list_memory",
     "description": "Show everything you remember about the user on the HUD and return it "
                    "('what do you know about me', 'what do you remember').",
     "input_schema": _schema({}, [])},
    {"name": "set_personality",
     "description": "Change how JARVIS talks, when the user asks ('be more sarcastic', 'less "
                    "jokes', 'call me Tony', 'keep it shorter'). Pass \"\" for anything unchanged. "
                    "humour: off | dry | playful | sarcastic. length: brief | normal | detailed. "
                    "address_as: what to call the user (e.g. 'sir', a name).",
     "input_schema": _schema({"humour": {"type": "string"}, "length": {"type": "string"},
                              "address_as": {"type": "string"}},
                             ["humour", "length", "address_as"])},
    {"name": "run_diagnostic",
     "description": "Full system diagnostic with a HUD scan animation: CPU/GPU temperature and "
                    "load, RAM, every drive's free space, battery, pending Windows updates, "
                    "devices with driver problems, uptime. For 'run a diagnostic', 'system check', "
                    "'how's my PC doing'. Speak a one-line verdict afterwards.",
     "input_schema": _schema({}, [])},
    {"name": "arrange_windows",
     "description": "Arrange windows by voice. action: left (snap to left half), right, top, "
                    "bottom, maximize, center, next_monitor (move to the other screen), "
                    "monitor_1 / monitor_2 / monitor_3, side_by_side (title = 'A|B': A left, B "
                    "right), clear_desk (minimize everything except JARVIS), restore_desk (undo "
                    "clear_desk). title: part of the window title or app name; '' = the window "
                    "the user is using now.",
     "input_schema": _schema({"title": {"type": "string"}, "action": {"type": "string"}},
                             ["title", "action"])},
    {"name": "show_wireframe",
     "description": "Show a slowly rotating holographic wireframe of a 3D model on the HUD. path: "
                    "a .obj file, or a .json file {\"verts\": [[x,y,z],...], \"edges\": [[i,j],...]}. "
                    "For the model open in Blender: first export it with the blender tools (e.g. "
                    "execute_blender_code writing the evaluated mesh's vertices and edges of the "
                    "active/selected object to data/wireframe.json; up to ~4000 edges, decimate "
                    "if bigger), then call this with that path.",
     "input_schema": _schema({"path": {"type": "string"}, "label": {"type": "string"},
                              "seconds": {"type": "integer"}}, ["path", "label", "seconds"])},
    {"name": "open_blender",
     "description": "Make sure Blender is open and its link to you (the mcp__blender__* tools) is "
                    "ready, launching Blender if needed and bringing it to the front. Call this "
                    "before any Blender work, then use the mcp__blender__* tools. Waits up to "
                    "~90 s on a cold start.",
     "input_schema": _schema({"background": {"type": "boolean"}}, ["background"])},
    {"name": "show_image",
     "description": "Show an image file (PNG/JPG) on the HUD for a while, e.g. a Blender viewport "
                    "render you just saved, or a reference image. path: absolute path.",
     "input_schema": _schema({"path": {"type": "string"}, "title": {"type": "string"},
                              "seconds": {"type": "integer"}}, ["path", "title", "seconds"])},
    {"name": "watch_screen",
     "description": "'Watch my screen' mode: for the given minutes, glance at the screen now and "
                    "then (and whenever an error-looking window appears) and only speak up if "
                    "something is wrong: an error dialog, crash, failed build or test, a game that "
                    "crashed. focus: what to watch for in particular ('' = general). minutes 0 "
                    "stops watching.",
     "input_schema": _schema({"minutes": {"type": "integer"}, "focus": {"type": "string"}},
                             ["minutes", "focus"])},
    {"name": "track_progress",
     "description": "Watch a long job and give short spoken progress updates (about every 25%, "
                    "with time left) and say when it's done. kind: window (target = part of a "
                    "window title that shows a percentage, e.g. a Blender render, installer, "
                    "Steam), process (target = process name; announces when it exits), file "
                    "(target = a file path being written/downloaded; done when it stops growing). "
                    "label: what to call it ('the render').",
     "input_schema": _schema({"kind": {"type": "string", "enum": ["window", "process", "file"]},
                              "target": {"type": "string"}, "label": {"type": "string"}},
                             ["kind", "target", "label"])},
]
for t in TOOL_DEFS:
    t["strict"] = True

# Anthropic-hosted web search (runs server-side).
SERVER_TOOLS = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}]


NOTES_PATH = Path.home() / "Documents" / "JARVIS Notes.md"


def find_chrome() -> str | None:
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.environ.get("LOCALAPPDATA", "")):
        exe = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if exe.exists():
            return str(exe)
    return None


def chrome_profile(cfg: dict) -> str | None:
    """The user's own Chrome profile folder (config "chrome_profile", else the one Chrome
    used last), so pages open signed in, never in a blank/guest profile."""
    if cfg.get("chrome_profile"):
        return cfg["chrome_profile"]
    try:
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data" / "Local State"
        prof = json.loads(local.read_text(encoding="utf-8")).get("profile", {})
        return prof.get("last_used") or "Default"
    except Exception:
        return "Default"


def find_beamng() -> Path | None:
    """Locate BeamNG.drive.exe in the Steam libraries."""
    roots = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"]
    lib = roots[0] / "steamapps" / "libraryfolders.vdf"
    try:
        for m in re.finditer(r'"path"\s+"([^"]+)"', lib.read_text(encoding="utf-8", errors="ignore")):
            roots.append(Path(m.group(1).replace("\\\\", "\\")))
    except Exception:
        pass
    for r in roots:
        exe = r / "steamapps" / "common" / "BeamNG.drive" / "BeamNG.drive.exe"
        if exe.exists():
            return exe
    return None


def load_memory() -> list[str]:
    try:
        return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


class ToolBox:
    """Executes tool calls. `confirm(question) -> bool` asks the user aloud;
    `hud` is the overlay; `say(text)` speaks asynchronously (timers)."""

    def __init__(self, confirm, hud, say, cfg: dict):
        self.confirm, self.hud, self.say, self.cfg = confirm, hud, say, cfg
        self.shot_scale = 1.0
        self._apps: dict[str, str] | None = None
        self.scheduler = None  # set by the assistant
        self.dictation_requested = False
        self.shot_origin = (0, 0)
        self.bg = None  # background-task runner (Claude Code backend)
        self.protocols = None
        self.face_enroll = None
        self.progress = None   # ProgressTracker, set by the assistant
        self.screen_watch = None   # ScreenWatcher, set by the assistant

    # ------------------------------------------------------------------ dispatch
    def run(self, name: str, args: dict):
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return f"Unknown tool {name}", True
        try:
            return fn(**args), False
        except Exception as e:
            log.exception("tool %s failed", name)
            return f"Error: {type(e).__name__}: {e}", True

    # ------------------------------------------------------------- AI cursor
    def _cursor(self, x: int, y: int, label: str, kind: str):
        """Show JARVIS's cursor at the target and pause so the user sees it coming."""
        if not self.cfg.get("show_cursor", True) or not hasattr(self.hud, "pointer"):
            return
        self.hud.pointer(x, y, label, kind)
        time.sleep(self.cfg.get("cursor_lead_ms", 420) / 1000)

    @staticmethod
    def _caret_or_mouse() -> tuple[int, int]:
        """Text caret position in the focused window if Windows knows it, else the mouse."""
        import ctypes
        from ctypes import wintypes

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                        ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                        ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                        ("rcCaret", wintypes.RECT)]
        try:
            gti = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
            if ctypes.windll.user32.GetGUIThreadInfo(0, ctypes.byref(gti)) and gti.hwndCaret:
                pt = wintypes.POINT(gti.rcCaret.left, gti.rcCaret.bottom)
                ctypes.windll.user32.ClientToScreen(gti.hwndCaret, ctypes.byref(pt))
                if pt.x or pt.y:
                    return pt.x, pt.y
        except Exception:
            pass
        import pyautogui
        p = pyautogui.position()
        return p.x, p.y

    def _ok(self, question: str) -> bool:
        if not self.cfg.get("confirm_risky_actions", True):
            return True
        return self.confirm(question)

    # ------------------------------------------------------------------- tools
    def t_run_powershell(self, command: str, reason: str):
        if RISKY.search(command) and not self._ok(f"{reason} Shall I proceed?"):
            return "User declined; command not run."
        self.hud.status(f"> {command[:90]}")
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = (p.stdout + ("\nSTDERR:\n" + p.stderr if p.stderr.strip() else "")).strip()
        return f"exit {p.returncode}\n{out[:8000] or '(no output)'}"

    def _scan_apps(self) -> dict[str, str]:
        if self._apps is not None and time.time() - getattr(self, "_apps_t", 0) > 600:
            self._apps = None   # rescan now and then, so newly installed apps are found
        if self._apps is None:
            self._apps_t = time.time()
            apps = {}
            roots = [Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
                     Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
                     Path.home() / "Desktop", Path(os.environ.get("PUBLIC", "")) / "Desktop"]
            for r in roots:
                if r.exists():
                    for f in r.rglob("*"):
                        if f.suffix.lower() in (".lnk", ".url", ".appref-ms"):
                            apps.setdefault(f.stem.lower(), str(f))
            try:   # Microsoft Store apps (Spotify, Xbox...) have no shortcut files
                out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                      "Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }"],
                                     capture_output=True, text=True, timeout=20,
                                     creationflags=subprocess.CREATE_NO_WINDOW).stdout
                for ln in out.splitlines():
                    name, _, appid = ln.partition("|")
                    if name.strip() and "!" in appid:
                        apps.setdefault(name.strip().lower(), "shell:AppsFolder\\" + appid.strip())
            except Exception:
                log.exception("Get-StartApps failed")
            self._apps = apps
        return self._apps

    def _front(self, before, exe=None, hint="", background=False) -> str:
        """Bring what was just opened to the front (in a thread, so the reply isn't held up)."""
        if background or not self.cfg.get("bring_to_front", True):
            return " (left in the background)"
        from .winfront import front_after_launch

        def go():
            title = front_after_launch(before, exe=exe, hint=hint)
            log.info("brought to front: %s", title)
        threading.Thread(target=go, daemon=True).start()
        return " It's being brought to the front."

    def t_open_application(self, name: str, background: bool = False):
        from .winfront import windows
        before = set(windows())
        res = self._open_application(name)
        return res + (self._front(before, hint=name, background=background)
                      if res.startswith("Launched") or "Start-Process" in res else "")

    BUILTIN_APPS = {"calculator": "calc.exe", "calc": "calc.exe", "notepad": "notepad.exe",
                    "paint": "mspaint.exe", "settings": "ms-settings:", "windows settings": "ms-settings:",
                    "task manager": "taskmgr.exe", "file explorer": "explorer.exe",
                    "explorer": "explorer.exe", "snipping tool": "ms-screenclip:",
                    "camera": "microsoft.windows.camera:", "clock": "ms-clock:",
                    "control panel": "control.exe", "command prompt": "cmd.exe",
                    "terminal": "wt.exe", "windows terminal": "wt.exe", "chrome": "chrome",
                    "google chrome": "chrome"}

    DOMAIN = re.compile(r"^(https?://)?([a-z0-9-]+\.)+(com|net|org|io|ai|dev|gg|tv|co|uk|de|se|"
                        r"edu|gov|app|me|us|ca|info|xyz|store|shop|news|blog|tech)(/\S*)?$", re.I)

    def resolve_open(self, what: str) -> tuple[str, str] | None:
        """'microcenter.com' -> ("url", ...); 'steam' -> ("app", "steam"); anything else
        (a search, a vague request) -> None, so Claude decides."""
        w = what.strip().strip(".!?,\"'").strip()
        w = re.sub(r"^(the|my)\s+", "", w, flags=re.I)
        w = re.sub(r"\s+(app|application|program|website|site|page)$", "", w, flags=re.I)
        spoken = re.sub(r"\s+dot\s+", ".", w, flags=re.I)        # "microcenter dot com"
        compact = spoken.replace(" ", "")
        if self.DOMAIN.match(compact):
            return "url", compact
        key = w.lower()
        key = self.SPOKEN_ALIASES.get(key, key)   # common speech-recognition mishearings
        if not key or len(key) > 40 or key in self.GENERIC:
            return None
        if key in self.BUILTIN_APPS:
            return "app", key
        apps = self._scan_apps()
        if key in apps:
            return "app", key
        # "steam" -> "Steam"; "beamng" -> "BeamNG.drive": the app name starts with what was said
        starts = [k for k in apps if k.startswith(key + " ") or k.startswith(key + ".") or k == key]
        if starts:
            return "app", min(starts, key=len)
        close = difflib.get_close_matches(key, apps.keys(), n=1, cutoff=0.88)
        if close:
            return "app", close[0]
        if key in self.KNOWN_SITES:   # an installed app wins; otherwise the website
            return "url", self.KNOWN_SITES[key]
        return None

    SPOKEN_ALIASES = {"keycard": "kicad", "key card": "kicad", "key cad": "kicad", "kai cad": "kicad",
                      "kycad": "kicad", "beam ng": "beamng", "beam and g": "beamng", "beamng drive": "beamng",
                      "blender app": "blender", "v s code": "visual studio code", "vs code": "visual studio code", "vscode": "visual studio code"}

    # Words that name a thing to look up, not an app ("pull up the weather"): Claude handles these.
    GENERIC = {"weather", "news", "calendar", "mail", "email", "music", "photos", "time", "schedule",
               "forecast", "stocks", "scores", "traffic", "notes", "files", "downloads", "documents"}

    KNOWN_SITES = {   # spoken name -> site, for "pull up youtube" when there's no such app
        "youtube": "youtube.com", "gmail": "mail.google.com", "reddit": "reddit.com",
        "amazon": "amazon.com", "netflix": "netflix.com", "twitch": "twitch.tv",
        "github": "github.com", "google": "google.com", "twitter": "x.com", "x": "x.com",
        "facebook": "facebook.com", "instagram": "instagram.com", "linkedin": "linkedin.com",
        "wikipedia": "wikipedia.org", "chatgpt": "chatgpt.com", "chat gpt": "chatgpt.com",
        "claude": "claude.ai", "google maps": "maps.google.com", "maps": "maps.google.com",
        "google drive": "drive.google.com", "google docs": "docs.google.com",
        "google calendar": "calendar.google.com", "ebay": "ebay.com", "micro center": "microcenter.com",
        "microcenter": "microcenter.com", "newegg": "newegg.com", "best buy": "bestbuy.com",
        "tiktok": "tiktok.com", "pinterest": "pinterest.com", "hulu": "hulu.com",
        "disney plus": "disneyplus.com", "outlook": "outlook.live.com", "canvas": "canvas.instructure.com",
    }

    def _open_application(self, name: str):
        apps = self._scan_apps()
        key = name.lower().strip()
        if key in self.BUILTIN_APPS:   # Windows' own apps aren't Start Menu shortcuts
            target = self.BUILTIN_APPS[key]
            if target == "chrome":
                chrome = find_chrome()
                subprocess.Popen([chrome, f"--profile-directory={chrome_profile(self.cfg)}"])
            else:
                os.startfile(target)
            return f"Launched '{key}'."
        match = next((k for k in apps if k == key), None) or \
            next((k for k in apps if key in k), None)
        if not match:
            close = difflib.get_close_matches(key, apps.keys(), n=1, cutoff=0.55)
            match = close[0] if close else None
        if match:
            os.startfile(apps[match])
            return f"Launched '{match}'."
        # fall back to App Paths / PATH / UWP protocol names
        subprocess.Popen(["powershell", "-NoProfile", "-Command", f"Start-Process '{name}'"],
                         creationflags=subprocess.CREATE_NO_WINDOW)
        return f"No Start Menu match; tried Start-Process '{name}'."

    def t_open_url(self, url: str, background: bool = False):
        if not re.match(r"^[a-z]+://", url):
            url = "https://" + url
        chrome = find_chrome()
        from .winfront import windows
        before = set(windows())
        if chrome:  # always Chrome (never Edge), in the user's own signed-in profile
            subprocess.Popen([chrome, f"--profile-directory={chrome_profile(self.cfg)}", url],
                             creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            webbrowser.open(url)
        return f"Opened {url} in Chrome." + self._front(before, exe="chrome.exe", background=background)

    @staticmethod
    def monitors() -> list[dict]:
        """mss monitor list: [0] = whole virtual desktop, [1] = primary, [2..] = others."""
        import mss
        with mss.MSS() as s:
            return [dict(m) for m in s.monitors]

    def _grab(self, monitor: int = 1):
        """Full-resolution capture with the overlays hidden. monitor: 1 = main screen,
        2+ = other screens, 0 = every screen stitched together. Returns (image, left, top)."""
        import mss
        from PIL import Image

        self.hud.hide_for_capture(True)
        try:
            time.sleep(0.12)
            with mss.MSS() as s:
                mons = s.monitors
                mon = mons[monitor] if 0 <= monitor < len(mons) else mons[1]
                raw = s.grab(mon)
        finally:
            self.hud.hide_for_capture(False)
        return Image.frombytes("RGB", raw.size, raw.rgb), mon["left"], mon["top"]

    def t_scan_faces(self, delay_seconds: int):
        import numpy as np
        from .vision import describe_positions, detect_faces

        if delay_seconds > 0:
            time.sleep(min(delay_seconds, 10))  # e.g. let a search page load first
        self.hud.status("SCANNING FOR FACES")
        mons = self.monitors()
        screens = range(1, len(mons))
        boxes, parts = [], []
        t0 = time.time()
        for i in screens:  # every screen, one at a time (keeps faces full-resolution)
            img, left, top = self._grab(i)
            found = detect_faces(np.asarray(img))
            boxes += [(x + left, y + top, w, h) for x, y, w, h in found]
            if found:
                where = describe_positions(found, img.width, img.height)
                parts.append(where if len(mons) <= 2 else f"screen {i}: {where}")
        log.info("face scan: %d faces on %d screen(s) in %.0f ms", len(boxes), len(screens),
                 (time.time() - t0) * 1000)
        if boxes:
            self.hud.mark_faces(boxes, seconds=10)
            return (f"{len(boxes)} face(s) detected and marked on screen, at: {'; '.join(parts)}. "
                    "(Detection only: no identity information is available or to be guessed.)")
        return "No faces detected on screen."

    def _to_screen(self, x: float, y: float) -> tuple[int, int]:
        """Last-screenshot coordinates -> real desktop coordinates."""
        return (int(self.shot_origin[0] + x * self.shot_scale),
                int(self.shot_origin[1] + y * self.shot_scale))

    def t_annotate_screen(self, marks: list[dict], seconds: int):
        s = self.shot_scale
        scaled = []
        for m in marks[:8]:
            x, y = self._to_screen(m["x"], m["y"])
            scaled.append({"x": x, "y": y, "w": int(m["w"] * s), "h": int(m["h"] * s),
                           "label": m.get("label", "")})
        self.hud.annotate(scaled, max(4, min(seconds, 60)))
        return f"Marked {len(scaled)} item(s) on screen."

    def t_take_screenshot(self, monitor: int):
        mons = self.monitors()
        if monitor >= len(mons):
            monitor = 1
        img, left, top = self._grab(monitor)
        target_w = 1366 if monitor else 2048
        self.shot_scale = img.width / target_w if img.width > target_w else 1.0
        self.shot_origin = (left, top)
        if self.shot_scale != 1.0:
            img = img.resize((target_w, int(img.height / self.shot_scale)))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70)
        n = len(mons) - 1
        which = "all screens" if monitor == 0 else ("the main screen" if monitor == 1 else f"screen {monitor}")
        extra = f" The PC has {n} screens; pass monitor=2.. or 0 for all." if n > 1 else ""
        return [
            {"type": "text", "text": f"Screenshot of {which}, {img.width}x{img.height}.{extra}"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(buf.getvalue()).decode()}},
        ]

    def t_mouse_click(self, x: int, y: int, button: str, double: bool):
        import pyautogui
        rx, ry = self._to_screen(x, y)
        label = ("double-click" if double else "click") if button == "left" else f"{button} click"
        self._cursor(rx, ry, label, "click")
        pyautogui.click(rx, ry, clicks=2 if double else 1, button=button)
        return f"Clicked {button} at {rx},{ry}"

    def t_type_text(self, text: str):
        import pyautogui
        x, y = self._caret_or_mouse()
        preview = " ".join(text.split())
        self._cursor(x, y, "typing: " + (preview[:22] + ("..." if len(preview) > 22 else "")), "type")
        pyautogui.write(text, interval=0.01) if text.isascii() else self._paste(text)
        return "Typed."

    def _paste(self, text: str):
        import pyautogui
        import pyperclip
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")

    def t_press_keys(self, keys: str):
        import pyautogui
        alias = {"win": "winleft", "windows": "winleft", "control": "ctrl", "escape": "esc",
                 "return": "enter", "del": "delete"}
        parts = [alias.get(k.strip().lower(), k.strip().lower()) for k in keys.split("+")]
        x, y = self._caret_or_mouse()
        self._cursor(x, y, "keys: " + "+".join(parts), "keys")
        pyautogui.hotkey(*parts)
        return f"Pressed {keys}"

    def t_scroll(self, amount: int):
        import pyautogui
        p = pyautogui.position()
        self._cursor(p.x, p.y, f"scroll {'up' if amount > 0 else 'down'}", "scroll")
        pyautogui.scroll(amount * 120)
        return "Scrolled."

    def t_list_windows(self):
        import pygetwindow as gw
        titles = sorted({t for t in gw.getAllTitles() if t.strip()})
        return "\n".join(titles[:80])

    def t_window_action(self, title: str, action: str):
        import pygetwindow as gw
        titles = [t for t in gw.getAllTitles() if t.strip()]
        lower = {t.lower(): t for t in titles}
        hit = next((t for t in titles if title.lower() in t.lower()), None)
        if not hit:
            close = difflib.get_close_matches(title.lower(), lower.keys(), n=1, cutoff=0.4)
            hit = lower[close[0]] if close else None
        if not hit:
            return f"No window matching '{title}'."
        w = gw.getWindowsWithTitle(hit)[0]
        if action == "close" and not self._ok(f"Close {hit}?"):
            return "User declined."
        try:
            self._cursor(w.left + w.width // 2, w.top + min(40, w.height // 2), f"{action}: {hit[:24]}", "focus")
        except Exception:
            pass
        if action == "focus":
            from .winfront import force_front
            force_front(w._hWnd)
        else:
            getattr(w, action)()
        return f"{action} -> {hit}"

    def t_media_control(self, action: str, steps: int):
        import pyautogui
        key = {"play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack",
               "volume_up": "volumeup", "volume_down": "volumedown", "mute": "volumemute"}[action]
        n = max(1, min(steps, 50)) if action.startswith("volume_") else 1
        pyautogui.press(key, presses=n, interval=0.02)
        return f"{action} x{n}"

    def t_system_status(self):
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
        bat = psutil.sensors_battery()
        up = dt.timedelta(seconds=int(time.time() - psutil.boot_time()))
        procs = []
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                procs.append((p.info["memory_info"].rss, p.info["name"]))
            except Exception:
                pass
        top = sorted(procs, reverse=True)[:6]
        lines = [
            f"CPU {psutil.cpu_percent(interval=0.5)}% ({psutil.cpu_count()} threads)",
            f"RAM {vm.percent}% of {vm.total / 2**30:.0f} GB",
            f"Disk {disk.percent}% used, {disk.free / 2**30:.0f} GB free",
            f"Battery {bat.percent:.0f}% {'charging' if bat.power_plugged else 'on battery'}" if bat else "No battery",
            f"Uptime {up}",
            "Top RAM: " + ", ".join(f"{n} {m / 2**20:.0f}MB" for m, n in top),
        ]
        return "\n".join(lines)

    def t_show_panel(self, title: str, lines: list[str], seconds: int):
        self.hud.panel(title, lines, max(5, min(seconds, 120)))
        return "Panel shown."

    def t_set_timer(self, seconds: int, message: str):
        seconds = max(1, seconds)
        end = time.time() + seconds
        tid = f"t{int(end * 1000)}"
        if hasattr(self.hud, "add_timer"):   # holographic countdown ring next to the reactor
            self.hud.add_timer(tid, _timer_label(message), end, seconds)

        def fire():
            if hasattr(self.hud, "remove_timer"):
                self.hud.remove_timer(tid)
            self.hud.panel("TIMER", [message], 15)
            self.say(message)
        threading.Timer(seconds, fire).start()
        when = (dt.datetime.now() + dt.timedelta(seconds=seconds)).strftime("%H:%M:%S")
        return f"Timer set for {when}."

    def t_schedule_task(self, when: str, action: str, target: str, repeat: str, label: str):
        if self.scheduler is None:
            return "Scheduling isn't available right now."
        task = self.scheduler.add(when, action, target, repeat, label)
        self.hud.panel("Scheduled", [self.scheduler.describe(task)], 8)
        return f"Scheduled: {self.scheduler.describe(task)}"

    def t_list_scheduled(self):
        if self.scheduler is None:
            return "Scheduling isn't available right now."
        items = self.scheduler.listing()
        self.hud.panel("Schedule", items or ["Nothing scheduled."], 15)
        return "\n".join(items) or "Nothing scheduled."

    def t_cancel_scheduled(self, which: str):
        if self.scheduler is None:
            return "Scheduling isn't available right now."
        gone = self.scheduler.cancel(which)
        return (f"Cancelled {len(gone)}: " + "; ".join(t["label"] for t in gone)) if gone \
            else f"Nothing matched '{which}'."

    def t_now_playing(self):
        from .media import now_playing
        info = now_playing()
        if not info:
            return "Nothing is playing (no active media session)."
        lines = [info["title"] or "(untitled)"]
        if info["artist"]:
            lines.append(info["artist"] + (f"  //  {info['album']}" if info["album"] else ""))
        lines.append(f"{info['app']}  //  {info['status']}")
        self.hud.panel("Now playing", lines, 8)
        return json.dumps(info)

    def t_take_note(self, text: str):
        NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        with NOTES_PATH.open("a", encoding="utf-8") as f:
            f.write(f"- [{stamp}] {text.strip()}\n")
        self.hud.panel("Note saved", [text], 6)
        return f"Saved to {NOTES_PATH}"

    def t_read_notes(self, count: int):
        if not NOTES_PATH.exists():
            return "No notes yet."
        lines = [l for l in NOTES_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
        recent = lines[-max(1, min(count, 50)):]
        self.hud.panel("Notes", [l.removeprefix("- ") for l in recent], 20)
        return "\n".join(recent)

    def t_start_dictation(self):
        self.dictation_requested = True
        return "Dictation will start after your reply. Tell the user to go ahead."

    def t_launch_beamng(self):
        exe = find_beamng()
        try:
            if exe:
                subprocess.Popen([str(exe), "-enablemcp"], cwd=str(exe.parent))
                how = "directly with -enablemcp"
            else:
                os.startfile("steam://run/284160//-enablemcp/")
                how = "through Steam with -enablemcp (Steam may ask to confirm the launch option)"
        except Exception as e:
            return f"Couldn't launch BeamNG.drive: {e}"
        return (f"Launching BeamNG.drive {how}. Its MCP tools become available once the game "
                "has loaded (about a minute).")

    def t_start_background_task(self, title: str, instructions: str, kind: str):
        if self.bg is None:
            return "Background tasks need the Claude Code backend."
        msg = self.bg.start_background(title, instructions, kind)
        self.hud.panel("Background task", [title, "Running..."], 6)
        return msg

    def t_list_background_tasks(self):
        if self.bg is None:
            return "Background tasks need the Claude Code backend."
        items = self.bg.list_background()
        self.hud.panel("Background tasks", items or ["None."], 12)
        return "; ".join(items) or "No background tasks."

    def t_cancel_background_task(self, which: str):
        if self.bg is None:
            return "Background tasks need the Claude Code backend."
        gone = self.bg.cancel_background(which)
        return f"Cancelled: {', '.join(gone)}" if gone else f"No running task matched '{which}'."

    def t_search_history(self, query: str, days: int):
        from .history import format_records, search
        recs = search(query, days=days or 30)
        return format_records(recs)

    def t_run_protocol(self, name: str):
        if self.protocols is None:
            return "Protocols aren't available."
        return self.protocols.run(name)

    def t_list_protocols(self):
        from .protocols import load
        data = load()
        self.hud.panel("Protocols", [f"{k}: {v.get('description', '')}" for k, v in data.items()], 15)
        return json.dumps(data, indent=1)[:6000]

    def t_save_protocol(self, name: str, description: str, steps_json: str):
        from .protocols import save
        steps = json.loads(steps_json)
        if not isinstance(steps, list) or not all(isinstance(s, dict) and len(s) == 1 for s in steps):
            return "steps_json must be a JSON list of single-key objects."
        save(name, description, steps)
        return f"Saved the {name} protocol ({len(steps)} steps)."

    def t_learn_my_face(self):
        if self.face_enroll is None:
            return "Face enrollment isn't available."
        return self.face_enroll()

    def t_security_settings(self, action: str):
        from .config import save_setting
        from .face_id import FACEPRINT_PATH
        c = self.cfg
        if action in ("sentry_on", "sentry_off"):
            c["sentry_enabled"] = action == "sentry_on"
            save_setting("sentry_enabled", c["sentry_enabled"])
        elif action in ("auto_lock_on", "auto_lock_off"):
            c["presence_lock"] = action == "auto_lock_on"
            save_setting("presence_lock", c["presence_lock"])
        elif action == "guest_on":
            c["_guest_until"] = time.time() + 3600
        elif action == "guest_off":
            c["_guest_until"] = 0
        guest = time.time() < c.get("_guest_until", 0)
        lines = [
            f"Webcam presence: {'on' if c.get('presence_enabled', True) else 'off'}",
            f"Your face enrolled: {'yes' if FACEPRINT_PATH.exists() else 'no'}",
            f"Sentry (lock for someone else): {'ON' if c.get('sentry_enabled', True) else 'off'}"
            + (" - paused, guest mode" if guest else ""),
            f"Auto-lock when nobody is in view: {'ON (%ss)' % c.get('presence_lock_sec', 20) if c.get('presence_lock') else 'off'}",
        ]
        self.hud.panel("Security", lines, 12)
        return "; ".join(lines)

    def t_clipboard(self, action: str, text: str):
        import pyperclip
        if action == "read":
            return pyperclip.paste()[:8000] or "(clipboard empty)"
        pyperclip.copy(text)
        return "Copied to clipboard."

    def t_read_file(self, path: str):
        p = Path(os.path.expandvars(os.path.expanduser(path)))
        return p.read_text(encoding="utf-8", errors="replace")[:20000]

    def t_write_file(self, path: str, content: str):
        p = Path(os.path.expandvars(os.path.expanduser(path)))
        if p.exists() and not self._ok(f"Overwrite {p.name}?"):
            return "User declined."
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {p}"

    def t_remember(self, action: str, fact: str):
        mem = load_memory()
        if action == "remember":
            if fact not in mem:
                mem.append(fact)
        else:
            close = difflib.get_close_matches(fact, mem, n=1, cutoff=0.4)
            if close:
                mem.remove(close[0])
        MEMORY_PATH.write_text(json.dumps(mem, indent=2), encoding="utf-8")
        self.hud.panel("Memory updated" if action == "remember" else "Forgotten", [fact], 5)
        return f"Memory now has {len(mem)} facts."

    def t_list_memory(self):
        mem = load_memory()
        self.hud.panel("What I know about you", mem or ["Nothing yet."], 25)
        return "\n".join(mem) or "Nothing remembered yet."

    def t_set_personality(self, humour: str, length: str, address_as: str):
        from .config import save_setting
        changed = []
        for key, val, ok in (("persona_humour", humour, ("off", "dry", "playful", "sarcastic")),
                             ("persona_length", length, ("brief", "normal", "detailed"))):
            v = (val or "").strip().lower()
            if v:
                if v not in ok:
                    return f"{key.split('_')[1]} must be one of: {', '.join(ok)}."
                self.cfg[key] = v
                save_setting(key, v)
                changed.append(f"{key.split('_')[1]}: {v}")
        if address_as.strip():
            self.cfg["user_name"] = address_as.strip()
            save_setting("user_name", address_as.strip())
            changed.append(f"address: {address_as.strip()}")
        self.cfg["_persona_changed"] = True   # the brain reloads its instructions next request
        self.hud.panel("Personality", changed or ["No change"], 8)
        return ("Updated " + "; ".join(changed) + ". It takes effect from your next request.") \
            if changed else "Nothing changed."

    # ---------------------------------------------------------- diagnostic
    def t_run_diagnostic(self):
        from .diagnostic import run_diagnostic
        return run_diagnostic(self.hud, self.cfg)

    # ---------------------------------------------------------- windows
    def t_arrange_windows(self, title: str, action: str):
        from .winfront import arrange
        return arrange(title, action)

    # ---------------------------------------------------------- wireframe
    def t_show_wireframe(self, path: str, label: str, seconds: int):
        return self.show_model(path, label, seconds)

    def show_model(self, path: str, label: str, seconds: int, mode: str = "auto") -> str:
        """mode: full (big, centre of the main screen), side (compact, right-hand side) or
        auto: side while you're using the PC (input in the last minute), else full."""
        from .wireframe import load_model
        p = Path(os.path.expandvars(os.path.expanduser(path)))
        if not p.is_absolute():
            from .config import ROOT
            p = ROOT / p
        if not p.exists():
            return f"No file at {p}."
        verts, edges = load_model(p)
        if not edges:
            return "That file has no edges to draw."
        if mode not in ("full", "side"):
            try:
                from .presence import idle_seconds
                busy = idle_seconds() < float(self.cfg.get("model_show_idle_sec", 60))
            except Exception:
                busy = False
            mode = "side" if busy else "full"
        if mode == "full":
            self.hud.sweep("scan")
        self.hud.wireframe(verts, edges, label or p.stem, max(8, min(seconds or 30, 600)), mode)
        return f"Showing {label or p.stem} ({mode}): {len(verts)} vertices, {len(edges)} edges."

    # ---------------------------------------------------------- blender
    @staticmethod
    def blender_link_up() -> bool:
        import socket
        port = int(os.environ.get("BLENDER_MCP_PORT", "9876"))
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False

    def t_open_blender(self, background: bool = False):
        running = any((p.info["name"] or "").lower() == "blender.exe"
                      for p in psutil.process_iter(["name"]))
        if running and self.blender_link_up():
            if not background:
                from .winfront import find_window, force_front
                force_front(find_window("blender"))
            return "Blender is open and linked; use the mcp__blender__* tools."
        if not running:
            self.hud.status("LAUNCHING BLENDER")
            res = self.t_open_application("blender", background)
            if not res.startswith("Launched"):
                return f"Couldn't launch Blender: {res}"
        self.hud.status("WAITING FOR BLENDER LINK")
        t0 = time.time()
        while time.time() - t0 < 90:
            if self.blender_link_up():
                time.sleep(1.0)   # let the add-on finish starting
                return (f"Blender is open and linked after {time.time() - t0:.0f} s; use the "
                        "mcp__blender__* tools now.")
            time.sleep(1.5)
        return ("Blender is open but its MCP add-on isn't answering on port 9876. Tell the user, "
                "briefly: in Blender, Edit > Preferences > System > Network, tick 'Allow Online "
                "Access' (the add-on needs it), make sure the MCP add-on is enabled with Auto "
                "Start, then Save Preferences. Steam updates to Blender can reset this.")

    def t_show_image(self, path: str, title: str, seconds: int):
        p = Path(os.path.expandvars(os.path.expanduser(path)))
        if not p.exists():
            return f"No image at {p}."
        self.hud.image(str(p), title, max(5, min(seconds or 20, 300)))
        return f"Showing {p.name} on the HUD."

    def t_watch_screen(self, minutes: int, focus: str):
        if self.screen_watch is None:
            return "Screen watching isn't available."
        return self.screen_watch.start(minutes, focus) if minutes > 0 else self.screen_watch.stop()

    # ---------------------------------------------------------- progress
    def t_track_progress(self, kind: str, target: str, label: str):
        if self.progress is None:
            return "Progress tracking isn't available."
        return self.progress.track(kind, target, label or target)


def _timer_label(message: str) -> str:
    """Short ring label: 'Pizza is ready' -> 'PIZZA'."""
    words = [w for w in re.findall(r"[A-Za-z0-9']+", message)
             if w.lower() not in {"the", "your", "a", "an", "is", "are", "time", "to", "for",
                                  "check", "timer", "ready", "done", "sir", "reminder"}]
    return " ".join(words[:2]).upper()[:14] or "TIMER"
