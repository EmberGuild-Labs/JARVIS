# JARVIS: a voice assistant for your PC or Mac, powered by Claude Code

Runs on **Windows 10/11** and **macOS** (Apple Silicon or Intel). The macOS notes are in [Running on macOS](#running-on-macos).

Say **"Hey Jarvis"** and talk. It checks that the voice is yours, sends your request to Claude,
and Claude can control the PC: open apps, click and type, read the screen, run PowerShell,
search the web, set timers, control media, and show info panels on a holographic-style HUD.
(On a Mac it runs zsh instead of PowerShell.)
It answers out loud.

## Everything JARVIS can do

| Area | What you can say or expect |
|---|---|
| **Talk** | "Hey Jarvis" + anything. Follow-ups with no wake word for 6 s. Interrupt any time with "Hey Jarvis" or `Ctrl+Alt+J`. Replies start speaking as they're written. |
| **Security** | Only obeys your voice (checked on every command and every "yes"). Risky actions need a spoken "yes". The voiceprint slowly adapts to you. |
| **Brains** | Haiku for quick things, Sonnet for complex ones, Opus for coding, with automatic handoff to Opus. New topics start a fresh conversation. Long-term memory of your preferences. |
| **PC control** | Open apps and websites, click, type, keyboard shortcuts, scroll, and focus, minimise, maximise or close windows. Its **own cursor** shows where it's acting. Volume and media keys, clipboard, files, PowerShell. |
| **Screen** | "Look at this" (explains and brackets things), "is anybody on screen?" (face brackets, never identifies), "find me on the internet" (image search plus brackets). |
| **Coding** | Writes, edits, runs and tests code in `C:\PROJECTS` using Claude Code's tools. Summarises out loud, with details on the HUD. |
| **Web** | Searches and reads web pages. |
| **Background** | Long jobs run in the background while you keep talking; JARVIS announces when they're done. |
| **Browser** | Drives a real Edge window for web tasks: lookups, forms, comparisons. |
| **History** | "What was that script we made yesterday?" searches past conversations. |
| **Time** | Timers, plus scheduled launches, reminders and full requests at set times, with daily, weekday or weekly repeats. |
| **Notes** | "Take a note...", "read my notes", and dictation into any window. |
| **Music** | "What's playing?" and a now-playing HUD panel when the track changes. |
| **BeamNG.drive** | "Launch BeamNG", then control the game by voice (spawn, speed, AI driving, screenshots, messages). |
| **Watchdog** | Speaks up about GPU heat, sustained CPU load, RAM pressure, memory hogs, low disk space and finished downloads. |
| **Looks** | Boot sequence, animated HUD ring, corner brackets, info panels, face lock-on brackets, labelled annotations, a tray icon that changes colour with state. |
| **Background** | Starts with Windows, about 0.1% CPU and ~200 MB RAM while idle, below-normal priority. |
## Setup (one time)

JARVIS runs on **your Claude Code login**, so no API key is needed. Claude Code must be installed and signed in
(run `claude` in a terminal once to check). Usage counts toward your Claude plan's limits.

1. **Start it:** double-click `run.pyw`, or sign out and back in (autostart is already on).
2. **Voice enrollment happens automatically.** On first start JARVIS says hello, opens a window and talks you
   through it: two seconds of quiet, then it says *"Phrase one. Go."* and you read the phrase shown on screen.
   There are six phrases, about a minute in total:
   1. Hey Jarvis, what's the weather looking like today?
   2. Hey Jarvis, open Spotify and play something relaxing.
   3. Hey Jarvis, how much memory is my computer using right now?
   4. Hey Jarvis, remind me in ten minutes to check the oven.
   5. Hey Jarvis, take a look at my screen and tell me what you see.
   6. The quick brown fox jumps over the lazy dog, then naps in the warm afternoon sun.

   When you finish, JARVIS confirms it has your voice print and is ready. To redo it later, or check it:
   `.\.venv\Scripts\python.exe -m jarvis.enroll` (and `... -m jarvis.enroll test`).
3. `.\autostart.ps1 -Remove` turns off autostart and `.\autostart.ps1` turns it back on.

To use an API key instead, set `"backend": "api"` in `config.json` and put the key in `.env`.
On a fresh machine, `.\setup.ps1` creates the venv, installs everything and downloads the models.

### Talking to it
- **Interrupt any time:** say "Hey Jarvis" (or press `Ctrl+Alt+J`) while it's talking or working. It stops
  immediately, cancels the task and listens. Say "stop" to end there, or just give the next request.
- **Standby:** "Hey Jarvis, standby" makes it go quiet: the HUD dims, the corner brackets and track panels switch off, and alerts only flash on the HUD. Say "Hey Jarvis, wake up" (or ask anything) to bring it back. Scheduled tasks still run. There's also a tray toggle.
- **Faster replies:** it starts speaking the first sentence while the rest is still being written, and on long
  jobs it gives a one-line heads-up first.
- **Boot sequence:** on start-up the HUD rings spin up, a system check ticks through (mic, voice auth,
  Claude link, scheduler, storage, BeamNG link), and it greets you by time of day. On the first start of the
  day it also mentions anything scheduled.
- **Notes and dictation:** "take a note: call Sam about the car" saves to `Documents\JARVIS Notes.md`, and "read
  my notes" reads them back. "Start dictation" types whatever you say into the focused window; you can also say
  "new line", "new paragraph" and "delete that", and "stop dictation" ends it.
- **Your voiceprint adapts:** each time it is very sure a command was you, it nudges the voiceprint slightly
  toward that sample. It is anchored to your original enrollment, so it can't drift toward someone else. Re-run
  enrollment to reset it.

### Background tasks
Say *"build me a weather app in the background"* or *"research the best budget wheel for BeamNG and let me
know when you're done"*. JARVIS starts the job in its own Claude session, says "On it", and stays free for
other questions. When the job finishes, it shows a HUD panel and reads the result aloud. *"How's that going?"*
lists your tasks and *"cancel that"* stops one. Background tasks can't use your mouse or keyboard, can't
ask you questions, and can't do anything that needs your spoken "yes"; instead they tell you what still
needs approval. Up to 3 run at once.

### Browser control
For anything on the web, JARVIS drives a real Edge window directly, rather than taking screenshots and
clicking pixels. That makes it faster and more reliable for looking things up on a site, comparing prices,
filling in forms and reading articles. The window stays open so you can watch. If a site needs a login,
sign in yourself in that window; it remembers you (the profile is in `data\browser_profile`). It never types
passwords or payment details, and won't submit purchases, bookings or messages unless you've clearly said so.

### Conversation history
Every exchange is saved locally in `data\history.jsonl`, so you can ask *"what was that script we made
yesterday?"* or *"what did you tell me about turbochargers?"*. Delete the file to wipe it.

### Multiple monitors
Screenshots, face scans, the overlay, its cursor and the corner brackets work across all connected screens:
*"look at my other screen"*. Right now you have one screen, so everything stays on it.
### Protocols
Named routines that run instantly, with no Claude call needed:

| Say | What happens |
|---|---|
| "Initiate gaming protocol" | High-performance power plan, desktop cleared, BeamNG launched, **game mode** (HUD hidden, no corner brackets, only urgent alerts like GPU heat) |
| "Initiate focus protocol" | Music paused; announcements held until focus ends |
| "Initiate night protocol" | Volume down, balanced power plan, JARVIS on standby |
| "Initiate lockdown protocol" | Mute, hide everything, standby, lock the PC |
| "Stand down" / "end gaming protocol" | Everything back to normal, plus anything held during focus |

Make your own by asking: *"make a movie protocol that pauses music, hides the HUD and opens Netflix"*. They're
stored in `data\protocols.json`.

### Face recognition (yours only) and the security sentry
Say **"Hey Jarvis, learn my face"** (or use the tray: *Learn my face*) and look at the webcam for about 10
seconds. Only your own face is enrolled; every other face is just "not you", and nobody else is ever
enrolled, named or identified. After that:
- "Welcome back" and the held messages are only for **you**, so a stranger sitting down hears nothing.
- **Security sentry:** if someone else is at your PC while you're away, JARVIS saves **one** photo to
  `data\sentry` (tray: *Sentry photos*), locks the PC, and tells you when you're back.

The sentry only arms once your face is enrolled. Settings: `sentry_enabled`, `face_threshold`.

### Voice waveform
While JARVIS talks, a ring of bars around the HUD reactor moves with its actual voice.

### Proactive suggestions
JARVIS learns your habits locally (`data\usage.json`, games only) and occasionally speaks up:
*"You usually play BeamNG around now, sir. Shall I launch it?"* (your verified "yes" launches it),
*"You've been at it for three hours. Perhaps a short break?"*, or *"It's past one. Shall I initiate night
protocol?"*. It gives at most one nudge every 45 minutes, and never while you're away, in standby, in
game or focus mode, or mid-conversation. Habit suggestions start after a few days of learning.
`suggestions_enabled` turns this off.
### Webcam presence
Connect any webcam and JARVIS notices when you're at your desk. It only detects *a* face in front of the PC,
never *whose*, and frames are processed in memory and never saved.
- **Nobody in view for 20 s** (and no keyboard or mouse use): it **locks the PC** and holds its announcements.
  It won't lock while you're typing, even if you're out of shot.
- **A face that isn't yours, with you not in view:** it **locks within about 3 seconds**. It saves one photo (at most
  every 5 min) and tells you when you're back. Say *"Hey Jarvis, guest mode"* (or use the tray) to let a friend use
  the PC for an hour, and *"end guest mode"* to re-arm it.
