"""Claude Code hook -> JARVIS relay.

Claude Code runs this for Stop, PreToolUse(AskUserQuestion) and PermissionRequest events
(see install_claude_hooks.py). It forwards the event to the running JARVIS on localhost and
prints whatever JARVIS answers (a voice reply, an answer, an allow/deny). If JARVIS isn't
running, or you don't answer by voice in time, it prints nothing and Claude carries on as
normal, so you can always answer in Claude itself.

Standard library only, so it starts fast.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def main():
    if os.environ.get("JARVIS_INTERNAL"):  # JARVIS's own Claude sessions: never relay
        return
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return
    try:
        info = json.loads((DATA / "relay.json").read_text(encoding="utf-8"))
    except Exception:
        return  # JARVIS has never run the relay
    req = urllib.request.Request(
        f"http://127.0.0.1:{info['port']}/hook", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Jarvis-Token": info["token"]})
    try:
        with urllib.request.urlopen(req, timeout=float(info.get("wait", 50)) + 5) as r:
            body = r.read().decode("utf-8")
    except Exception:
        return  # JARVIS not running / timed out: Claude handles it normally
    if body.strip() and body.strip() != "{}":
        sys.stdout.write(body)


if __name__ == "__main__":
    main()
