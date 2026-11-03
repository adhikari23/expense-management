#!/usr/bin/env bash
# Build the Aptiq iPhone app and install it on a connected iPhone (cable or same
# Wi-Fi, unlocked, Developer Mode on). Upgrades in place, so the app's data is kept.
#
#   APTIQ_DIR=~/path/to/Aptiq scripts/install_aptiq_iphone.sh <device-id>
#
# Find <device-id> with: flutter devices
# A free Apple ID signs apps for 7 days: re-run this weekly.
set -euo pipefail

DEVICE="${1:-${APTIQ_DEVICE:-}}"
APP_DIR="${APTIQ_DIR:-}"
[[ -n "$DEVICE" && -n "$APP_DIR" ]] || {
  echo "usage: APTIQ_DIR=<path to Aptiq Flutter project> $0 <device-id>   (device ids: flutter devices)"
  exit 1
}
cd "$APP_DIR"
# Only `flutter build` accepts this flag; habit icons are chosen at runtime.
flutter build ios --release --no-tree-shake-icons
# Not `flutter install`: it uninstalls first, which wipes the app's data on the phone.
xcrun devicectl device install app --device "$DEVICE" build/ios/iphoneos/Runner.app