- **You come back** (once unlocked): *"Welcome back, sir. While you were away for 14 minutes, 2 things came up"*,
  followed by each one, with a HUD panel listing them.

There's a tray toggle, and settings are under `presence_*` in `config.json`. With no webcam it just checks for one every
5 minutes. While a webcam is in use its light stays on.
### Alerts
Once a minute (about 85 ms of CPU) it checks GPU temperature (85 °C and up), CPU near 100% for 5 minutes, RAM
above 90%, any one app using over 40% of RAM, disk space (under 20 GB free), and finished downloads in your
Downloads folder. It speaks up once and shows a HUD panel; each alert type then waits a cooldown before
repeating. Thresholds are the `alert_*` settings in `config.json`.

### Music
"What song is this?" or "what's playing?" reads the current track from Spotify, browsers and other media apps.
A small now-playing panel appears on the HUD when the track changes (`media_panel`).

### BeamNG.drive
When BeamNG runs with its MCP server enabled, JARVIS can control the game: "spawn a pickup", "what's my speed",
"let the AI drive to the gas station", "take a screenshot", "show a message". "Launch BeamNG" (or a scheduled
BeamNG launch) starts it with `-enablemcp`. If the game is started some other way, add `-enablemcp` once in Steam:
right-click BeamNG.drive > Properties > Launch options. JARVIS links up automatically once the game is running.
### Which model answers
Each request is sorted by Haiku in about a second, then handled by the right model:

