#!/usr/bin/env bash
# One-time setup on macOS: virtualenv, dependencies, model downloads.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

# --- Python 3.13 with Tk (the HUD uses tkinter) ------------------------------
PY="$(command -v python3.13 || true)"
if [ -z "$PY" ]; then
  echo "python3.13 not found on PATH. Install it with:  brew install python@3.13 python-tk@3.13" >&2
  exit 1
fi
if ! "$PY" -c "import tkinter" 2>/dev/null; then
  echo "$PY has no tkinter. Install it with:  brew install python-tk@3.13" >&2
  echo "(or use a Python build that ships Tk, then re-run ./setup.sh)" >&2
  exit 1
fi

if [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi
VPY="$ROOT/.venv/bin/python"
if ! "$VPY" -c "import tkinter" 2>/dev/null; then
  echo "The existing .venv has no tkinter; delete .venv and re-run ./setup.sh" >&2
  exit 1
fi

"$VPY" -m pip install --upgrade pip -q
"$VPY" -m pip install -r requirements.txt

# --- models (gitignored, fetched once) ---------------------------------------
"$VPY" -c "import openwakeword.utils as u; u.download_models(model_names=['hey_jarvis'])"
"$VPY" -c "from jarvis.speech import ensure_speaker_model; ensure_speaker_model()"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env - put your Anthropic API key in it."
fi
chmod +x autostart.sh 2>/dev/null || true

cat <<MSG

Setup done. Next steps:

  1. Record your voice:   $VPY -m jarvis.enroll
  2. Start JARVIS:        $VPY run.py

macOS permissions: the first time JARVIS needs each one, macOS asks. Grant them to the
app that launches JARVIS (Terminal / iTerm / your IDE, or the python binary itself when
started by the autostart LaunchAgent) under System Settings > Privacy & Security:

  - Microphone         (wake word and voice commands)
  - Camera             (presence detection and face ID)
  - Screen Recording   (window titles, "watch my screen", screenshots)
  - Accessibility      (window management, global hotkey, typing and clicking)
  - Input Monitoring   (the global push-to-talk hotkey)
  - Automation         (controlling Spotify/Music and hiding apps, asked on first use)

Optional: ./autostart.sh   start JARVIS when you log in  (./autostart.sh --remove to undo)
MSG
