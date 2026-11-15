"""Conversation history: every exchange is appended to data/history.jsonl so JARVIS
can answer "what was that script we made yesterday?" or "what did you say about
turbochargers?". Stays on your PC; delete the file to wipe it."""
from __future__ import annotations

import datetime as dt
import json
import re
import threading

from .config import DATA_DIR

HISTORY_PATH = DATA_DIR / "history.jsonl"
_lock = threading.Lock()
_STOP = set("a an the and or but to of in on for with is are was were be it that this what "
            "did do you me my i we our about how why when where which who".split())


def log_exchange(user: str, reply: str, kind: str = "chat", model: str = "") -> None:
    if not (user or reply):
        return
    rec = {"t": dt.datetime.now().isoformat(timespec="seconds"), "kind": kind,
           "model": model, "user": user, "reply": reply}
    with _lock:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _records():
    if not HISTORY_PATH.exists():
        return []
    out = []
    with HISTORY_PATH.open(encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def search(query: str, days: int = 30, limit: int = 8) -> list[dict]:
    """Keyword search (most relevant first, ties -> most recent). An empty query
    returns the latest exchanges in the window."""
    since = dt.datetime.now() - dt.timedelta(days=max(1, days))
    recs = [r for r in _records() if dt.datetime.fromisoformat(r["t"]) >= since]
    words = [w for w in re.findall(r"[a-z0-9']+", query.lower()) if w not in _STOP]
    if not words:
        return recs[-limit:]
    scored = []
    for i, r in enumerate(recs):
        text = f"{r['user']} {r['reply']}".lower()
        score = sum(text.count(w) for w in words) + 3 * sum(w in r["user"].lower() for w in words)
        if score:
            scored.append((score, i, r))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [r for _, _, r in scored[:limit]]


def format_records(recs: list[dict]) -> str:
    lines = []
    for r in recs:
        when = dt.datetime.fromisoformat(r["t"]).strftime("%a %d %b %H:%M")
        tag = "" if r.get("kind", "chat") == "chat" else f" ({r['kind']})"
        lines.append(f"[{when}{tag}] User: {r['user']}\n  JARVIS: {r['reply'][:600]}")
    return "\n".join(lines) or "No matching conversations."