| Kind of request | Model | Examples |
|---|---|---|
| Simple | Haiku (fastest, ~3 s) | weather, time, maths, open an app, volume, timers, "is anybody on screen" |
| Complex | Sonnet | research, comparisons, summarising a page, "look at this", operating an app's GUI |
| Coding | Opus | writing, fixing or running code; also when you say "use Opus" or "think hard" |

A follow-up never drops to a weaker model mid-conversation. If Haiku or Sonnet gets stuck, isn't confident,
or errors out, it hands off to Opus with the conversation so far, and Opus finishes the job (the HUD shows
"ESCALATING TO OPUS"). The HUD status line always shows which model is working.
### Coding
JARVIS has Claude Code's full coding toolset: it reads, writes and edits files, runs commands and tests, and
searches the web. Ask things like *"make me a Python script that renames my screenshots by date"* or *"in my
JARVIS project, make the HUD a bit bigger"*. New projects go in `C:\PROJECTS` (`code_dir`). It speaks a short
summary and puts details on the HUD. Risky shell commands, and any file change outside `C:\PROJECTS`, need a
spoken "yes" from your verified voice.

## Running on macOS

The same code runs on macOS. A small platform layer picks the right implementation at runtime, so
Windows behaviour is unchanged.

### Setup (macOS)
1. Install Claude Code and sign in (`claude` in a terminal), as on Windows.
2. You need Python 3.13 with Tk. Homebrew: `brew install python@3.13 python-tk@3.13`; Miniforge's
   3.13 also works.
