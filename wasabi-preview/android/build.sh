#!/bin/sh
# Builds the Android app: Arti (tor/, the same crate the iOS app links) as libwasabitor.so for arm64, then the APK.
# Needs: rustup target aarch64-linux-android, cargo-ndk, the Android SDK (platform 37.2, NDK 29) and JDK 21.
# Release builds are signed with ~/.config/wasabi-mobile/release.p12 when it exists (password in the macOS Keychain,
# item "wasabi-mobile-release"), otherwise with the debug key.
set -eu
cd "$(dirname "$0")"
export PATH="/opt/homebrew/opt/rustup/bin:$HOME/.cargo/bin:$PATH"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
export ANDROID_NDK_HOME="${ANDROID_NDK_HOME:-$(ls -d "$ANDROID_HOME"/ndk/* | sort -V | tail -1)}"
export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home}"
key="$HOME/.config/wasabi-mobile/release.p12"
if [ -f "$key" ] && [ -z "${WASABI_KEYSTORE:-}" ]; then
  export WASABI_KEYSTORE="$key"
  WASABI_KEYSTORE_PASSWORD="$(security find-generic-password -s wasabi-mobile-release -w)"
  export WASABI_KEYSTORE_PASSWORD
fi
(cd ../tor && cargo ndk -t arm64-v8a --platform 30 -o ../android/app/src/main/jniLibs build --release --quiet)
./gradlew -q :app:testDebugUnitTest ":app:assemble${1:-Release}"
ls -lh app/build/outputs/apk/*/*.apk
