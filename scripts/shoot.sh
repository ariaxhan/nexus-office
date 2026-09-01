#!/bin/sh
#
# The eyes, for the Mac app.
#
# A build passing proves nothing about a room. Every defect this project has had
# was invisible in source and obvious on screen, so this builds the app, runs it
# against the demo fixture, and photographs twenty-one framings into shots/.
#
# Eighteen come out of one run. The nineteenth is a second run with --light,
# because the office follows the system appearance now and there is no way to be
# in two appearances at once: a Mac set to Dark would otherwise photograph the
# light room never, which is the same as not having built it.
#
#   ./scripts/shoot.sh
#
# It needs no account, no session, no pipeline and no network: the whole floor
# comes from app/Demo/demo.json. A check that needs credentials is a check that
# stops running.
#
# It does need a screen that is awake. `screencapture` returns a black frame
# from a sleeping display and no error, which is the single most misleading
# failure available here, so caffeinate holds the display up for the run.
#
# Then LOOK at the PNGs. That is the entire point of this file existing.

set -eu

# Two ways to take the pictures, and only one of them touches the desk.
#
# `--offscreen` never activates a window, never makes one key, and never moves
# the cursor: the office is ordered to the back and photographed out of the
# window server by name. That is safe while somebody is working, so it is the
# path an unattended lane is allowed to take, and it needs no consent because
# it asks for nothing.
#
# The visible path drives real windows on the logged in desktop and warps the
# pointer, so it still needs a person at the Mac who said yes: a terminal AND
# the variable. An unattended lane that wants eyes uses --offscreen.
QUIET=""
for arg in "$@"; do
  [ "$arg" = "--offscreen" ] && QUIET="--offscreen"
done

if [ -z "$QUIET" ]; then
  if [ ! -t 0 ] || [ "${NEXUS_OFFICE_ALLOW_VISIBLE_SHOTS:-}" != "1" ]; then
    echo "shoot: refused: this command opens and drives visible Office windows" >&2
    echo "shoot: while at the Mac, run NEXUS_OFFICE_ALLOW_VISIBLE_SHOTS=1 npm run shot" >&2
    echo "shoot: unattended, run ./scripts/shoot.sh --offscreen instead" >&2
    exit 2
  fi
fi

cd "$(dirname "$0")/.."
ROOT="$PWD"

command -v xcodegen >/dev/null 2>&1 || {
  echo "shoot: xcodegen is not installed (brew install xcodegen)" >&2
  exit 1
}

# Spotlight indexes a build product the same as an installed app, and Launchpad
# reads Spotlight, not LaunchServices. That is why three Offices kept appearing
# after the duplicates were unregistered: unregistering hides a copy from the
# Dock and the icon, and does nothing to the index. This marker file stops the
# whole build directory from ever being indexed, so a build stays a build.
mkdir -p "$ROOT/app/build"
: > "$ROOT/app/build/.metadata_never_index"

echo "shoot: generating the project"
( cd app && xcodegen generate --quiet )

echo "shoot: building"
xcodebuild -project app/Office.xcodeproj \
           -scheme Office \
           -configuration Debug \
           -derivedDataPath app/build \
           build -quiet

APP="$ROOT/app/build/Build/Products/Debug/Office.app/Contents/MacOS/Office"
[ -x "$APP" ] || { echo "shoot: no binary at $APP" >&2; exit 1; }

mkdir -p "$ROOT/shots"
rm -f "$ROOT"/shots/app-*.png

# Hold the display awake for the runs, and put it back when we are done.
caffeinate -u -t 420 &
CAFFEINE=$!
trap 'kill "$CAFFEINE" 2>/dev/null || true' EXIT
sleep 1

# A watchdog, because a launch that never gets a window produces no output and
# no error, and a harness that hangs for an hour is worse than one that fails.
shoot_run() {
  "$APP" --demo "$ROOT/app/Demo/demo.json" --shot-mode --shots "$ROOT/shots" "$@" &
  OFFICE=$!
  ( sleep 90; kill "$OFFICE" 2>/dev/null ) &
  WATCHDOG=$!
  wait "$OFFICE" 2>/dev/null || true
  { kill "$WATCHDOG" 2>/dev/null; wait "$WATCHDOG" 2>/dev/null; } || true
}

echo "shoot: running the twenty dark framings"
shoot_run $QUIET

# The same room with the lights on. A separate process because an appearance is
# forced on the whole app before its window exists, and a window that has
# already drawn itself once is a race between the redraw and the shutter.
echo "shoot: running the light framing"
shoot_run --light $QUIET

MISSING=0
for framing in roster faces desk gate needs wall library clock pipeline context bigger putaway feed deskfeed automation sessions reactions readme attach settings compare light; do
  if [ -f "$ROOT/shots/app-$framing.png" ]; then
    echo "shoot: shots/app-$framing.png"
  else
    echo "shoot: MISSING shots/app-$framing.png" >&2
    MISSING=1
  fi
done

[ "$MISSING" -eq 0 ] || exit 1
# Same reason as install.sh: a build product left on disk is indexed by
# Spotlight and shows up in Launchpad as another copy of the app.
rm -rf "$ROOT/app/build/Build/Products/Debug/Office.app"

echo "shoot: now open them. A framing nobody looks at is a framing that rots."
