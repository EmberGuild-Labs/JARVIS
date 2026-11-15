"""Protocols: named multi-step routines ("initiate gaming protocol").

They run locally and instantly (no Claude call needed). Edit data/protocols.json or
ask JARVIS to create/change one ("make a movie protocol that dims the HUD, pauses
music and opens Netflix"). Step types:

  {"say": "text"}                      speak
  {"launch": "BeamNG.drive"}           open an app (BeamNG gets its MCP link)
  {"open_url": "https://..."}          open a website
  {"close": ["Discord", "Slack"]}      ask these windows to close (apps may ask to save)
  {"minimize_all": true}               show the desktop
  {"power_plan": "high"}               high | balanced | saver
  {"volume": -10}                      volume steps (~2% each); {"volume": "mute"}
  {"media": "play_pause"}              play_pause | pause | next | previous
  {"mode": "game"}                     game | focus | normal | standby (see below)
  {"lock": true}                       lock the PC
  {"panel": ["line", "line"]}          HUD panel
  {"wait": 2}                          pause, seconds

Modes: game = HUD hidden, corners off, only urgent alerts; focus = announcements held
until focus ends; standby = JARVIS's standby; normal = everything back on.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time

from .config import DATA_DIR

log = logging.getLogger("jarvis")
PROTOCOLS_PATH = DATA_DIR / "protocols.json"

DEFAULT_PROTOCOLS = {
    "gaming": {
        "description": "Game time: performance power plan, quiet game mode, launch BeamNG.",
        "steps": [
            {"say": "Initiating gaming protocol."},
            {"panel": ["Power plan: high performance", "Game mode: HUD hidden, alerts urgent-only",
                       "Launching BeamNG.drive"]},
            {"power_plan": "high"},
            {"minimize_all": True},
            {"launch": "BeamNG.drive"},
            {"mode": "game"},
            {"say": "All set. Enjoy yourself, sir."},
        ],
    },
    "focus": {
        "description": "Heads-down work: music paused, announcements held until focus ends.",
        "steps": [
            {"media": "pause"},
            {"mode": "focus"},
            {"say": "Focus protocol engaged. I'll hold anything that isn't urgent."},
        ],
    },
    "night": {
        "description": "Winding down: quieter, balanced power, JARVIS on standby.",
        "steps": [
            {"say": "Initiating night protocol. Good night, sir."},
            {"volume": -10},
            {"power_plan": "balanced"},
            {"mode": "standby"},
        ],
    },
    "lockdown": {
        "description": "Mute, hide everything, standby, lock the PC.",
        "steps": [
            {"say": "Lockdown."},
            {"volume": "mute"},
            {"minimize_all": True},
            {"mode": "standby"},
            {"lock": True},
        ],
    },
    "normal": {
        "description": "Stand down: everything back to normal.",
        "steps": [
            {"mode": "normal"},
            {"power_plan": "balanced"},
            {"say": "Standing down. Back to normal operations."},
        ],
    },
}

POWER_PLANS = {"high": "SCHEME_MIN", "balanced": "SCHEME_BALANCED", "saver": "SCHEME_MAX"}
RUN_RE = re.compile(r"^\W*(?:please\s+)?(?:initiate|engage|activate|run|start|begin|enable)\s+"
                    r"(?:the\s+)?([a-z0-9 ]+?)\s+protocol\W*$", re.I)
END_RE = re.compile(r"^\W*(?:end|stop|disengage|deactivate|cancel|exit)\s+(?:the\s+)?([a-z0-9 ]+?)?"
                    r"\s*(?:protocol|mode)\W*$|^\W*stand\s+down\W*$", re.I)


def load() -> dict:
    if not PROTOCOLS_PATH.exists():
        PROTOCOLS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROTOCOLS_PATH.write_text(json.dumps(DEFAULT_PROTOCOLS, indent=2), encoding="utf-8")
        return dict(DEFAULT_PROTOCOLS)
    try:
        return json.loads(PROTOCOLS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.error("protocols.json is invalid; using defaults")
        return dict(DEFAULT_PROTOCOLS)


def save(name: str, description: str, steps: list[dict]):
    data = load()
    data[name.lower().strip()] = {"description": description, "steps": steps}
    PROTOCOLS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def find(name: str) -> str | None:
    data = load()
    key = name.lower().strip()
    if key in data:
        return key
    for k in data:
        if key.replace(" ", "") == k.replace(" ", "") or key in k or k in key:
            return k
    return None


class ProtocolRunner:
    """Executes steps. `assistant` provides say/set_mode/set_standby/toolbox/hud."""

    def __init__(self, assistant):
        self.a = assistant

    def run(self, name: str) -> str:
        key = find(name)
        if key is None:
            return f"There's no {name} protocol. Known: {', '.join(load())}."
        steps = load()[key]["steps"]
        log.info("protocol %s: %d steps", key, len(steps))
        self.a.hud.status(f"PROTOCOL // {key.upper()}")
        done = []
        for step in steps:
            try:
                done.append(self._step(step))
            except Exception as e:
                log.exception("protocol step failed: %s", step)
                done.append(f"failed: {step} ({e})")
        return f"{key} protocol complete: " + "; ".join(d for d in done if d)

    def _step(self, step: dict) -> str:
        a, tb = self.a, self.a.toolbox
        (kind, val), = step.items()
        if kind == "say":
            a.say(val)
        elif kind == "launch":
            if "beamng" in str(val).lower():
                tb.run("launch_beamng", {})
            else:
                tb.run("open_application", {"name": val})
            return f"launched {val}"
        elif kind == "open_url":
            tb.run("open_url", {"url": val})
            return f"opened {val}"
        elif kind == "close":
            for title in val:
                tb.run("window_action", {"title": title, "action": "close"})
            return f"closed {', '.join(val)}"
        elif kind == "minimize_all":
            import pyautogui
            pyautogui.hotkey("winleft", "d")
            return "desktop shown"
        elif kind == "power_plan":
            scheme = POWER_PLANS.get(str(val).lower())
            if scheme:
                subprocess.run(["powercfg", "/setactive", scheme], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
                return f"power plan {val}"
        elif kind == "volume":
            if val == "mute":
                tb.run("media_control", {"action": "mute", "steps": 1})
            else:
                n = int(val)
                tb.run("media_control", {"action": "volume_up" if n > 0 else "volume_down",
                                         "steps": abs(n)})
            return f"volume {val}"
        elif kind == "media":
            action = "play_pause" if val in ("pause", "play") else val
            if val == "pause":
                from .media import now_playing
                info = now_playing(timeout=2)
                if not info or info.get("status") != "playing":
                    return "media already paused"
            tb.run("media_control", {"action": action, "steps": 1})
            return f"media {val}"
        elif kind == "mode":
            a.set_mode(val)
            return f"{val} mode"
        elif kind == "lock":
            from .presence import lock_pc
            time.sleep(0.5)
            lock_pc()
            return "locked"
        elif kind == "panel":
            a.hud.panel("Protocol", list(val), 10)
        elif kind == "wait":
            time.sleep(min(float(val), 30))
        return ""