3. From the project folder, run `./setup.sh`. It creates `.venv`, installs everything and downloads the models.
4. Start it with `.venv/bin/python run.py`. Voice enrollment starts on the first run in a Terminal window,
   or run it yourself with `.venv/bin/python -m jarvis.enroll` (`... -m jarvis.enroll test` to check it).
5. Optional: `./autostart.sh` starts JARVIS at login through a LaunchAgent
   (`~/Library/LaunchAgents/local.jarvis.assistant.plist`) and starts it now. `./autostart.sh --remove`
   turns it off. You can also use **Start at login** in the menu-bar menu, which takes effect at the next
   login. The agent gets a full PATH, so it finds your `claude`, Homebrew and node. If JARVIS crashes,
   it restarts after 30 s; choosing Quit leaves it stopped.
6. If you use the Claude Code relay, re-run `.venv/bin/python install_claude_hooks.py` on the Mac so the
   hooks point at the macOS venv.

### Permissions
macOS asks for each permission the first time JARVIS needs it. Grant it to the app that starts JARVIS:
Terminal, iTerm or your IDE. With autostart, grant it to the venv's `python` instead. Everything is under
**System Settings > Privacy & Security**:

| Permission | Needed for |
|---|---|
| Microphone | Wake word and voice commands |
| Camera | Webcam presence, face ID, the sentry |
| Screen Recording | Screenshots, "look at this", window titles, "watch my screen" |
| Accessibility | Moving, focusing and closing windows, clicking and typing, the caret-following cursor |
| Input Monitoring | The global `Ctrl+Option+J` push-to-talk hotkey |
| Automation | Spotify and Music control, hiding apps for "clear the desk" (asked on first use) |

With autostart, launchd runs the venv's interpreter directly, so permissions belong to that Python
binary, not to Terminal. Find its real path with `readlink -f .venv/bin/python` and add it under
Accessibility and Input Monitoring with the **+** button. Microphone and Camera ask on their own.
If the mic permission is missing, the log says "microphone is delivering silence".

Without Screen Recording, window titles fall back to app names. Without Accessibility, window commands
answer with a message telling you to turn it on.

### Microphone switch (menu bar)
The JARVIS icon in the menu bar has a **Microphone** item at the top. Switching it off closes the mic
stream completely, so nothing is captured, the orange mic indicator goes out, and the icon turns grey with a
red slash. It stays off after a restart until you switch it back on (`mic_enabled` in `config.json`).
While it's off, the push-to-talk hotkey just tells you the mic is off. The switch works on Windows too
(tray menu).

### What's different on a Mac

| Feature | Windows | macOS |
|---|---|---|
| Shell tool | PowerShell (`run_shell`) | zsh login shell (`run_shell`), with Unix risky-command checks (`sudo`, `rm -rf`, `diskutil`, `launchctl`...) |
| Tray icon | System tray (pystray) | Menu-bar item (AppKit), same menu; no Dock icon |
| HUD | Colour-key transparent, click-through Tk window | `systemTransparent` Tk window made click-through and floating on all Spaces via AppKit |
| Hotkey | `keyboard` library | `pynput` (needs Input Monitoring) |
| Windows | Win32 (`jarvis/winfront_win.py`) | Quartz window list + Accessibility API (`jarvis/macfront.py`) |
| Apps | Start menu apps | `.app` bundles in `/Applications`, `/System/Applications`, `~/Applications` (`open -a`) |
| Chrome | Your Chrome profile | Same, via `open -na "Google Chrome" --args --profile-directory=...` |
| Media / volume | Media keys, system media session | Volume via AppleScript, media keys via system events; "what's playing" asks Spotify or Music |
| Lock / idle | `LockWorkStation`, `GetLastInputInfo` | The system lock-screen call, Quartz idle time |
| Offline voice | Windows SAPI (pyttsx3) | The built-in `say` command |
| Diagnostic | PowerShell: drivers, Windows Update | `sw_vers`, chip, `system_profiler` GPU, volumes, `softwareupdate -l`; no drivers row |
| Power plans in protocols | `powercfg` | Skipped (needs admin on macOS) |
| GPU temperature alert | `nvidia-smi` | Not available on Macs, skipped quietly |
| BeamNG.drive | Supported | No macOS build: the BeamNG tools say so and the boot check is skipped |
| `code_dir` default | `C:\PROJECTS` | `~/PROJECTS` (change it in `config.json`) |
| Autostart | Registry Run key (`autostart.ps1`) | LaunchAgent (`autostart.sh`) |

