#!/bin/sh
# Archive a Release build and upload it to App Store Connect for TestFlight.
# The build number is a timestamp, so every upload is higher than the last without tracking it anywhere.
set -e
cd "$(dirname "$0")/.."
[ -d tor/WasabiTor.xcframework ] || tor/build.sh # Arti static lib; rerun tor/build.sh after Rust changes
xcodegen generate -q
build=$(date -u +%Y%m%d%H%M)
rm -rf build/store
xcodebuild -quiet -project WasabiPreview.xcodeproj -scheme WasabiPreview -configuration Release \
  -destination 'generic/platform=iOS' -archivePath build/store/WasabiPreview.xcarchive \
  -allowProvisioningUpdates CURRENT_PROJECT_VERSION="$build" archive
cat > build/store/export.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>method</key><string>app-store-connect</string>
<key>destination</key><string>upload</string>
<key>teamID</key><string>XVW345W8QC</string>
<key>signingStyle</key><string>automatic</string>
<key>manageAppVersionAndBuildNumber</key><false/>
</dict></plist>
PLIST
xcodebuild -quiet -exportArchive -archivePath build/store/WasabiPreview.xcarchive \
  -exportOptionsPlist build/store/export.plist -exportPath build/store/out -allowProvisioningUpdates
echo "Uploaded build $build; it shows in App Store Connect, TestFlight, once Apple has processed it."
