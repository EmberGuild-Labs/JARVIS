"""How JARVIS voices a Claude Code relay event and collects your spoken reply."""
from __future__ import annotations

import logging
import os
import re

from . import osutil
from .audio import chime
from .relay import (DISMISS, RelayItem, describe_tool, match_option, permission_output,
                    project_name, question_output, stop_output, summary)

log = logging.getLogger("jarvis.relay")

# A reply meant for the Claude session must say so: "Tell Claude to...", "Claude, ...",
# "Reply ...", "Ask Claude ...". Everything else is a normal request to JARVIS.
CLAUDE_CUE = re.compile(r"^\W*((please\s+)?(tell|ask|reply\s+to|answer|message|say\s+to)\s+(claude|it)\b(\s+(that|to))?"
                        r"|claude\b[\s,:]+|(reply|respond|answer)\b(\s+(with|that))?)\s*", re.I)


def watching_claude(idle_limit: float = 45.0) -> bool:
    """True when the Claude app is the foreground window and you've used the PC recently,
    i.e. you can already see what Claude is saying."""
    try:
        from .presence import idle_seconds
        # foreground_app() is "claude" on both OSes (claude.exe / Claude.app)
        return osutil.foreground_app() == "claude" and idle_seconds() < idle_limit
    except Exception:
        return False


def _reply(a, seconds: float) -> str | None:
    """Listen once for your verified voice. Returns the words, or None for silence / not you."""
    from .assistant import WAKE_ECHO
    chime(a.player, "wake")
    audio = a.listen(timeout=seconds)
    if audio is None:
        return None
    if not a.verified(audio):
        log.info("relay reply rejected: not the owner's voice")
        return None
    text = WAKE_ECHO.sub("", a.stt.transcribe(audio)).strip()
    a.hud.heard(text)
    return text or None


