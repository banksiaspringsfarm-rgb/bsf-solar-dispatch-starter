#!/bin/bash
# Builds "Solar 3 Sites.app" in ~/Applications: a click-to-open desktop twin of the Android
# 3-site widget. It opens fleet/widget.html (served by the fleet dashboard server on :8790)
# in a compact, chromeless Chrome app window. Run it again on a new Mac (e.g. the Mac mini)
# after the fleet relay + dashboard LaunchAgents are set up there.
#   bash fleet/mac/make_mac_app.sh [url]
set -euo pipefail
URL="${1:-http://localhost:8790/widget.html}"
APP="$HOME/Applications/Solar 3 Sites.app"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/Applications"
rm -rf "$APP"
# Own Chrome profile: a separate instance honours --window-size and keeps the widget's size/position
# without touching Steven's normal Chrome windows.
PROFILE="$HOME/Library/Application Support/BSF Solar Sites"
osacompile -o "$APP" -e "do shell script \"open -na 'Google Chrome' --args --user-data-dir='$PROFILE' --no-first-run --no-default-browser-check --app=$URL --window-size=460,500 --window-position=40,60\""
if [ -f "$HERE/icon.png" ]; then
  T="$(mktemp -d)/icon.iconset"; mkdir -p "$T"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$HERE/icon.png" --out "$T/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$HERE/icon.png" --out "$T/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$T" -o "$APP/Contents/Resources/applet.icns"
  touch "$APP"
fi
echo "Built: $APP  (opens $URL)"
