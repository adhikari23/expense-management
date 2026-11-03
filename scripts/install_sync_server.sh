#!/usr/bin/env bash
# Keep the sync server (serve_for_aptiq.py, port 8000) running whenever you are
# logged in to this Mac, using a per-user LaunchAgent.
#
#   scripts/install_sync_server.sh            # install / reinstall
#   scripts/install_sync_server.sh --remove   # uninstall
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.expense-ledger.sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="$REPO/.venv/bin/python"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [[ "${1:-}" == "--remove" ]]; then
  rm -f "$PLIST"
  echo "Removed $LABEL"
  exit 0
fi

[[ -x "$PYTHON" ]] || { echo "Create the virtualenv first: python3 -m venv .venv"; exit 1; }
mkdir -p "$REPO/data/ledger" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$REPO/serve_for_aptiq.py</string>
    <string>8000</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$REPO/data/ledger/server.log</string>
  <key>StandardErrorPath</key><string>$REPO/data/ledger/server.log</string>
</dict>
</plist>
PLIST
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL -> http://$(scutil --get LocalHostName).local:8000/transactions.csv"
echo "Log: $REPO/data/ledger/server.log"
