"""Claude Code relay: updates and questions from your Claude Code sessions, spoken by JARVIS.

cc_hook.py (a Claude Code hook) POSTs each event here. JARVIS speaks it between its own
conversations and listens briefly for your (verified) voice reply:

  Stop               "Claude in <project> has finished: <summary>"  -> your reply continues
                     that same session (the hook blocks the stop with your words).
  AskUserQuestion    reads the question and options aloud -> your spoken choice is passed
                     back to Claude.
  PermissionRequest  "Claude in <project> wants to run <command>. Allow?" -> yes / no.

Say nothing (or "no thanks") and Claude carries on as usual: answer in Claude instead.
While you're looking at the Claude window, JARVIS only shows a HUD panel and stays quiet.
"""
from __future__ import annotations

import difflib
import json
import logging
import queue
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import DATA_DIR

log = logging.getLogger("jarvis.relay")
RELAY_INFO = DATA_DIR / "relay.json"

DISMISS = re.compile(r"^\W*(no|nope|nothing|no thanks?|no thank you|thanks?|thank you|that'?s all|"
                     r"ok(ay)?|cool|great|nice|got it|never ?mind|ignore( it)?|later|"
                     r"not now|all good|fine|dismiss|skip)\W*$", re.I)
ORDINALS = {"first": 0, "one": 0, "1": 0, "a": 0, "second": 1, "two": 1, "2": 1, "b": 1,
            "third": 2, "three": 2, "3": 2, "c": 2, "fourth": 3, "four": 3, "4": 3, "d": 3,
            "last": -1}


# ------------------------------------------------------------------- text helpers
def clean(text: str) -> str:
    """Markdown / code / links -> something worth saying aloud."""
    text = re.sub(r"```.*?```", " (code) ", text or "", flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"^\s*(#+|[-*]|\d+\.)\s*", "", text, flags=re.M)
    text = re.sub(r"[*_|>]+", "", text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    lines = [l for l in lines if not re.match(r"^[-:\s]+$", l)]
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def summary(text: str, limit: int = 240) -> str:
    """First sentence or two, at most ~limit characters."""
    t = clean(text)
    out = ""
    for s in re.split(r"(?<=[.!?])\s+", t):
        if out and len(out) + len(s) > limit:
            break
        out = f"{out} {s}".strip()
        if len(out) >= limit * 0.6:
            break
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0] + "..."
    return out


def project_name(payload: dict) -> str:
    cwd = payload.get("cwd") or ""
    name = Path(cwd).name if cwd else "a session"
    if re.fullmatch(r"[0-9a-f]{8,}", name) or "scratchpad" in name.lower():
        name = Path(cwd).parent.name or name
    return name


def describe_tool(tool: str, ti: dict) -> str:
    """Short, speakable description of what Claude wants to do."""
    ti = ti or {}
    if tool in ("Bash", "PowerShell"):
        cmd = clean(ti.get("description") or "") or clean(ti.get("command", ""))[:90]
        return f"run a command: {cmd}"
    if tool in ("Write", "Edit", "NotebookEdit", "MultiEdit"):
        f = Path(ti.get("file_path") or ti.get("notebook_path") or "a file").name
        return f"{'write' if tool == 'Write' else 'edit'} {f}"
    if tool == "WebFetch":
        return f"fetch {re.sub(r'^https?://(www\\.)?', '', ti.get('url', ''))[:60]}"
    if tool == "WebSearch":
        return f"search the web for {ti.get('query', '')[:60]}"
    if tool.startswith("mcp__"):
        parts = tool.split("__")
        return f"use {parts[-1].replace('_', ' ')} from {parts[1].replace('_', ' ')}"
    return f"use {tool}"


