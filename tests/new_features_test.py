"""Headless checks for: standby mishearings, personality, diagnostic, progress parsing,
model loading (.obj/.stl/.json), timer labels, Chrome profile, window helpers."""
import json
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
results = []


def check(name, ok, info=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {info}")


class FakeHUD:
    def __init__(self):
        self.calls = []

    def __getattr__(self, n):
        return lambda *a, **k: self.calls.append((n, a))


from jarvis.assistant import STANDBY
for t, want in [("Stand back.", True), ("Alright, stand back.", True), ("Stamp by.", True),
                ("go to standby", True), ("stand back up the files", False), ("open standby docs", False)]:
    check(f"standby matcher: {t!r}", bool(STANDBY.match(t)) == want)

from jarvis import config
from jarvis.tools import ToolBox, _timer_label, chrome_profile
tmpcfg = Path(tempfile.mkdtemp()) / "config.json"
config.CONFIG_PATH = tmpcfg
cfg = {"user_name": "sir", "show_cursor": False}
hud = FakeHUD()
tb = ToolBox(lambda q: True, hud, lambda t: None, cfg)

out, err = tb.run("set_personality", {"humour": "sarcastic", "length": "brief", "address_as": ""})
check("set_personality", not err and cfg["persona_humour"] == "sarcastic" and cfg["_persona_changed"], out)
out, err = tb.run("set_personality", {"humour": "silly", "length": "", "address_as": ""})
check("set_personality rejects bad value", "must be one of" in out)
from jarvis.brain_code import personality_note
check("personality reaches the instructions", "sarcastic" in personality_note(cfg))

check("timer label", _timer_label("Your pizza is ready") == "PIZZA", _timer_label("Your pizza is ready"))
check("chrome profile", bool(chrome_profile({})), chrome_profile({}))

from jarvis.progress import ProgressTracker
check("progress: percent in title", ProgressTracker._percent("Rendering 47% - Blender") == 47.0)
check("progress: x/y in title", round(ProgressTracker._percent("Exporting frame 50/200")) == 25)
check("progress: none", ProgressTracker._percent("Untitled - Notepad") is None)

from jarvis.wireframe import load_model
d = Path(tempfile.mkdtemp())
(d / "q.obj").write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n")
v, e = load_model(d / "q.obj")
check("obj quad -> 4 edges", len(v) == 4 and len(e) == 4, f"{len(v)} v {len(e)} e")
tri = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
b = b"\0" * 80 + struct.pack("<I", 1) + struct.pack("<12f", 0, 0, 1, *sum(tri, ())) + b"\0\0"
(d / "t.stl").write_bytes(b)
v, e = load_model(d / "t.stl")
check("binary stl", len(v) == 3 and len(e) == 3)
(d / "a.stl").write_text("solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\n"
                         "vertex 0 1 0\nendloop\nendfacet\nendsolid x\n")
check("ascii stl", len(load_model(d / "a.stl")[1]) == 3)
(d / "m.json").write_text(json.dumps({"verts": [[0, 0, 0], [0, 0, 1]], "edges": [[0, 1]]}))
out, err = tb.run("show_wireframe", {"path": str(d / "m.json"), "label": "rod", "seconds": 10})
check("show_wireframe reaches the HUD", any(c[0] == "wireframe" for c in hud.calls), out)
r = tb.show_model(str(d / "q.obj"), "quad", 10, "side")
check("show_model side mode", "(side)" in r, r)

from jarvis.winfront import monitors, windows, find_window
check("monitors found", len(monitors()) >= 1, str(monitors()))
check("windows listed", len(windows()) >= 1)
check("find_window('') = something", find_window("") is not None)

from jarvis.diagnostic import run_diagnostic
dh = FakeHUD()
rep = run_diagnostic(dh, {})
rows = [c for c in dh.calls if c[0] == "scan_row"]
check("diagnostic ran", len(rows) >= 6 and any(c[0] == "scan_end" for c in dh.calls), rep.splitlines()[0])
print(rep)

print(f"\n{sum(results)}/{len(results)} passed")