### Notes on the port
- `jarvis/osutil.py` holds everything OS-specific that isn't window management: idle time, locking,
  the foreground app, the shell, media and volume, opening files, single-instance lock and process priority.
- `jarvis/winfront.py` is now a dispatcher with one API (`windows`, `find_window`, `force_front`, `arrange`,
  `monitors`, `front_after_launch`). It loads `winfront_win.py` on Windows and `macfront.py` on macOS.
  On macOS a window handle is the window server's window number.
- Screenshots on Retina displays are captured in pixels, but clicks use points. The scale factor is
  folded into the screenshot-to-screen mapping, so clicks and annotations land in the right place.
- `run_powershell` was renamed to `run_shell` on both platforms. The prompts tell Claude which OS and
  shell it's on, and to use `cmd` shortcuts on a Mac.
- The HUD never takes keyboard focus on macOS. Its windows refuse key status, and if macOS activates
  JARVIS anyway, focus goes straight back to the app you were using. You can keep typing while it shows things.
  It only touches the keyboard or mouse when it's actually clicking or typing for you.
- Only windows on the current Space are visible to JARVIS on macOS. Full-screen browser video doesn't
  count as a game, so the HUD stays visible.
- `requirements.txt` uses platform markers: `keyboard`, `pygetwindow` and `winrt` are Windows-only;
  `pynput` and `pyobjc` are macOS-only.
- Tested on macOS 27 (Apple M3, Python 3.13): imports and the pure-logic tests, tools (shell, app launch,
  volume, screenshots), HUD rendering and click-through flags, menu-bar item, window arrange. Not yet
  tested: a full voice session end to end, multi-monitor moves, and "clear the desk".

## Using it

| Do this | What happens |
|---|---|
| "Hey Jarvis" ... pause ... request | Chime, then it listens, verifies your voice and acts |
| Keep talking after it replies | Follow-up window (6 s) with no wake word needed |
| `Ctrl+Alt+J` | Push-to-talk (your voice is still checked) |
| "stop" / "thanks" | Ends the conversation |
| Tray icon (ring by the clock) | Changes colour with state (blue = standby, cyan = listening, amber = thinking, red = voice rejected). Menu: talk, show/hide HUD, scan for faces, screen corners mode, show memory, voice lock, new conversation, log, quit |

### Screen tricks
- **"Look at this"** / **"what am I looking at?"**: takes a screenshot, explains it, and draws amber labelled brackets around what it's talking about.
- **"Is anybody on screen?"**: scans the screen and draws animated lock-on brackets around every face. Detection only: no names, no info, no identification.
- **"Find me on the internet"** / **"search for <name>"**: opens an image search, then brackets the faces in the results. It uses your name from memory; if it doesn't know it, it asks once.
- **Corner brackets**: L-shaped brackets in the four screen corners while JARVIS is active. Set `screen_corners` to `always` or `off`, or change it from the tray menu.

### Scheduling
Say *"boot up BeamNG.drive at 17:00 today"*, *"every weekday at 9 remind me to check my email"*,
*"open YouTube at 8 tonight"*, or *"tomorrow at 7, check the weather and tell me if I need a coat"*
(that last kind runs a full JARVIS request at that time). *"What's scheduled?"* lists everything on the HUD,
and *"cancel the BeamNG one"* removes it. Repeats can be daily, weekdays or weekly.

Schedules are saved in `data/schedule.json` and survive restarts. JARVIS has to be running for them to fire
(autostart handles that). Anything missed by under 10 minutes, for example while the PC was booting, still
runs; older missed one-offs are skipped.
### Memory and conversations
JARVIS stores facts about you (name, preferences, people, routines) in `data/memory.json`, on its own or when you say *"remember that..."*. *"Forget..."* removes a fact, and the tray menu's **Show memory** lists them all. Follow-ups keep the conversation's context. When you switch to something unrelated (like *"what's the weather?"* in the middle of something else), it starts a fresh conversation automatically. Memory still carries over.

