#!/bin/sh
#
# One Office, in /Applications, with the icon.
#
# This exists because installing by hand produced eight LaunchServices
# registrations of the same bundle id and six loose copies, none of them
# installed, and macOS drew whichever icon it found first. The same accident
# also means the app you double click is not the app you just built, so a fix
# can be on main, in a build, and still "not working" because the copy on the
# Dock is from before it.
#
#   ./scripts/install.sh
#
# Release, not Debug: a Debug build carries a dylib next to the binary and is
# slower to launch for no benefit to somebody using the app.
set -eu

cd "$(dirname "$0")/.."
ROOT="$PWD"
DEST="/Applications/Office.app"

command -v xcodegen >/dev/null 2>&1 || {
  echo "install: xcodegen is not installed (brew install xcodegen)" >&2
  exit 1
}
( cd app && xcodegen generate --quiet )

echo "install: building Release"
xcodebuild -project app/Office.xcodeproj \
           -scheme Office \
           -configuration Release \
           -derivedDataPath app/build \
           build -quiet

BUILT="$ROOT/app/build/Build/Products/Release/Office.app"
[ -d "$BUILT" ] || { echo "install: nothing built at $BUILT" >&2; exit 1; }

# Quit the running copy first. Copying over a running bundle leaves a half
# replaced app that launches with the old code and the new icon, which is the
# most confusing possible outcome.
osascript -e 'quit app "Office"' >/dev/null 2>&1 || true

echo "install: replacing $DEST"
rm -rf "$DEST"
cp -R "$BUILT" "$DEST"

# Every other copy on this machine is a stray: an old DerivedData product or a
# pipeline worktree that has since been thrown away. They are what makes
# LaunchServices pick the wrong icon and the wrong binary, so the registration
# is rebuilt against the one that is installed.
LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
"$LSREG" -kill -r -domain local -domain system -domain user >/dev/null 2>&1 || true
"$LSREG" -f "$DEST" >/dev/null 2>&1 || true

# A build product is not an installed app. `app/build` holds a Debug copy for
# the shot harness and the Release copy this script just installed from, and
# registering either is what makes the Dock, Spotlight and the icon pick the
# wrong one. They are unregistered rather than deleted, because deleting them
# means the next shoot rebuilds the whole app for nothing.
"$LSREG" -dump 2>/dev/null | grep -o '/[^ ]*Office\.app' | sort -u | while read -r found; do
  [ "$found" = "$DEST" ] && continue
  "$LSREG" -u "$found" >/dev/null 2>&1 || true
done

echo "install: $DEST"
LEFT="$("$LSREG" -dump 2>/dev/null | grep -o '/[^ ]*Office\.app' | sort -u | grep -v "^$DEST\$" || true)"
if [ -n "$LEFT" ]; then
  echo "install: still registered, and should not be:"
  echo "$LEFT"
else
  echo "install: it is the only Office registered"
fi
