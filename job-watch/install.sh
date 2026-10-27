#!/bin/bash
# Installs Job Watch to ~/job-watch and schedules it daily at 09:00 (Mac local time) via launchd.
# Usage:  bash install.sh            (install + first run)
#         bash install.sh uninstall  (remove the schedule; keeps ~/job-watch)
set -e
DEST="$HOME/job-watch"
LABEL="com.vatsa.jobwatch"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "$1" = "uninstall" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Schedule removed. Your reports are still in $DEST."
  exit 0
fi

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "python3 not found. Install it with:  xcode-select --install   (or brew install python)"; exit 1
fi

mkdir -p "$DEST/logs"
cp "$(dirname "$0")/job_watch.py" "$DEST/job_watch.py"
chmod +x "$DEST/job_watch.py"

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>$DEST/job_watch.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DEST</string>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer>
  </dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$DEST/logs/run.log</string>
  <key>StandardErrorPath</key><string>$DEST/logs/run.log</string>
</dict></plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Scheduled daily at 09:00. (If the Mac is asleep at 9, it runs when it wakes.)"
echo "Running once now..."
"$PY" "$DEST/job_watch.py" --open