Try: *"what's eating my RAM"*, *"open Spotify and play my liked songs"*, *"look at my screen and
summarise this page"*, *"remind me in 20 minutes to stretch"*, *"turn the volume down a bit"*,
*"what's the news on the Mars mission"*, *"remember I prefer Celsius"*.

## Safety

- Commands come only from **your** voice (speaker verification on every request and every "yes").
- Destructive actions (deleting, killing processes, registry, shutdown, downloads, overwriting files,
  closing windows) are spoken back to you and need a verified spoken "yes".
- Claude is told to treat web pages, files and screenshots as data, never as instructions, and
  never to enter passwords or payment details.
- Voice verification is good, not bank-grade. A recording of your voice could fool it. Don't leave
  it running unattended on a shared machine with the voice lock turned off.

## Resource use

Face scans only run when you ask (about 60 ms each). Idle: about 0.1% CPU and around 200 MB RAM, at below-normal process priority. The HUD only animates
while active, and the speech-to-text model unloads after 15 idle minutes. Autostart waits 20 s
after sign-in.

## Tuning (`config.json`, created on first run)

| Key | Default | Notes |
|---|---|---|
| `user_name` | `sir` | How it addresses you |
| `backend` | `claude_code` | `claude_code` (your login) or `api` (API key in `.env`) |
| `code_dir` | `C:\PROJECTS` | Where it creates and edits code without asking |
| `model_routing` / `auto_escalate` | `true` / `true` | Haiku/Sonnet/Opus routing and handoff to Opus; `tier_models` changes which model each tier uses |
| `agent_effort` | `medium` | Reasoning effort in Claude Code mode; `high` for tougher coding |
| `model` / `effort` | `claude-opus-5-5` / `low` | Raise effort for harder tasks; `claude-sonnet-5-5` or `claude-haiku-5-5` are cheaper and faster |
| `speaker_threshold` | `0.55` | Lower it if it rejects you, raise it if it accepts others |
| `wake_threshold` | `0.5` | Raise it if it wakes by accident |
| `whisper_model` | `base.en` | `tiny.en` is lighter, `small.en` is more accurate |
| `tts_voice` | `en-GB-RyanNeural` | Any Edge neural voice; falls back to the Windows voice offline |
| `show_cursor` / `cursor_lead_ms` | `true` / `420` | JARVIS's own cursor, and how long it shows before acting |
| `screen_corners` | `active` | Corner brackets: `active`, `always` or `off` |
| `topic_detection` / `topic_model` | `true` / same as `model` | Auto new conversation on topic change; set `topic_model` to `claude-haiku-5-5` for a faster check |
| `hud_position` | `top-right` | Or `top-left`, `bottom-right`, `bottom-left` |

Logs are in `data/jarvis.log` and long-term memory is in `data/memory.json`.

## Layout

```
jarvis/osutil.py     OS layer (Windows + macOS): idle, lock, shell, media, foreground app
jarvis/winfront.py   window management API -> winfront_win.py (Win32) / macfront.py (macOS)
jarvis/assistant.py  listening loop (wake, verify, transcribe, Claude, speak)
jarvis/brain.py      Claude tool-use loop and persona
jarvis/tools.py      PC-control tools
jarvis/speech.py     wake word, Whisper STT, speaker verification, TTS
jarvis/hud.py        transparent click-through overlay
jarvis/audio.py      mic capture, recording, playback
```

## Claude Code relay (updates, questions, permissions)

`install_claude_hooks.py` adds three hooks to `~/.claude/settings.json` (run it with `--remove` to undo).
Every Claude Code session on this PC (desktop app or terminal) then reports to JARVIS:

- **Finished a turn**: "Claude in *project* has finished: *summary*". Reply within ~8 s and your words
  go straight back into that session. Say "no thanks" or nothing and it just stops as usual.
- **Claude asks a question**: the question and options are read out. Say "option two" / "the blue one" /
  your own answer; it is passed to Claude and the dialog is skipped.
- **Permission prompt**: "Claude wants to run a command: ... Shall I allow it?" Say yes or no.

