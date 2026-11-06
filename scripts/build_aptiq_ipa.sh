#!/usr/bin/env bash
# Package the Aptiq iPhone app as an .ipa for AltStore / SideStore.
# AltStore re-signs it with your Apple ID and refreshes it before the 7 days
# run out, so this is only needed when the app's code changes.
#
#   APTIQ_DIR=~/path/to/Aptiq scripts/build_aptiq_ipa.sh             # build, then package
#   APTIQ_DIR=~/path/to/Aptiq scripts/build_aptiq_ipa.sh --no-build  # package the last build
#
# Output: $APTIQ_DIR/build/Aptiq.ipa (also copied to ~/Downloads/Aptiq.ipa)
set -euo pipefail

APP_DIR="${APTIQ_DIR:-}"
[[ -n "$APP_DIR" ]] || { echo "usage: APTIQ_DIR=<path to Aptiq Flutter project> $0 [--no-build]"; exit 1; }
cd "$APP_DIR"

if [[ "${1:-}" != "--no-build" ]]; then
  # Only `flutter build` accepts this flag; habit icons are chosen at runtime.
  flutter build ios --release --no-tree-shake-icons
fi
APP="build/ios/iphoneos/Runner.app"
[[ -d "$APP" ]] || { echo "No build at $APP - run without --no-build"; exit 1; }

# An .ipa is just Payload/<App>.app zipped; AltStore replaces the signature.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir "$STAGE/Payload"
cp -R "$APP" "$STAGE/Payload/"
rm -f build/Aptiq.ipa
(cd "$STAGE" && /usr/bin/zip -qry "$APP_DIR/build/Aptiq.ipa" Payload)
cp build/Aptiq.ipa "$HOME/Downloads/Aptiq.ipa"
echo "Built $APP_DIR/build/Aptiq.ipa ($(du -h build/Aptiq.ipa | cut -f1)) - copied to ~/Downloads/Aptiq.ipa"
