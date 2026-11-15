"""Send a 3D model to JARVIS's HUD as a rotating hologram.

    python C:/PROJECTS/JARVIS/jarvis/show_model.py MODEL [--label NAME] [--seconds N]
                                                  [--mode auto|full|side] [--summary TEXT]

MODEL: .obj, .stl (ASCII or binary) or .json {"verts": [[x,y,z]...], "edges": [[i,j]...]}.
mode auto: compact on the right while the user is at the PC, full projection otherwise.
Standard library only, so any Python can run it. Exit code 1 if JARVIS isn't running.
"""
import argparse
import json
import os
import sys
import urllib.request

INFO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "relay.json")


def main():
    ap = argparse.ArgumentParser(description="Show a 3D model on the JARVIS HUD")
    ap.add_argument("model")
    ap.add_argument("--label", default="")
    ap.add_argument("--seconds", type=int, default=45)
    ap.add_argument("--mode", default="auto", choices=["auto", "full", "side"])
    ap.add_argument("--summary", default="", help="one short sentence JARVIS may say about it")
    a = ap.parse_args()
    path = os.path.abspath(a.model)
    if not os.path.exists(path):
        sys.exit(f"No file at {path}")
    try:
        info = json.load(open(INFO, encoding="utf-8"))
    except Exception:
        sys.exit("JARVIS isn't running (no relay info).")
    body = json.dumps({"path": path, "label": a.label or os.path.splitext(os.path.basename(path))[0],
                       "seconds": a.seconds, "mode": a.mode, "summary": a.summary}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/show", data=body,
                                 headers={"X-Jarvis-Token": info["token"],
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(json.loads(r.read().decode()).get("result", "sent"))
    except Exception as e:
        sys.exit(f"Couldn't reach JARVIS: {e}")


if __name__ == "__main__":
    main()
