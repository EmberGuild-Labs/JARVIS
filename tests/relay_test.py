"""Claude Code relay: hook script -> server -> voice handler (voice simulated), plus helpers."""
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import jarvis.config as config
from jarvis import relay
from jarvis.config import DEFAULTS

results = []
def check(name, cond, info=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name} {info}")

# ---- helpers
opts = ["Red", "Blue", "Green"]
check("ordinal", relay.match_option("the second one", opts) == "Blue")
check("option number", relay.match_option("option 3", opts) == "Green")
check("label", relay.match_option("let's go with red please", opts) == "Red")
check("fuzzy", relay.match_option("grean", opts) == "Green")
check("no match", relay.match_option("purple", opts) is None)
s = relay.summary("## Done\n\nI've **updated** `hud.py` so the HUD shows over Claude. "
                  "Also fixed the relay. ```py\nx=1\n``` More text here that is long. " * 3)
check("summary clean", "**" not in s and "`" not in s and len(s) <= 245, repr(s[:90]))
check("describe bash", relay.describe_tool("Bash", {"command": "npm install", "description": "Install deps"})
      == "run a command: Install deps")
check("project", relay.project_name({"cwd": r"C:\PROJECTS\JARVIS"}) == "JARVIS")
check("dismiss", bool(relay.DISMISS.match("No thanks.")) and not relay.DISMISS.match("make it blue"))

# ---- server + real hook script
cfg = dict(DEFAULTS, relay_port=47899)
srv = relay.RelayServer(cfg, wait=5)
assert srv.start()

answers = {}
def fake_main_loop():
    while True:
        item = srv.q.get()
        ev = item.payload.get("hook_event_name")
        if ev == "Stop":
            item.result = relay.stop_output("also add tests")
        elif ev == "PermissionRequest":
            item.result = relay.permission_output(True)
        elif ev == "PreToolUse":
            item.result = relay.question_output([("Colour?", "Blue")])
        item.done.set()
threading.Thread(target=fake_main_loop, daemon=True).start()

py = sys.executable
def run_hook(payload, env=None):
    import os
    e = dict(os.environ, **(env or {}))
    r = subprocess.run([py, str(ROOT / "jarvis" / "cc_hook.py")], input=json.dumps(payload).encode(),
                       capture_output=True, timeout=30, env=e)
    return r.stdout.decode().strip()

out = run_hook({"hook_event_name": "Stop", "cwd": "C:\\x", "last_assistant_message": "Done."})
check("stop -> block with reply", json.loads(out).get("decision") == "block" and "also add tests" in out, out[:80])
out = run_hook({"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "ls"}})
check("permission -> allow", json.loads(out)["hookSpecificOutput"]["decision"]["behavior"] == "allow")
out = run_hook({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "tool_input": {}})
check("question -> deny with answer", "Blue" in out and "deny" in out)
check("JARVIS-internal sessions skipped", run_hook({"hook_event_name": "Stop"}, {"JARVIS_INTERNAL": "1"}) == "")

srv.busy = lambda: True
t0 = time.time()
out = run_hook({"hook_event_name": "Stop", "last_assistant_message": "x"})
check("busy JARVIS never holds Claude up", out == "" and time.time() - t0 < 3, f"{time.time()-t0:.1f}s")
srv.busy = lambda: False

srv.stop()
t0 = time.time()
out = run_hook({"hook_event_name": "Stop"})
check("JARVIS not running -> silent, fast", out == "" and time.time() - t0 < 3, f"{time.time()-t0:.1f}s")

# bad token is refused
import urllib.request, urllib.error
srv2 = relay.RelayServer(cfg, wait=1); srv2.start()
try:
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{srv2.httpd.server_address[1]}/hook",
                           data=b"{}", headers={"X-Jarvis-Token": "nope"}), timeout=3)
    check("bad token refused", False)
except urllib.error.HTTPError as e:
    check("bad token refused", e.code == 403)
srv2.stop()

print(f"\n{sum(results)}/{len(results)} passed")