def handle(a, item: RelayItem):
    """Runs on the assistant's main loop. Sets item.result (hook JSON) and item.done."""
    p = item.payload
    ev = p.get("hook_event_name", "")
    proj = project_name(p)
    cfg = a.cfg
    user = cfg["user_name"]
    title = f"Claude  //  {proj}"
    try:
        quiet = (a.standby or a.away or a.mode in ("game", "focus") or item.notify_only
                 or (cfg.get("relay_quiet_when_watching", True) and watching_claude()))
        reply_sec = float(cfg.get("relay_reply_seconds", 8))
        log.info("relay %s %s from %s (%s)", ev, p.get("tool_name", ""), proj,
                 "quiet" if quiet else "spoken")

        if ev == "Stop":
            msg = p.get("last_assistant_message") or ""
            if not msg.strip() or not cfg.get("relay_updates", True):
                return
            short = summary(msg)
            cue = cfg.get("relay_reply_cue", True)
            a.hud.panel(title, [short[:300]] + (["Reply: say \"Tell Claude ...\""] if cue else []),
                        12 if quiet else 20)
            if quiet:
                if item.notify_only and not a.standby and a.mode == "normal" and not a.away:
                    a.say_async(f"By the way, Claude in {proj} finished: {short}")
                return
            a.say(f"{user.capitalize()}, Claude in {proj} has finished. {short}")
            if item.expired:
                return
            ans = _reply(a, reply_sec)
            if not ans or DISMISS.match(ans):
                return
            m = CLAUDE_CUE.match(ans)
            if m or not cue:
                # "Tell Claude to delete the cube" -> into that Claude session
                item.result = stop_output(ans)   # the whole sentence reads clearest to Claude
                a.say("Passing that on.")
                return
            # Anything else was meant for JARVIS: release Claude, then handle it here.
            item.done.set()
            if not a.quick_open(ans):
                a.hud.set_state("thinking")
                a.respond(ans)
            return

        if ev == "PreToolUse" and p.get("tool_name") == "AskUserQuestion":
            qs = (p.get("tool_input") or {}).get("questions") or []
            if not qs:
                return
            a.hud.panel(title, [qs[0].get("question", "")[:200]] +
                        [f"{i + 1}. {o.get('label', '')}" for i, o in enumerate(qs[0].get("options", []))], 25)
            if quiet:
                if item.notify_only and a.mode == "normal" and not a.standby and not a.away:
                    a.say_async(f"Claude in {proj} has a question for you in its window.")
                return
            answers = []
            for n, q in enumerate(qs):
                labels = [o.get("label", "") for o in q.get("options", [])]
                spoken = " ".join(f"Option {i + 1}: {l}." for i, l in enumerate(labels))
                lead = (f"{user.capitalize()}, Claude in {proj} has a question. "
                        if n == 0 else "Next question. ")
                multi = " You can pick more than one." if q.get("multiSelect") else ""
                a.say(f"{lead}{q.get('question', '')} {spoken}{multi}")
                if item.expired:
                    return
                ans = _reply(a, max(reply_sec, 10))
                if not ans or DISMISS.match(ans):
                    a.say("I'll leave that one for you in Claude.")
                    return
                if q.get("multiSelect"):
                    picked = [m for part in ans.replace(" and ", ",").split(",")
                              if (m := match_option(part, labels))]
                    choice = ", ".join(dict.fromkeys(picked)) or ans
                else:
                    choice = match_option(ans, labels) or ans
                answers.append((q.get("question", ""), choice))
            item.result = question_output(answers)
            a.say(f"Got it: {answers[0][1]}." if len(answers) == 1 else "Got it, answers sent.")
            return

        if ev == "PermissionRequest":
            tool = p.get("tool_name", "")
            if tool in ("AskUserQuestion",):
                return
            if tool == "ExitPlanMode":
                a.hud.panel(title, ["Plan ready for your review"], 20)
                if not quiet:
                    a.say(f"{user.capitalize()}, Claude in {proj} has a plan ready for you to review in Claude.")
                return
            what = describe_tool(tool, p.get("tool_input") or {})
            a.hud.panel(title, [f"Wants to {what}"[:200], "Say yes or no, or answer in Claude"], 20)
            if quiet or not cfg.get("relay_permissions", True):
                if item.notify_only and a.mode == "normal" and not a.standby and not a.away:
                    a.say_async(f"Claude in {proj} is waiting for your permission in its window.")
                return
            a.say(f"{user.capitalize()}, Claude in {proj} wants to {what}. Shall I allow it?")
            if item.expired:
                return
            ans = _reply(a, reply_sec)
            if ans is None:
                a.say("I'll leave it for you to answer in Claude.")
                return
            from .assistant import NO, YES
            if YES.search(ans) and not NO.search(ans):
                item.result = permission_output(True)
                a.say("Allowed.")
            elif NO.search(ans):
                item.result = permission_output(False)
                a.say("Denied.")
            else:
                a.say("I didn't catch a yes or no, so I'll leave it for you in Claude.")
            return
    except Exception:
        log.exception("relay event failed")
    finally:
        item.done.set()
        _note_waiting(a, p, ev, item)
        a.rest()


def _note_waiting(a, p: dict, ev: str, item: RelayItem):
    """Left for the user to answer in Claude: remember it, so JARVIS can remind them if
    the session is still stuck a few minutes later."""
    try:
        tool = p.get("tool_name", "")
        if item.result is not None or not p.get("transcript_path"):
            return
        if ev == "PreToolUse" and tool == "AskUserQuestion":
            kind = "question"
        elif ev == "PermissionRequest" and tool == "ExitPlanMode":
            kind = "plan"
        elif ev == "PermissionRequest" and tool != "AskUserQuestion":
            kind = "permission"
        else:
            return
        import time
        from pathlib import Path
        tp = p["transcript_path"]
        mtime = Path(tp).stat().st_mtime if Path(tp).exists() else time.time()
        a.claude_waiting.append({"proj": project_name(p), "kind": kind, "t": time.time(),
                                 "transcript": tp, "mtime": max(mtime, time.time()), "reminded": 0})
    except Exception:
        log.exception("could not note waiting session")