def match_option(answer: str, options: list[str]) -> str | None:
    """Spoken answer -> one of the option labels, or None."""
    a = answer.lower().strip(" .!?,")
    a = re.sub(r"^(the|option|choice|number|go with|pick|choose|i'?ll take|let'?s do|let'?s go with)\s+",
               "", a)
    a = re.sub(r"^(the|option|choice|number)\s+", "", a)
    a = re.sub(r"\s+(one|option)$", "", a) if a not in ORDINALS else a
    first = a.split()[0] if a.split() else ""
    for key in (a, first):
        if key in ORDINALS and options:
            i = ORDINALS[key]
            if i < len(options):
                return options[i]
    lows = [o.lower() for o in options]
    for o, lo in zip(options, lows):
        if lo == a or (len(lo) > 2 and lo in a) or (len(a) > 2 and a in lo):
            return o
    best = difflib.get_close_matches(a, lows, n=1, cutoff=0.6)
    if best:
        return options[lows.index(best[0])]
    return None


def stop_output(reply: str) -> dict:
    return {"decision": "block",
            "reason": f"The user replied by voice through JARVIS (their voice assistant): {reply}"}


def question_output(answers: list[tuple[str, str]]) -> dict:
    lines = "; ".join(f'"{q}" -> {a}' for q, a in answers)
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            "Not an error: the user already answered this question by voice through JARVIS, "
            f"so the dialog was skipped. Their answers: {lines}. Carry on using these answers "
            "and do not ask the same question again.")}}


def permission_output(allow: bool) -> dict:
    d = {"behavior": "allow"} if allow else {
        "behavior": "deny", "message": "The user declined this by voice through JARVIS."}
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": d}}


# ------------------------------------------------------------------- server
class RelayItem:
    def __init__(self, payload: dict):
        self.payload = payload
        self.done = threading.Event()
        self.result: dict = {}
        self.expired = False
        self.notify_only = False


class RelayServer:
    """Localhost-only HTTP endpoint the hook script talks to. Events are queued and handled
    by the assistant's main loop (so JARVIS never talks over itself)."""

    def __init__(self, cfg: dict, wait: float = 45.0):
        self.cfg = cfg
        self.wait = wait
        self.q: queue.Queue[RelayItem] = queue.Queue()
        self.token = secrets.token_hex(16)
        self.httpd = None
        self.busy = lambda: False   # set by the assistant
        self.on_show = None         # set by the assistant: payload -> result dict

    def start(self) -> bool:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                if (self.path not in ("/hook", "/show") or
                        not secrets.compare_digest(self.headers.get("X-Jarvis-Token", ""), server.token)):
                    self.send_response(403)
                    self.end_headers()
                    return
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    payload = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                except Exception:
                    payload = {}
                if self.path == "/show":   # show_model.py: a 3D model for the HUD
                    result = server.on_show(payload) if server.on_show else {"error": "not ready"}
                else:
                    result = server.submit(payload)
                body = json.dumps(result).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        port = int(self.cfg.get("relay_port", 47821))
        for p in (port, port + 1, port + 2, 0):
            try:
                ThreadingHTTPServer.allow_reuse_address = False  # never share the port (Windows)
                self.httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
                break
            except OSError:
                continue
        if self.httpd is None:
            log.warning("relay: no free port")
            return False
        self.httpd.daemon_threads = True
        port = self.httpd.server_address[1]
        RELAY_INFO.write_text(json.dumps({"port": port, "token": self.token, "wait": self.wait}),
                              encoding="utf-8")
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="relay").start()
        log.info("Claude Code relay on 127.0.0.1:%d", port)
        return True

    def submit(self, payload: dict) -> dict:
        """Called on the HTTP thread: queue the event and wait for the main loop's answer."""
        if not self.cfg.get("relay_enabled", True):
            return {}
        item = RelayItem(payload)
        if self.busy():
            # JARVIS is mid-conversation: don't hold Claude up. Mention it afterwards; the
            # question / permission stays in Claude's own window.
            item.notify_only = True
            item.done.set()
            self.q.put(item)
            return {}
        self.q.put(item)
        if not item.done.wait(self.wait):
            item.expired = True   # JARVIS was busy: let Claude ask in its own UI
            return {}
        return item.result or {}

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        try:
            RELAY_INFO.unlink()
        except Exception:
            pass