Only your verified voice counts. If you don't answer, or JARVIS is busy, Claude shows its own dialog
and you can answer there. While the Claude app is in front and you're using the PC, JARVIS only shows a
HUD panel and stays quiet. Settings: `relay_*` in config.json.

## Voice lock

The speaker check now uses centred embeddings calibrated against ~90 other voices at enrollment,
and also applies to "Hey Jarvis" itself: someone else saying it is ignored, and someone talking after
your wake word gets "Voice not recognised". Re-run voice enrollment once to calibrate.

## Newer abilities

| Say | What happens |
|---|---|
| "Pull up / open / show me X" | Opens in **your own Chrome profile** (signed in) and brings it to the front. Add "in the background" to keep your current window. |
| "Remember I parked on level 3" … "Where did I park?" | Stored in memory with the date. "What do you know about me?" lists it on the HUD. |
| "What's this?" / "Explain this error" / "Summarise this page" | Screenshot of the screen with your active window, a short answer, and the parts marked on screen. |
| "Be more sarcastic" / "Keep it brief" / "Call me Tony" | Personality settings (`persona_humour`: off, dry, playful, sarcastic; `persona_length`: brief, normal, detailed). |
| "Run a diagnostic" | A scan on the HUD: CPU, GPU, RAM, every drive, battery, drivers, Windows updates, uptime, then a one-line verdict. |
| "Twenty minutes for the pizza" | A countdown ring next to the reactor; pulses in the last 10 seconds. |
| "Put Chrome on the left and Code on the right" / "Send this to the other monitor" / "Clear the desk" | Window arranging (`arrange_windows`). "Restore the desk" undoes clear. |
| "Show me the model" (Blender open) | Exports the selected meshes and shows a rotating holographic wireframe. |
| "Let me know how the render's going" / "Tell me when the download finishes" | Spoken progress about every 25% with time left, and when it's done. |
| "Stand by" | Now also understood when Whisper hears "stand back" / "stamp by". |

Also automatic:
- **Holographic sweeps**: a full-screen boot sweep at start-up and on waking from standby, a power-down effect on standby and quit (`hud_sweeps`).
- **Interrupting** with "Hey Jarvis" while it talks only works for your voice (`barge_voice_check`). The hotkey always works.
- **Alerts**: low battery, internet dropped and back, an app that stopped responding, a Claude Code session waiting on your answer for 5+ minutes (`relay_waiting_remind_min`), and "still working" updates for long background tasks.

### `/jarvis-model-show` (Claude Code skill)

In any Claude Code session, `/jarvis-model-show <what to build>` makes a 3D model (in Blender, or as .obj/.stl from code) and puts it on the HUD. While you're using the PC it appears small on the right; when you've been away from the keyboard for a minute it's a full-screen projection and JARVIS announces it. To show any model by hand:

```
python C:/PROJECTS/JARVIS/jarvis/show_model.py model.obj --label "Drone frame" [--mode full|side] [--seconds 60]
```

### Blender co-pilot, multi-app requests, screen watch

- **Blender**: "Open Blender", then "add a bevel to the selected edges", "how many polys is this?", "make it glossy red", "render a preview" (shown on the HUD). JARVIS launches Blender itself and waits for its link (`open_blender`), so "open Blender and model X" works in one request. Needs the Blender MCP add-on enabled with Auto Start and *Edit > Preferences > System > Network > Allow Online Access* ticked; Steam updates to Blender can reset these.
- **Chained requests**: "Open Blender, go to Chrome, find reference images of the Iron Man suit and model it" runs every step: a Google Images search in your Chrome, reference images loaded into Blender, a blockout-to-detail model, then a preview render and hologram. Say "in the background" to keep talking to JARVIS meanwhile.
- **Other apps**: every MCP server in your Claude Code config (`~/.claude.json`) is available to JARVIS too (`share_claude_mcp`), so connecting an app for Claude Code connects it for JARVIS.
- **"Watch my screen for an hour"** (or "keep an eye on the build"): JARVIS glances at the screen every couple of minutes when it changes, and immediately when an error-looking window appears (even behind other windows), and only speaks up if something is wrong. "Stop watching my screen" ends it.

## License

MIT, see [LICENSE](LICENSE).
