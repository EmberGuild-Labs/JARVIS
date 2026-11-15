"""Configuration loading for JARVIS.

Settings live in config.json at the project root (created on first run from
DEFAULTS). Secrets (the Anthropic API key) live in .env.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"
CONFIG_PATH = ROOT / "config.json"
VOICEPRINT_PATH = DATA_DIR / "voiceprint.npy"
MEMORY_PATH = DATA_DIR / "memory.json"
LOG_PATH = DATA_DIR / "jarvis.log"

DEFAULTS: dict = {
    "user_name": "sir",
    # Claude
    "backend": "claude_code",     # claude_code = your Claude Code login (no API key) | api = API key
    "code_dir": "C:\\PROJECTS",   # where JARVIS creates and edits code projects
    "agent_effort": "medium",     # effort for the Claude Code backend (coding benefits from more)
    "model": "claude-opus-5-5",
    "effort": "low",              # low = snappy voice replies; raise for harder tasks
    "max_history_turns": 12,
    "topic_detection": True,      # start a fresh conversation when you change subject
    # Model routing (Claude Code backend): simple -> Haiku, complex -> Sonnet, coding -> Opus
    "model_routing": True,
    "router_model": "claude-haiku-5-5",   # picks the tier + spots topic changes
    "tier_models": {"simple": "claude-haiku-5-5", "complex": "claude-sonnet-5-5",
                    "coding": "claude-opus-5-5"},
    "auto_escalate": True,        # Haiku/Sonnet hand off to Opus when stuck or on errors
    "topic_model": "",            # API backend only: blank = same as "model"
    # Wake word (openWakeWord "hey jarvis" model)
    "wake_threshold": 0.5,
    "hotkey": "ctrl+alt+j",       # push-to-talk (skips wake word, still verifies voice)
    # Speaker verification
    "speaker_threshold": 0.55,    # cosine similarity needed to accept your voice
    "wake_voice_check": True,     # only your voice can wake JARVIS ("Hey Jarvis")
    "wake_speaker_threshold": 0.40,  # looser: the wake phrase is short
    "say_voice_not_recognised": True,  # speak "Voice not recognised" when a request is rejected
    "require_voice_match": True,
    # Speech-to-text (faster-whisper)
    "whisper_model": "base.en",   # tiny.en is lighter, small.en is more accurate
    "unload_stt_after_idle_min": 15,
    # Text-to-speech
    "tts_voice": "en-GB-RyanNeural",
    "tts_rate": "+8%",
    # Listening
    "follow_up_seconds": 6,       # after a reply, listen this long without wake word
    "silence_end_ms": 900,
    "max_command_seconds": 45,
    # HUD
    "hud_enabled": True,
    "hud_position": "top-right",  # top-right | top-left | bottom-right | bottom-left
    "hud_idle_indicator": True,
    "relay_enabled": True,        # speak Claude Code session updates / questions (cc_hook.py)
    "relay_updates": True,        # announce when a Claude Code session finishes a turn
    "relay_permissions": True,    # ask permission prompts aloud ("yes" / "no")
    "relay_reply_seconds": 8,     # how long JARVIS listens for your spoken reply
    "relay_wait_seconds": 45,     # max time a Claude session waits on JARVIS before its own UI
    "relay_quiet_when_watching": True,  # HUD panel only while you're looking at the Claude app
    "relay_port": 47821,
    "hud_not_games": [],          # extra .exe names the HUD should never hide over
    "hud_sweeps": True,           # holographic full-screen boot / shutdown / scan sweeps
    "model_show_idle_sec": 60,    # models show compact on the right if you used the PC this recently
    "bring_to_front": True,       # things JARVIS opens come to the front (unless you say "in the background")
    "chrome_profile": "",         # Chrome profile folder for open_url; "" = the one Chrome used last
    "persona_humour": "dry",      # off | dry | playful | sarcastic (also changeable by voice)
    "persona_length": "normal",   # brief | normal | detailed
    "barge_voice_check": True,    # "Hey Jarvis" while it talks only interrupts for your voice
    "barge_threshold_margin": 0.05,  # how much looser than the wake check (JARVIS's own voice is in the clip)
    "alert_battery_percent": 20,
    "alert_internet": True,       # say when the internet drops / comes back
    "relay_waiting_remind_min": 5,  # remind when a Claude session waits on you this long
    "progress_update_min": 10,    # spoken "still running" updates for tracked jobs / background tasks
    "progress_bg_updates": True,
    "blender_mcp": True,          # give JARVIS the Blender MCP tools (they work once Blender is open)
    "relay_reply_cue": True,      # only "Tell Claude ..." replies go to a Claude session; the rest is for JARVIS
    "share_claude_mcp": True,     # JARVIS also gets every app connection (MCP server) in your Claude Code config
    "share_claude_mcp_skip": [],  # ...except these names
    "screen_watch_interval_sec": 120,  # "watch my screen": how often to glance (if the screen changed)
    "screen_watch_model": "claude-haiku-5-5",
    "hide_in_games": True,        # hide the whole HUD while a game / full-screen app is in front
    "show_cursor": True,          # JARVIS's own reticle shows where it clicks / types
    "cursor_lead_ms": 420,        # pause so you see the reticle arrive before it acts
    "screen_corners": "active",   # corner brackets: active (while JARVIS is engaged) | always | off
    # Interrupting: say "hey Jarvis" (or press the hotkey) while it talks or works
    "barge_in": True,
    "barge_in_threshold": 0.6,    # a little stricter than the normal wake word (speaker echo)
    # Voice print slowly adapts to you from confident matches (anchored to enrollment)
    "adapt_voiceprint": True,
    # System alerts (checked once a minute)
    "alerts_enabled": True,
    "alerts_speak": True,
    "alert_gpu_temp": 85,
    "alert_cpu_percent": 90,
    "alert_ram_percent": 90,
    "alert_process_ram_share": 0.4,
    "alert_disk_free_gb": 20,
    "alert_downloads": True,
    # Browser control (Playwright drives a real Chrome window; logins persist in data/browser_profile_chrome)
    "browser_control": True,
    "browser_channel": "chrome",  # Chrome only - never Microsoft Edge
    # Background tasks (separate Claude sessions that announce their result)
    "max_background_tasks": 3,
    # Webcam presence (detects *a* face in front of the PC, never who; frames never saved)
    "presence_enabled": True,     # does nothing until a webcam is connected
    "camera_index": 0,
    "presence_interval_sec": 1.5,
    "presence_away_sec": 20,      # no face this long = you've stepped away
    "presence_lock": False,       # lock the PC when nobody is in view (off: looking down hides your face)...
    "presence_lock_sec": 20,      # ...for this long
    "presence_lock_requires_idle": True,  # ...and the keyboard/mouse is idle too (no lock while typing)
    "presence_greet": True,
    "presence_greet_after_min": 2,
    "presence_hold_messages": True,  # save announcements for when you're back
    "face_threshold": 0.42,      # how closely a face must match YOUR enrolled face
    "sentry_enabled": True,      # someone else at the PC while you're away -> photo + lock
    "sentry_cooldown_min": 5,
    # Proactive suggestions (learns your habits; at most one nudge per 45 min)
    "suggestions_enabled": True,
    "break_reminder_hours": 3,
    # Media / games
    "media_panel": True,          # small HUD panel when the track changes
    "beamng_mcp": True,           # connect to BeamNG.drive's MCP server when it's running
    "beamng_mcp_url": "http://127.0.0.1:29292/mcp",
    # Safety
    "confirm_risky_actions": True,
}


def load_config() -> dict:
    load_dotenv(ROOT / ".env")
    DATA_DIR.mkdir(exist_ok=True)
    MODELS_DIR.mkdir(exist_ok=True)
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")))
        except json.JSONDecodeError as e:
            print(f"[config] config.json is invalid ({e}); using defaults")
    else:
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
    cfg["api_key_present"] = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return cfg


def save_setting(key: str, value) -> None:
    """Persist one setting to config.json (used by the tray menu)."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")) if CONFIG_PATH.exists() else {}
    except json.JSONDecodeError:
        cfg = {}
    cfg[key] = value
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
