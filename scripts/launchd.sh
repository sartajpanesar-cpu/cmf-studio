#!/usr/bin/env bash
# Install / remove watchd as a per-user LaunchAgent. Run by the owner.
#   bash scripts/launchd.sh install | uninstall | status | logs
set -euo pipefail
cd "$(dirname "$0")/.."

LABEL="com.cmfstudio.watchd"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BIN="$PWD/.venv/bin/watchd"
LOGDIR="$HOME/Library/Logs/cmf-studio"
DOMAIN="gui/$(id -u)"

install() {
  [ -x "$BIN" ] || { echo "missing $BIN: run 'uv sync' first"; exit 1; }
  mkdir -p "$LOGDIR" "$(dirname "$PLIST")"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$BIN</string></array>
  <key>WorkingDirectory</key><string>$PWD</string>
  <key>RunAtLoad</key><true/>
  <!-- restart if it exits for any reason; launchd waits ThrottleInterval between restarts -->
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <!-- Interactive: keep App Nap from throttling the BLE keepalive -->
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$LOGDIR/watchd.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/watchd.log</string>
</dict>
</plist>
EOF
  plutil -lint "$PLIST" >/dev/null
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$PLIST"
  echo "installed $PLIST; logs: $LOGDIR/watchd.log"
  echo "If macOS asks whether python3.12 may use Bluetooth, allow it."
}

case "${1:-}" in
  install) install ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed $LABEL" ;;
  status) launchctl print "$DOMAIN/$LABEL" | grep -E 'state =|pid =|last exit code' || echo "not loaded" ;;
  logs) tail -n 50 -f "$LOGDIR/watchd.log" ;;
  *) echo "usage: $0 install|uninstall|status|logs"; exit 2 ;;
esac
