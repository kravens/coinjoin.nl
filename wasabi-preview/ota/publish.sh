#!/bin/sh
# Build a development-signed IPA and serve it to tailnet devices as an over-the-air install link.
set -e
cd "$(dirname "$0")/.."
[ -d tor/WasabiTor.xcframework ] || tor/build.sh # Arti static lib; rerun tor/build.sh after Rust changes
xcodegen generate -q
xcodebuild -quiet -project WasabiPreview.xcodeproj -scheme WasabiPreview -configuration Debug \
  -destination 'generic/platform=iOS' -archivePath build/WasabiPreview.xcarchive -allowProvisioningUpdates archive
rm -rf ota/out
# This Mac's Tailscale IP; the self-signed install cert is made for this IP only and stays local (gitignored).
host=${OTA_HOST:-$(/Applications/Tailscale.app/Contents/MacOS/Tailscale ip -4)}
[ -f ota/cert.pem ] || openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 825 -keyout ota/key.pem -out ota/cert.pem \
  -subj "/CN=Wasabi Preview install $host" -addext "subjectAltName=IP:$host" -addext "basicConstraints=critical,CA:TRUE" \
  -addext "nameConstraints=critical,permitted;IP:$host/255.255.255.255" -addext "keyUsage=critical,digitalSignature,keyCertSign" -addext "extendedKeyUsage=serverAuth"
sed "s/HOST/$host/g" ota/export.plist > build/export.plist
xcodebuild -quiet -exportArchive -archivePath build/WasabiPreview.xcarchive -exportOptionsPlist build/export.plist \
  -exportPath ota/out -allowProvisioningUpdates
sips -s format png -z 57 57 /System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/GenericApplicationIcon.icns --out ota/out/icon.png >/dev/null
openssl x509 -in ota/cert.pem -outform der -out ota/out/install.cer
link="itms-services://?action=download-manifest&url=https://$host:8443/manifest.plist"
cat > ota/out/index.html <<HTML
<meta name="viewport" content="width=device-width">
<h1><a href="$link">Install Wasabi Preview</a></h1>
<p>First time: <a href="http://$host:8080/install.cer">get the certificate</a>, install it under Settings, General, VPN &amp; Device Management,
then turn it on under Settings, General, About, Certificate Trust Settings.</p>
HTML
pkill -f 'ota/serve.py' || true
nohup python3 -I ota/serve.py ota/out >/dev/null 2>&1 &
ts=/Applications/Tailscale.app/Contents/MacOS/Tailscale
$ts serve --bg --tcp 8080 tcp://127.0.0.1:18080 >/dev/null
$ts serve --bg --tcp 8443 tcp://127.0.0.1:18443 >/dev/null
echo "Open on the iPhone: http://$host:8080/  (install link works over https://$host:8443/)"
