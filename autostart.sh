#!/usr/bin/env bash
# Enable or disable JARVIS starting when you log in to macOS (a LaunchAgent).
#   ./autostart.sh           -> enable
#   ./autostart.sh --remove  -> disable
# Set JARVIS_LAUNCHAGENT_DIR to write the plist somewhere else (testing); the plist is
# then only written/removed, never loaded.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
LABEL="local.jarvis.assistant"
DIR="${JARVIS_LAUNCHAGENT_DIR:-$HOME/Library/LaunchAgents}"
PLIST="$DIR/$LABEL.plist"
DOMAIN="gui/$(id -u)"
LIVE=1; [ -n "${JARVIS_LAUNCHAGENT_DIR:-}" ] && LIVE=0

if [ "${1:-}" = "--remove" ]; then
  if [ "$LIVE" = 1 ]; then launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true; fi
  rm -f "$PLIST"
  echo "JARVIS autostart disabled."
  exit 0
fi

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "No .venv found - run ./setup.sh first." >&2; exit 1; }
RUN="$ROOT/run.py"; [ -f "$RUN" ] || RUN="$ROOT/run.pyw"
mkdir -p "$DIR" "$ROOT/data"

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
  <key>RunAtLoad</key>
  <true/>
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
  echo "Wrote $PLIST (not loaded)."
fi
