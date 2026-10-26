#!/bin/sh
# Builds libwasabitor.a for iPhone and simulator and wraps both in WasabiTor.xcframework.
set -eu
cd "$(dirname "$0")"
export PATH="/opt/homebrew/opt/rustup/bin:$HOME/.cargo/bin:$PATH"
export IPHONEOS_DEPLOYMENT_TARGET=18.0
for target in aarch64-apple-ios aarch64-apple-ios-sim; do
  cargo build --release --quiet --target "$target"
done
rm -rf WasabiTor.xcframework
xcodebuild -create-xcframework \
  -library target/aarch64-apple-ios/release/libwasabitor.a -headers include \
  -library target/aarch64-apple-ios-sim/release/libwasabitor.a -headers include \
  -output WasabiTor.xcframework >/dev/null
du -sh WasabiTor.xcframework/*/libwasabitor.a
