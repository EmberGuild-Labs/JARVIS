"""Add (or with --remove, take out) the JARVIS relay hooks in ~/.claude/settings.json.

Claude Code then sends JARVIS: turn-finished updates (Stop), Claude's questions
(AskUserQuestion) and permission prompts (PermissionRequest). Other settings are kept.
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS = Path.home() / ".claude" / "settings.json"
PY = str((ROOT / ".venv" / "Scripts" / "python.exe")).replace("\\", "/")
HOOK = str(ROOT / "jarvis" / "cc_hook.py").replace("\\", "/")
EVENTS = {"Stop": None, "PreToolUse": "AskUserQuestion", "PermissionRequest": None}


def is_ours(entry: dict) -> bool:
    return any("cc_hook.py" in " ".join([h.get("command", "")] + h.get("args", []))
               for h in entry.get("hooks", []))


def main():
    remove = "--remove" in sys.argv
    s = json.loads(SETTINGS.read_text(encoding="utf-8-sig")) if SETTINGS.exists() else {}
    if SETTINGS.exists():
        shutil.copy(SETTINGS, SETTINGS.with_suffix(".json.bak-jarvis"))
    hooks = s.setdefault("hooks", {})
    for ev, matcher in EVENTS.items():
        lst = [e for e in hooks.get(ev, []) if not is_ours(e)]
        if not remove:
            entry = {"hooks": [{"type": "command", "command": PY, "args": [HOOK], "timeout": 70,
                                "statusMessage": "Checking with JARVIS"}]}
            if matcher:
                entry = {"matcher": matcher, **entry}
            lst.append(entry)
        if lst:
            hooks[ev] = lst
        else:
            hooks.pop(ev, None)
    if not hooks:
        s.pop("hooks")
    SETTINGS.write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    print(("Removed" if remove else "Installed") + f" JARVIS hooks in {SETTINGS}")


if __name__ == "__main__":
    main()
