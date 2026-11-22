#!/bin/bash
# Re-signs and reinstalls Wasabi Mobile before its free 7-day profile expires. iOS forgets the "trusted developer"
# choice when the last profile from that developer expires, so a refresh in time means never trusting it again.
# Run by ~/Library/LaunchAgents/nl.kravens.wasabi-mobile-refresh.plist every few hours; does nothing until the profile
# has less than REFRESH_HOURS left, then waits for the paired iPhone (cable or same Wi-Fi) to be reachable.
#
# macOS keeps background jobs out of ~/Documents, so `refresh-iphone.sh --sync` (run by hand after installing a new
# version) copies the source to Application Support and points the LaunchAgent there; the refresh then re-signs
# exactly the version that is on the phone.
set -uo pipefail

if [ "${1:-}" = --sync ]; then
  SRC=$(cd "$(dirname "$0")/.." && pwd)
  COPY="$HOME/Library/Application Support/wasabi-mobile-refresh"
  PLIST=$HOME/Library/LaunchAgents/nl.kravens.wasabi-mobile-refresh.plist
  mkdir -p "$COPY/build"
  rsync -a --delete --exclude /build --exclude /.git --exclude /tor/target --exclude /release/*.ipa --exclude /tor-client.auth_private --exclude /ota/out --exclude /ota/key.pem --exclude /ota/cert.pem "$SRC/" "$COPY/"
  cp -R "$SRC/build/dd" "$COPY/build/" 2>/dev/null # keeps the installed build's profile date, so no needless reinstall
  cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>nl.kravens.wasabi-mobile-refresh</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$COPY/desktop/refresh-iphone.sh</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>StartInterval</key><integer>10800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/wasabi-mobile-refresh.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/wasabi-mobile-refresh.log</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/nl.kravens.wasabi-mobile-refresh" 2>/dev/null
  launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "synced to $COPY; refresh agent loaded"
  exit
fi

REPO=$(cd "$(dirname "$0")/.." && pwd)
APP=$REPO/build/dd/Build/Products/Debug-iphoneos/WasabiPreview.app
REFRESH_HOURS=${REFRESH_HOURS:-72}
export DEVELOPER_DIR=${DEVELOPER_DIR:-$(xcode-select -p)}

log() { echo "$(date '+%F %T') $*"; }
notify() { osascript -e "display notification \"$1\" with title \"Wasabi Mobile\"" >/dev/null 2>&1; }

expiry=$(security cms -D -i "$APP/embedded.mobileprovision" 2>/dev/null | plutil -extract ExpirationDate raw - 2>/dev/null)
left=$(( ( $(date -j -u -f '%Y-%m-%dT%H:%M:%SZ' "${expiry:-1970-01-01T00:00:00Z}" +%s) - $(date +%s) ) / 3600 ))
if (( left > REFRESH_HOURS )); then exit 0; fi

# The first paired, physical iPhone with Developer Mode on that CoreDevice can reach right now.
udid=$(xcrun devicectl list devices --json-output - 2>/dev/null | python3 -I -c '
import json, sys
for d in json.load(sys.stdin)["result"]["devices"]:
    c, h = d["connectionProperties"], d["hardwareProperties"]
    if h.get("reality") == "physical" and h.get("platform") == "iOS" and c.get("pairingState") == "paired" \
            and d["deviceProperties"].get("developerModeStatus") == "enabled" and c.get("transportType"):
        print(h["udid"]); break')
if [ -z "$udid" ]; then
  log "profile has ${left}h left; iPhone not reachable"
  if (( left < 24 )); then notify "Expires in ${left}h. Unlock the iPhone near this Mac so it can be refreshed."; fi
  exit 0
fi

log "profile has ${left}h left; refreshing"
# Xcode keeps using a cached profile until it has expired; drop this app's so it fetches a fresh 7-day one.
for f in "$HOME/Library/Developer/Xcode/UserData/Provisioning Profiles"/*.mobileprovision; do
  security cms -D -i "$f" 2>/dev/null | grep -q '\.nl\.kravens\.WasabiPreview</string>' && rm -f "$f"
done
cd "$REPO" || exit 1
if ! xcodebuild -project WasabiPreview.xcodeproj -scheme WasabiPreview -destination "id=$udid" -destination-timeout 120 \
    -configuration Debug -derivedDataPath build/dd -allowProvisioningUpdates -allowProvisioningDeviceRegistration build \
    >"$REPO/build/refresh-build.log" 2>&1; then
  log "build failed, see build/refresh-build.log"; notify "Refresh build failed; see build/refresh-build.log."; exit 1
fi
if xcrun devicectl device install app --device "$udid" "$APP" >/dev/null 2>&1; then
  log "installed; profile now expires $(security cms -D -i "$APP/embedded.mobileprovision" | plutil -extract ExpirationDate raw -)"
else
  log "install failed (iPhone locked?); next run tries again"
fi
