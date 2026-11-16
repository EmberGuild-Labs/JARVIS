"""The listening loop: wake word -> record -> verify speaker -> transcribe -> Claude -> speak.

Also: barge-in (say "hey Jarvis" or press the hotkey while it talks or works to cut it
off), streamed speech, the boot sequence, system alerts, scheduled tasks, dictation
and voice-print adaptation.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import queue
import re
import threading
import time

import numpy as np

from . import osutil
from .audio import SAMPLE_RATE, Microphone, Player, chime, record_utterance
from .brain import Brain
from .config import DATA_DIR
from .hud import HUD
from .speech import SpeakerVerifier, SpeechToText, TextToSpeech, WakeWord
from .tools import ToolBox

log = logging.getLogger("jarvis")

YES = re.compile(r"\b(yes|yeah|yep|sure|do it|go ahead|proceed|confirm|affirmative|okay|ok)\b", re.I)
NO = re.compile(r"\b(no|nope|don't|do not|cancel|stop|abort|negative)\b", re.I)
# Whisper often hears "standby" as "stand back" / "stamp by" (worse with game audio in the room).
STANDBY = re.compile(r"^\W*((alright|all right|okay|ok|right|jarvis)\W+)*(go\s+(in)?to\s+|enter\s+)?"
                     r"(stand\s?-?(by|back|bye|buy)|stamp\s?by|standby(\s+mode)?)(\W+(please|jarvis|now))*\W*$", re.I)
WAKE_UP = re.compile(r"^\W*(wake\s+up|come\s+back( online)?|back\s+online|resume|you'?re\s+up|online)\W*$", re.I)
GUEST = re.compile(r"^\W*(end\s+|stop\s+|exit\s+|disable\s+)?guest\s+mode\W*$", re.I)
STOP = re.compile(r"^\W*(stop|cancel|never ?mind|shut up|quiet|that's all|thank you|thanks)\W*$", re.I)
WAKE_ECHO = re.compile(r"^\W*(hey|hi|okay|ok)?\W*jarvis\W*", re.I)
# "Run the boot sequence", "show me the boot animation", "boot up", "power up", "reboot yourself",
# "initialise", "systems check" ... (never the PC itself: "restart my computer" doesn't match)
BOOT_SHOW = re.compile(
    r"^\W*((can|could)\s+you|please|jarvis|hey|okay|ok|alright)?[\s,]*("
    r"(run|play|do|show(\s+me)?|start|replay|trigger)\s+(the\s+|your\s+)?(boot(\s?up)?|start\s?up|power\s?up|"
    r"initiali[sz]ation)(\s+(sequence|animation|routine|thing))?|"
    r"(boot|power|suit)\s+up|reboot\s+yourself|restart\s+yourself|initiali[sz]e|wake\s+up\s+properly"
    r")(\s+(please|again|for\s+me|jarvis))*\W*$", re.I)
# "Pull up steam", "Jarvis, open microcenter.com in the background", "can you launch discord"
OPEN_RE = re.compile(r"^\W*((can|could|would)\s+you|please|jarvis|hey|okay|ok|alright)?[\s,]*"
                     r"(pull\s+up|open(\s+up)?|launch|bring\s+up|start(\s+up)?|load(\s+up)?|go\s+to|fire\s+up|boot\s+up)\s+"
                     r"(?P<what>.+?)(?P<bg>\s+in\s+the\s+background)?(\s+(please|for\s+me|now))*\W*$", re.I)
DICT_END = re.compile(r"\b(stop|end|finish|exit)\s+dictation\b", re.I)
STATE_PATH = DATA_DIR / "state.json"

_enroll_cache = [0.0, False]


def enrollment_running() -> bool:
    """True while a voice-enrollment window (python -m jarvis.enroll) is open.
    Checked at most every 2 s, so it costs nothing in the listening loop."""
    if time.time() - _enroll_cache[0] < 2:
        return _enroll_cache[1]
    import psutil
    running = False
    for p in psutil.process_iter(["cmdline"]):
        try:
            cmd = p.info["cmdline"] or []
            if "jarvis.enroll" in cmd and "test" not in cmd:
                running = True
                break
        except Exception:
            pass
    _enroll_cache[:] = [time.time(), running]
    return running


class _SilentStream:
    """Stands in for a SpeechStream when a reply should only be shown, not spoken."""
    def feed(self, chunk): pass
    def flush(self): pass
    def close(self): pass
    def wait(self, timeout=None): return True
    def wait_idle(self, timeout=60): return True
    def stop(self): pass


class Assistant:
    def __init__(self, cfg: dict, hud: HUD):
        self.cfg = cfg
        self.hud = hud
        self.player = Player()
        self.mic = Microphone()
        if not cfg.get("mic_enabled", True):   # switched off from the menu bar last time
            self.mic.set_enabled(False)
        self.tts = TextToSpeech(cfg["tts_voice"], cfg["tts_rate"], self.player)
        hud.voice_source = self.player.window   # HUD ring follows JARVIS's voice
        self.stt = SpeechToText(cfg["whisper_model"], cfg["unload_stt_after_idle_min"])
        self.verifier = SpeakerVerifier(cfg["speaker_threshold"])
        self.wake = WakeWord(cfg["wake_threshold"])
        self.toolbox = ToolBox(self.confirm, hud, self.say_async, cfg)
        if cfg.get("backend", "claude_code") == "api":
            self.brain = Brain(cfg, self.toolbox)
        else:
            from .brain_code import CodeBrain
            self.brain = CodeBrain(cfg, self.toolbox)
            self.brain.on_background_done = self.background_done
            self.toolbox.bg = self.brain
        self.trigger = threading.Event()     # set by hotkey for push-to-talk / interrupt
        self.running = True
        self._speak_lock = threading.RLock()
        self._async_q: queue.Queue[str] = queue.Queue()
        self._request_q: queue.Queue[str] = queue.Queue()
        self._typed_q: queue.Queue[tuple[str, bool]] = queue.Queue()
        self._offer_q: queue.Queue = queue.Queue()   # proactive yes/no suggestions
        self._listening = False              # pauses the barge-in watcher while recording
        self._watching = False
        self._barged = False
        self.current_stream = None
        self.standby = False
        self.away = False              # set by webcam presence
        self.away_log: list[str] = []  # announcements held while you're away
        self.presence = None
        self.mode = "normal"            # normal | game | focus (protocol modes)
        self.focus_log: list[str] = []
        from .protocols import ProtocolRunner
        self.protocols = ProtocolRunner(self)
        self.toolbox.protocols = self.protocols
        self.toolbox.face_enroll = self.enroll_face
        from .scheduler import Scheduler
        self.scheduler = Scheduler(self.run_scheduled)
        self.toolbox.scheduler = self.scheduler
        from .relay import RelayServer
        self.relay = RelayServer(cfg, wait=float(cfg.get("relay_wait_seconds", 45)))
        self._in_session = False
        self.claude_waiting: list[dict] = []   # Claude Code sessions waiting on the user
        self.relay.busy = lambda: self._in_session or self.current_stream is not None

    # ------------------------------------------------------------ background
    def run_scheduled(self, task: dict):
        """Called by the scheduler thread when a task is due."""
        action, target, label = task["action"], task["target"], task["label"]
        self.hud.panel("Scheduled", [label], 10)
        if action == "open_application":
            if "beamng" in target.lower() and self.cfg.get("beamng_mcp", True):
                self.toolbox.run("launch_beamng", {})
            else:
                self.toolbox.run("open_application", {"name": target})
            name = label.removeprefix("Launch ").strip() or target
            self.say_async(f"As scheduled, {self.cfg['user_name']}, launching {name}.")
        elif action == "open_url":
            self.toolbox.run("open_url", {"url": target})
            self.say_async(f"As scheduled, opening {label}.")
        elif action == "remind":
            self.say_async(f"A reminder, {self.cfg['user_name']}: {target}")
        elif action == "jarvis_request":
            self._request_q.put(target)

    def background_done(self, task: dict):
        """A background task finished: show it, log it, and announce it between conversations."""
        from .history import log_exchange
        title = task['title']
        ok = task['status'] == 'done'
        self.hud.panel('Task complete' if ok else 'Task failed', [title, task['result'][:300]], 15)
        log_exchange(f"(background task) {title}", task['result'], kind='background')
        lead = (f"{self.cfg['user_name'].capitalize()}, the background task {title} is done. "
                if ok else '')
        self.say_async(lead + task['result'])

    def quick_open(self, text: str) -> bool:
        """'Pull up / open / launch X' where X is clearly a website or an installed app:
        do it right away without asking Claude. Searches and anything vague return False
        (Claude handles them)."""
        if not self.cfg.get("quick_open", True):
            return False
        m = OPEN_RE.match(text)
        if not m:
            return False
        what, bg = m.group("what"), bool(m.group("bg"))
        try:
            hit = self.toolbox.resolve_open(what)
        except Exception:
            log.exception("resolve_open failed")
            return False
        if not hit:
            return False
        kind, target = hit
        if kind == "url":
            out, err = self.toolbox.run("open_url", {"url": target, "background": bg})
            spoken = f"Pulling up {target.split('/')[0].removeprefix('www.')}."
        elif "beamng" in target and self.cfg.get("beamng_mcp", True):   # with its MCP link
            out, err = self.toolbox.run("launch_beamng", {})
            spoken = "Launching BeamNG."
        else:
            out, err = self.toolbox.run("open_application", {"name": target, "background": bg})
            spoken = f"Opening {what.strip().strip('.?!').removeprefix('the ').removeprefix('my ')}."
        log.info("quick open %s %s: %s", kind, target, out)
        if err:
            return False   # let Claude try another way
        self.hud.set_state("speaking")
        self.say(spoken + (" In the background." if bg else ""))
        try:
            from .history import log_exchange
            log_exchange(text, out, kind="quick_open")
        except Exception:
            pass
        if hasattr(self.brain, "transcript"):   # so "close it" / "no, the other one" make sense
            self.brain.transcript += [f"User: {text}", f"Assistant: ({out})"]
        return True

    def show_model_request(self, payload: dict) -> dict:
        """From /jarvis-model-show (any Claude Code session): put a model up as a hologram.
        Compact on the right while you're using the PC; full projection plus a spoken
        line when you're not."""
        path = str(payload.get("path", ""))
        label = str(payload.get("label") or "the prototype")[:60]
        res = self.toolbox.show_model(path, label, int(payload.get("seconds") or 45),
                                      str(payload.get("mode") or "auto"))
        log.info("model show: %s", res)
        if res.startswith("Showing"):
            note = str(payload.get("summary") or "").strip()
            if "(full)" in res and not self.standby and self.mode == "normal":
                self.say_async(f"{self.cfg['user_name'].capitalize()}, Claude's prototype of {label} "
                               f"is up." + (f" {note}" if note else ""))
            elif note:
                self.hud.panel(f"Prototype  //  {label}", [note[:200]], 20)
        return {"result": res}

    def remind_claude_waiting(self):
        """A Claude Code session left waiting on a question or permission for a while (and
        not touched since): remind once after N minutes, again after 3N."""
        from pathlib import Path
        mins = float(self.cfg.get("relay_waiting_remind_min", 5))
        keep = []
        for w in self.claude_waiting:
            try:
                moved_on = Path(w["transcript"]).stat().st_mtime > w["mtime"] + 1
            except Exception:
                moved_on = False
            age = time.time() - w["t"]
            if moved_on or age > 3600:
                continue
            due = mins * 60 * (1 if w["reminded"] == 0 else 3)
            if w["reminded"] < 2 and age >= due:
                w["reminded"] += 1
                what = "an answer" if w["kind"] == "question" else \
                    "your plan review" if w["kind"] == "plan" else "your permission"
                self.hud.panel(f"Claude  //  {w['proj']}", [f"Still waiting for {what}"], 15)
                self.say_async(f"{self.cfg['user_name'].capitalize()}, Claude in {w['proj']} "
                               f"has been waiting for {what} for {int(age // 60)} minutes.")
            keep.append(w)
        self.claude_waiting = keep

    def alert(self, kind: str, title: str, spoken: str, lines: list[str]):
        """Called by the system monitor. In standby: a brief HUD panel, no voice.
        In game mode only urgent alerts (GPU heat, low disk) get through."""
        if getattr(self, "mode", "normal") == "game" and kind not in ("gpu_temp", "disk"):
            return
        self.hud.panel(title, lines, 5 if self.standby else 12)
        if self.cfg.get("alerts_speak", True) and not self.standby:
            # "Your drive..." -> "Sir, your drive..." but keep acronyms like "GPU" intact
            lead = spoken if spoken[1:2].isupper() else spoken[0].lower() + spoken[1:]
            self.say_async(f"{self.cfg['user_name'].capitalize()}, {lead}")

    # ---------------------------------------------------------------- standby
    def rest(self):
        """Return the HUD to its resting look (dim ring when on standby)."""
        self.hud.set_state("standby" if self.standby else "idle")

    def set_standby(self, on: bool, speak: bool = True):
        if on == self.standby:
            return
        self.standby = on
        self.cfg["_standby"] = on      # read by the HUD / media watcher
        log.info("standby %s", "on" if on else "off")
        if on:
            self.hud.clear_marks()
            self.hud.sweep("shutdown")
            if speak:
                self.say(f"Standing by, {self.cfg['user_name']}.")
        else:
            self.hud.sweep("boot")
            self.hud.boot([("Resuming from standby", True), ("All systems nominal", True)])
            time.sleep(1.4)
            if speak:
                self.say(f"Back online, {self.cfg['user_name']}.")
        self.rest()

    @property
    def mic_on(self) -> bool:
        return self.mic.enabled

    def set_mic(self, on: bool, speak: bool = True):
        """Switch the microphone on/off (menu bar). Off closes the input stream: no wake
        word, no push-to-talk, nothing recorded. Saved, so it stays off after a restart."""
        if on == self.mic.enabled:
            return
        from .config import save_setting
        self.mic.set_enabled(on)
        self.cfg["mic_enabled"] = on
        save_setting("mic_enabled", on)
        log.info("microphone %s", "on" if on else "off")
        if on:
            self.hud.panel("Microphone", ["On. Say \"Hey Jarvis\" when you need me."], 5)
            if speak:
                self.say_async(f"Microphone on, {self.cfg['user_name']}.")
        else:
            self.hud.panel("Microphone", ["Off. I can't hear anything.",
                                          "Turn it back on from the menu bar."], 6)
            if speak:
                self.say_async("Microphone off.")

    # ---------------------------------------------------------------- barge-in
    def _watch(self, on_barge):
        """While JARVIS talks or works, listen for the wake word / hotkey and call
        on_barge() once. Returns a stop() function."""
        if not self.cfg.get("barge_in", True) or self._watching:
            return lambda: None
        active = threading.Event()
        active.set()
        self._watching = True
        self.trigger.clear()
        th = self.cfg.get("barge_in_threshold", 0.6)

        def loop():
            self.wake.reset()
            while active.is_set():
                if self._listening:
                    time.sleep(0.05)
                    continue
                frame = self.mic.read(timeout=0.2)
                pushed = self.trigger.is_set()
                heard = not pushed and frame is not None and self.wake.detect(frame, th)
                if heard and not self._barge_is_owner():
                    self.wake.reset()      # someone else said "Hey Jarvis": keep going
                    continue
                if pushed or heard:
                    self.trigger.clear()
                    log.info("barge-in")
                    on_barge()
                    break

        t = threading.Thread(target=loop, daemon=True)
        t.start()

        def stop():
            active.clear()
            t.join(1)
            self._watching = False
            self.wake.reset()
        return stop

    def _barge(self, stream=None):
        self._barged = True
        if stream is not None:
            stream.stop()
        if self.current_stream is not None:
            self.current_stream.stop()
        self.brain.interrupt()
        self.hud.status("INTERRUPTED")

    # ---------------------------------------------------------------- speaking
    def say(self, text: str) -> bool:
        """Speak text. Returns False if the user cut it off."""
        if not text:
            return True
        with self._speak_lock:
            if self.current_stream is not None:   # let an in-progress reply finish first
                self.current_stream.wait_idle()
            self.hud.say(text)
            self.hud.set_state("speaking")
            barge = self.cfg.get("barge_in", True)
            if not barge:
                self.mic.mute(True)
            stream = self.tts.stream()
            stop_watch = self._watch(lambda: self._barge(stream))
            try:
                stream.feed(text)
                stream.close()
                ok = stream.wait()
            finally:
                stop_watch()
                time.sleep(0.1)
                if not barge:
                    self.mic.mute(False)
                self.mic.flush()
            return ok

    def say_async(self, text: str):
        """Used by timers, alerts, schedules: spoken from the main loop between interactions.
        While you're away from the desk (webcam presence) it's held for your return."""
        if self.away and self.cfg.get("presence_hold_messages", True):
            self.away_log.append(text)
            return
        if self.mode == "focus":
            self.focus_log.append(text)
            return
        self._async_q.put(text)

    def offer(self, question: str, action):
        """A proactive suggestion: asked between conversations; your verified 'yes' runs it."""
        self._offer_q.put((question, action))

    # ------------------------------------------------------------------- modes
    def set_mode(self, mode: str):
        """game: HUD hidden, corners off, urgent alerts only. focus: announcements held.
        standby: JARVIS standby. normal: everything back."""
        mode = mode.lower()
        if mode == "standby":
            self.set_standby(True, speak=False)
            return
        prev, self.mode = self.mode, (mode if mode in ("game", "focus") else "normal")
        self.cfg["_mode"] = self.mode
        log.info("mode %s -> %s", prev, self.mode)
        self.hud.set_visible(self.mode != "game" and self.cfg.get("hud_enabled", True))
        if self.mode == "normal":
            if self.standby:
                self.set_standby(False, speak=False)
            held, self.focus_log = self.focus_log, []
            if held:
                self.hud.panel("Held during focus", [m[:90] for m in held], 20)
                self._async_q.put(f"While you were focusing, {len(held)} thing"
                                  f"{'s' if len(held) != 1 else ''} came up.")
                for m in held:
                    self._async_q.put(m)

    def enroll_face(self) -> str:
        """Learn the owner's face from the webcam, with spoken guidance."""
        from .face_id import enroll_from_camera
        from .presence import Camera
        cam, own = None, False
        if self.presence is not None and self.presence.has_camera:
            cam = self.presence.camera
            self.presence.paused = True
        else:
            cam = Camera(self.cfg.get("camera_index", 0))
            own = True
            if not cam.open():
                return "I can't find a webcam to learn your face with."
        self.hud.status("FACE ENROLLMENT")
        try:
            ok = enroll_from_camera(cam.read_rgb, say=self.say)
        finally:
            if own:
                cam.close()
            if self.presence is not None:
                self.presence.paused = False
                from .face_id import FaceID
                self.presence.face_id = FaceID(self.cfg.get("face_threshold", 0.42))
        return ("Face learned. I'll know it's you at the desk, and the sentry is armed." if ok else
                "I couldn't get a clear enough look. Make sure you're alone in view and well lit, "
                "then try again.")

    def on_intruder(self, photo, when):
        msg = (f"Security notice: someone other than you was at your PC at {when:%H:%M}. "
               "I locked it and saved a photo in the sentry folder.")
        self.hud.panel("Sentry", [f"Unknown person at {when:%H:%M}", "PC locked",
                                  str(photo.name) if photo else "(no photo)"], 30)
        self.away_log.append(msg) if self.away else self._async_q.put(msg)

    # ---------------------------------------------------------------- presence
    def on_away(self):
        self.away = True
        self.hud.status("AWAY")

    def on_return(self, gone_sec: float):
        self.away = False
        held, self.away_log = self.away_log, []
        if self.standby or not self.cfg.get("presence_greet", True):
            for msg in held:
                self._async_q.put(msg)
            return
        if gone_sec < self.cfg.get("presence_greet_after_min", 2) * 60 and not held:
            return
        mins = int(gone_sec // 60)
        line = f"Welcome back, {self.cfg['user_name']}."
        if held:
            n = len(held)
            line += (f" While you were away{f' for {mins} minutes' if mins >= 2 else ''}, "
                     f"{n} thing{'s' if n != 1 else ''} came up.")
            self.hud.panel("While you were away", [m[:90] for m in held], 20)
        self._async_q.put(line)
        for msg in held:
            self._async_q.put(msg)

    def submit_typed(self, text: str, speak: bool = False):
        """A request typed into the 'Type to JARVIS' bar (handled by the main loop)."""
        self._typed_q.put((text, speak))

    def respond(self, text: str, speak: bool = True) -> bool:
        """Send a request to Claude and speak the reply as it streams in (speak=False:
        the reply is only shown on the HUD). Returns False if the user interrupted."""
        self._barged = False
        stream = self.tts.stream() if speak else _SilentStream()
        self.current_stream = stream
        shown: list[str] = []
        stop_watch = self._watch(lambda: self._barge(stream))

        def on_text(chunk):
            if chunk is None:
                stream.flush()
                return
            if not shown:
                self.hud.set_state("speaking")
            shown.append(chunk)
            self.hud.say("".join(shown))
            stream.feed(chunk)

        try:
            reply = self.brain.ask(text, on_status=self.hud.status, on_text=on_text)
            log.info("jarvis: %s", reply)
            try:
                from .history import log_exchange
                log_exchange(text, reply or '(interrupted)',
                             model=getattr(self.brain, 'model', '') or '')
            except Exception:
                log.exception('history log failed')
            if reply and not getattr(self.brain, "streamed", False) and not self._barged:
                self.hud.say(reply)
                self.hud.set_state("speaking")
                stream.feed(reply)
            stream.close()
            stream.wait()
        finally:
            stop_watch()
            self.current_stream = None
            self.mic.flush()
        return not self._barged

    # ------------------------------------------------------------- listening
    def listen(self, timeout: float, silence_ms: int | None = None) -> np.ndarray | None:
        self.hud.set_state("listening")
        self._listening = True
        try:
            audio = record_utterance(self.mic, timeout, silence_ms or self.cfg["silence_end_ms"],
                                     self.cfg["max_command_seconds"], on_level=self.hud.set_level)
        finally:
            self._listening = False
        self.hud.set_level(0)
        return audio

    def verified(self, audio: np.ndarray, extra: np.ndarray | None = None) -> bool:
        if not self.cfg.get("require_voice_match", True):
            return True
        sample = audio if extra is None else np.concatenate([extra, audio])
        ok, score = self.verifier.verify(sample)
        log.info("speaker score %.3f (%s)", score, "accept" if ok else "reject")
        if ok and self.cfg.get("adapt_voiceprint", True):
            self.verifier.adapt(score)
        return ok

    def wake_is_owner(self, wake_audio: np.ndarray) -> bool:
        """Speaker check on the "Hey Jarvis" itself. Uses a looser threshold than requests
        because the wake phrase is short; too short to judge (< 1 s) counts as a pass and the
        request check still applies."""
        if (not self.cfg.get("wake_voice_check", True) or not self.cfg.get("require_voice_match", True)
                or not self.verifier.enrolled or len(wake_audio) < SAMPLE_RATE):
            return True
        score = self.verifier.score(wake_audio)
        thr = (self.verifier.wake_threshold if self.verifier.wake_threshold is not None
               else self.cfg.get("wake_speaker_threshold", 0.40))
        ok = score >= thr
        log.info("wake speaker score %.3f (%s)", score, "accept" if ok else "ignore")
        if not ok:
            self.hud.status("VOICE NOT RECOGNISED")
            self.hud.set_state("denied")
            time.sleep(0.8)
            self.rest()
            self.hud.status("")
        return ok

    def _barge_is_owner(self) -> bool:
        """Voice check on a "Hey Jarvis" said while JARVIS is talking or working. The clip
        also contains JARVIS's own voice from the speakers, so the bar is a little lower
        than for a normal wake word. The hotkey always interrupts."""
        if (not self.cfg.get("barge_voice_check", True) or not self.cfg.get("require_voice_match", True)
                or not self.verifier.enrolled):
            return True
        clip = self.mic.preroll()
        if clip is None or len(clip) < SAMPLE_RATE:
            return True
        score = self.verifier.score(clip)
        base = (self.verifier.wake_threshold if self.verifier.wake_threshold is not None
                else self.cfg.get("wake_speaker_threshold", 0.40))
        thr = base - float(self.cfg.get("barge_threshold_margin", 0.05))
        ok = score >= thr
        log.info("barge-in speaker score %.3f (%s)", score, "accept" if ok else "ignore")
        if not ok:
            self.hud.status("INTERRUPT IGNORED: VOICE NOT RECOGNISED")
        return ok

    def confirm(self, question: str) -> bool:
        """Ask a yes/no question aloud; only the verified user's 'yes' counts."""
        for _ in range(2):
            self.say(question)
            audio = self.listen(timeout=6)
            if audio is None:
                continue
            if not self.verified(audio):
                self.say("I need that confirmation from you personally.")
                return False
            ans = self.stt.transcribe(audio)
            self.hud.heard(ans)
            if NO.search(ans):
                return False
            if YES.search(ans):
                return True
        return False

    # ------------------------------------------------------------ dictation
    def dictate(self):
        """Type everything the (verified) user says until 'stop dictation'."""
        import pyautogui
        self.toolbox.dictation_requested = False
        self.hud.status("DICTATION  //  say 'stop dictation' to finish")
        last_len, idle_since = 0, time.time()
        while self.running:
            audio = self.listen(timeout=10, silence_ms=1200)
            if audio is None:
                if time.time() - idle_since > 120:
                    self.say("Dictation ended, as you've gone quiet.")
                    break
                continue
            idle_since = time.time()
            if not self.verified(audio):
                continue
            self.hud.set_state("thinking")
            text = WAKE_ECHO.sub("", self.stt.transcribe(audio)).strip()
            if not text:
                continue
            self.hud.heard(text)
            low = text.lower().strip(" .!?,")
            if DICT_END.search(low):
                chime(self.player, "end")
                self.say("Dictation finished.")
                break
            if low in ("new line", "newline", "next line"):
                pyautogui.press("enter")
                last_len = 0
                continue
            if low in ("new paragraph", "next paragraph"):
                pyautogui.press("enter", presses=2)
                last_len = 0
                continue
            if low in ("delete that", "scratch that", "undo that"):
                if last_len:
                    pyautogui.press("backspace", presses=last_len, interval=0.002)
                last_len = 0
                continue
            chunk = text + " "
            self.toolbox.run("type_text", {"text": chunk})
            last_len = len(chunk)
        self.hud.status("")

    # ----------------------------------------------------------------- boot
    def boot(self, replay: bool = False):
        """Start-up sequence. replay=True: just the show ("run the boot sequence")."""
        from .brain_code import beamng_running
        import psutil
        import sounddevice as sd
        try:
            mic = sd.query_devices(kind="input")["name"][:28]
        except Exception:
            mic = "default"
        du = psutil.disk_usage(osutil.SYSTEM_DRIVE)
        backend = "Claude Code" if self.cfg.get("backend", "claude_code") != "api" else "Claude API"
        cli_ok = getattr(self.brain, "cli", True) is not None
        n_sched = len(self.scheduler.tasks)
        checks = [
            (f"Audio input: {mic}" if self.mic.enabled else "Microphone: switched off", self.mic.enabled),
            ("Wake word engine", True),
            ("Voice authentication" + ("" if self.verifier.enrolled else ": not enrolled"),
             self.verifier.enrolled),
            (f"Neural link: {backend}", cli_ok),
            ("Model routing: Haiku / Sonnet / Opus", True),
            (f"Scheduler: {n_sched} task{'s' if n_sched != 1 else ''}", True),
            (f"Storage: {du.free / 2**30:.0f} GB free", du.free / 2**30 >= self.cfg.get("alert_disk_free_gb", 20)),
            *([] if osutil.IS_MAC else
              [("BeamNG link: " + ("online" if beamng_running(self.cfg) else "standby"), True)]),
            ("Presence sensor: " + ("scanning for webcam" if self.cfg.get("presence_enabled", True)
                                    else "off"), True),
        ]
        self.hud.sweep("boot")
        self.hud.boot(checks)
        time.sleep(0.5 + 0.32 * len(checks) + 0.5)
        if replay:
            self.say(f"All systems online, {self.cfg['user_name']}.")
            return

        # Fuller greeting on the first start of the day.
        state = {}
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
        today = dt.date.today().isoformat()
        first_today = state.get("last_boot") != today
        state["last_boot"] = today
        try:
            STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
        except Exception:
            pass

        h = dt.datetime.now().hour
        part = "morning" if h < 12 else "afternoon" if h < 18 else "evening"
        user = self.cfg["user_name"]
        if not self.verifier.enrolled and self.cfg.get("require_voice_match", True):
            return
        line = f"Good {part}, {user}. All systems online."
        if first_today:
            todays = [t for t in self.scheduler.tasks if t["when"].startswith(today)]
            if todays:
                line += (f" You have {len(todays)} thing{'s' if len(todays) != 1 else ''} "
                         f"scheduled today, the next at {todays[0]['when'][11:16]}.")
        self.say(line)

    # -------------------------------------------------------------- main loop
    def run(self):
        self.mic.start()

        def mic_check():   # all-zero input = the OS is blocking the mic (macOS privacy)
            time.sleep(8)
            if self.mic.enabled:
                peak = getattr(self.mic, "peak", 0)
                if peak == 0:
                    log.warning("microphone is delivering silence - check the Microphone permission "
                                "(System Settings > Privacy & Security > Microphone)")
                else:
                    log.info("microphone ok (peak level %d)", peak)
        threading.Thread(target=mic_check, daemon=True).start()
        self.verifier.refresh()
        threading.Thread(target=self.stt.warm, daemon=True).start()
        self.boot()
        waiting_for_enrollment = False
        if not self.verifier.enrolled and self.cfg.get("require_voice_match", True):
            waiting_for_enrollment = True   # voice commands stay off until there's a voiceprint
            if self.cfg.get("auto_enroll", True):
                self.launch_enrollment()
            else:
                log.info("no voiceprint; auto enrollment off - voice commands disabled until enrolled")
                self.hud.panel("Voice not enrolled",
                               ["Voice commands are off until you enroll.",
                                "Type to JARVIS works meanwhile.",
                                "Menu bar > Enroll my voice… when you're ready."], 15)
        self.rest()
        self.scheduler.start()
        from .alerts import SystemMonitor
        SystemMonitor(self.alert, self.cfg).start()
        from .media import TrackWatcher
        TrackWatcher(self.hud, self.cfg).start()
        from .presence import PresenceMonitor
        from .face_id import FaceID
        self.presence = PresenceMonitor(self.cfg, self.on_away, self.on_return,
                                        face_id=FaceID(self.cfg.get("face_threshold", 0.42)),
                                        on_intruder=self.on_intruder)
        self.presence.start()
        from .suggestions import SuggestionEngine
        self.suggestions = SuggestionEngine(self)
        self.suggestions.start()
        if self.cfg.get("relay_enabled", True):
            try:
                self.relay.start()
            except Exception:
                log.exception("relay failed to start")
        from .progress import ProgressTracker
        self.toolbox.progress = ProgressTracker(self)
        self.relay.on_show = self.show_model_request
        from .screenwatch import ScreenWatcher
        self.toolbox.screen_watch = ScreenWatcher(self)
        last_check = 0.0
        last_wait_check = 0.0

        while self.running:
            if time.time() - last_check > 2:
                last_check = time.time()
                if self.verifier.refresh() and waiting_for_enrollment:
                    waiting_for_enrollment = False
                    self.say("Voice print received. I'll know you anywhere now. Just say, hey Jarvis.")
                    self.rest()
            if time.time() - last_wait_check > 30:
                last_wait_check = time.time()
                self.remind_claude_waiting()
            while not self._async_q.empty():
                self.say(self._async_q.get())
                self.rest()
            while not self._offer_q.empty():  # proactive suggestions
                question, action = self._offer_q.get()
                if self.away or self.standby or self.mode != "normal":
                    continue
                chime(self.player, "wake")
                if self.confirm(question):
                    try:
                        action()
                        self.say("Done.")
                    except Exception:
                        log.exception("suggestion action failed")
                else:
                    self.say("Very well.")
                self.rest()
            while not self.relay.q.empty():  # Claude Code session updates / questions
                from .relay_voice import handle as relay_handle
                item = self.relay.q.get()
                if not item.expired:
                    relay_handle(self, item)
            while not self._typed_q.empty():    # "Type to JARVIS" requests
                text, speak = self._typed_q.get()
                log.info("typed: %s (speak=%s)", text, speak)
                if self.standby:
                    self.set_standby(False, speak=speak)
                self.hud.heard(text)
                self.hud.set_state("thinking")
                self._in_session = True
                try:
                    self.respond(text, speak=speak)
                except Exception:
                    log.exception("typed request failed")
                    self.hud.set_state("error")
                    self.hud.say("Something went wrong on my end. Details are in the log.")
                self._in_session = False
                self.rest()
            while not self._request_q.empty():  # scheduled "jarvis_request" tasks
                req = self._request_q.get()
                self.hud.set_state("thinking")
                self.hud.heard(f"(scheduled) {req}")
                try:
                    self.respond(f"(This is a task you scheduled earlier; carry it out now and "
                                 f"tell the user the result.) {req}")
                except Exception:
                    log.exception("scheduled request failed")
                self.rest()

            if not self.mic.enabled:
                if self.trigger.is_set():   # push-to-talk with the mic switched off
                    self.trigger.clear()
                    self.hud.panel("Microphone", ["Off. Turn it on from the menu bar first."], 5)
                    self.say("My microphone is off. You can turn it back on from the menu bar.")
                    self.rest()
                time.sleep(0.3)
                continue
            frame = self.mic.read(timeout=0.5)
            if frame is None:
                continue
            self.mic.update_noise(frame)
            # Stay deaf while enrolling (the phrases contain "Hey Jarvis", and our own
            # replies would end up in the voice samples) or before there's a voice print.
            if waiting_for_enrollment or enrollment_running():
                self.trigger.clear()
                continue
            pushed = self.trigger.is_set()
            if not pushed and not self.wake.detect(frame):
                continue

            self.trigger.clear()
            wake_audio = None if pushed else self.mic.preroll()
            self.wake.reset()
            self.mic.flush()
            if wake_audio is not None and not self.wake_is_owner(wake_audio):
                continue   # someone else said "Hey Jarvis": stay silent
            self._in_session = True
            try:
                self.session(wake_audio)
            except Exception:
                log.exception("session failed")
                self.hud.set_state("error")
                self.say("Something went wrong on my end. Details are in the log.")
            self._in_session = False
            self.wake.reset()
            self.mic.flush()
            self.rest()

    def launch_enrollment(self):
        """Open the guided voice enrollment in its own console window."""
        from .config import ROOT
        if enrollment_running():  # a window is already open: don't start a second one
            log.info("enrollment already running")
            return
        try:
            osutil.open_in_console([osutil.python_exe(), "-m", "jarvis.enroll"], cwd=str(ROOT))
            self.say("Hello. Before I take orders, I need to learn your voice. "
                     "I've opened a window with a few phrases to read. I'll guide you through it.")
        except Exception:
            log.exception("could not launch enrollment")
            self.say("I need to learn your voice first. Please run the enrollment script.")

    def session(self, wake_audio):
        if self.cfg.get("require_voice_match", True) and not self.verifier.enrolled:
            self.say("I don't have your voice print yet, so I can't take commands.")
            return
        chime(self.player, "wake")
        first, interrupted = True, False
        while True:
            audio = self.listen(timeout=6 if first else self.cfg["follow_up_seconds"])
            if audio is None:
                if first:
                    chime(self.player, "end")
                return
            # Judge the request on its own voice, so someone talking after your
            # "Hey Jarvis" is caught. Very short requests also use the wake word audio.
            alone = len(audio) >= 1.5 * SAMPLE_RATE or wake_audio is None or not first
            if not self.verified(audio, None if alone else wake_audio):
                self.hud.set_state("denied")
                self.hud.status("VOICE NOT RECOGNISED")
                chime(self.player, "deny")
                if self.cfg.get("say_voice_not_recognised", True):
                    self.say("Voice not recognised.")
                time.sleep(0.6)
                return
            first, wake_audio = False, None
            self.hud.set_state("thinking")
            text = WAKE_ECHO.sub("", self.stt.transcribe(audio)).strip()
            if not text:
                return
            self.hud.heard(text)
            log.info("user: %s", text)
            from .protocols import END_RE, RUN_RE
            m = RUN_RE.match(text)
            if m:
                self.hud.set_state("thinking")
                log.info("protocol: %s", self.protocols.run(m.group(1)))
                return
            if END_RE.match(text) and self.mode != "normal":
                self.protocols.run("normal")
                return
            g = GUEST.match(text)
            if g:
                if g.group(1):  # "end guest mode"
                    self.cfg["_guest_until"] = 0
                    self.say("Guest mode off. The sentry is armed again.")
                else:
                    self.cfg["_guest_until"] = time.time() + 3600
                    self.say("Guest mode for the next hour. I won't lock the screen for your guest.")
                return
            if STANDBY.match(text):
                self.set_standby(True)
                return
            if self.standby:          # any verified request wakes it up
                if WAKE_UP.match(text):
                    self.set_standby(False)
                    continue
                self.set_standby(False, speak=False)
            elif WAKE_UP.match(text):
                self.say("I'm right here, " + self.cfg["user_name"] + ".")
                continue
            if STOP.match(text):
                chime(self.player, "end")
                return
            if BOOT_SHOW.match(text):   # "run the boot sequence": replay the start-up show
                self.boot(replay=True)
                return
            if self.quick_open(text):   # "pull up steam" / "pull up microcenter.com": instant
                interrupted = False
                continue
            completed = self.respond(f"(interrupted) {text}" if interrupted else text)
            interrupted = not completed
            if interrupted:          # cut off: listen straight away for what's next
                chime(self.player, "wake")
                first = True
                continue
            if self.toolbox.dictation_requested:
                self.dictate()
                return
