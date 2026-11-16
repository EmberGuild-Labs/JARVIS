#!/usr/bin/env bash
# Enable or disable JARVIS starting when you log in to macOS (a LaunchAgent).
#   ./autostart.sh           -> enable
#   ./autostart.sh --remove  -> disable
# Add --no-load to only write/remove the plist (takes effect at next login, and doesn't
# start or stop the copy that's running now; used by the menu-bar "Start at login" item).
# Set JARVIS_LAUNCHAGENT_DIR to write the plist somewhere else (testing); the plist is
# then only written/removed, never loaded.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
LABEL="local.jarvis.assistant"
DIR="${JARVIS_LAUNCHAGENT_DIR:-$HOME/Library/LaunchAgents}"
PLIST="$DIR/$LABEL.plist"
DOMAIN="gui/$(id -u)"
LIVE=1; [ -n "${JARVIS_LAUNCHAGENT_DIR:-}" ] && LIVE=0
REMOVE=0
for a in "$@"; do
  case "$a" in
    --remove) REMOVE=1 ;;
    --no-load) LIVE=0 ;;
  esac
done

if [ "$REMOVE" = 1 ]; then
  if [ "$LIVE" = 1 ]; then launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true; fi
  rm -f "$PLIST"
  echo "JARVIS autostart disabled."
  exit 0
fi

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "No .venv found - run ./setup.sh first." >&2; exit 1; }
RUN="$ROOT/run.py"; [ -f "$RUN" ] || RUN="$ROOT/run.pyw"
mkdir -p "$DIR" "$ROOT/data"

# launchd starts agents with a bare PATH (/usr/bin:/bin:...), so the Claude Code CLI,
# Homebrew and node (for the browser tools) wouldn't be found. Bake in a real PATH.
P=""
add() { [ -n "$1" ] && [ -d "$1" ] && case ":$P:" in *":$1:"*) ;; *) P="${P:+$P:}$1" ;; esac; }
for tool in claude node npx; do
  t="$(command -v "$tool" 2>/dev/null || true)"; [ -n "$t" ] && add "$(dirname "$t")"
done
add "$HOME/.local/bin"; add /opt/homebrew/bin; add /opt/homebrew/sbin; add /usr/local/bin
add /usr/bin; add /bin; add /usr/sbin; add /sbin

# XML-escape paths (the repo path contains a space; & < > would break the plist)
esc() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(esc "$PY")</string>
    <string>$(esc "$RUN")</string>
    <string>--startup</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$(esc "$ROOT")</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$(esc "$P")</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ThrottleInterval</key>
  <integer>30</integer>
  <key>ProcessType</key>
  <string>Interactive</string>
  <key>StandardOutPath</key>
  <string>$(esc "$ROOT")/data/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$(esc "$ROOT")/data/launchd.err.log</string>
</dict>
</plist>
PLIST

if [ "$LIVE" = 1 ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$PLIST"
  echo "JARVIS will start automatically when you log in ($PLIST)."
  echo "Grant Microphone/Camera/Screen Recording/Accessibility to $PY if macOS asks."
else
  echo "Wrote $PLIST (starts at next login)."
fi
