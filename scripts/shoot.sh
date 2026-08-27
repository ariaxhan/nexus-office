#!/bin/sh
#
# The eyes, for the Mac app.
#
# A build passing proves nothing about a room. Every defect this project has had
# was invisible in source and obvious on screen, so this builds the app, runs it
# against the demo fixture, and photographs eight framings into shots/.
#
# Seven come out of one run. The eighth is a second run with --light, because
# the office follows the system appearance now and there is no way to be in two
# appearances at once: a Mac set to Dark would otherwise photograph the light
# room never, which is the same as not having built it.
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

cd "$(dirname "$0")/.."
ROOT="$PWD"

command -v xcodegen >/dev/null 2>&1 || {
  echo "shoot: xcodegen is not installed (brew install xcodegen)" >&2
  exit 1
}

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

echo "shoot: running the seven dark framings"
shoot_run

# The same room with the lights on. A separate process because an appearance is
# forced on the whole app before its window exists, and a window that has
# already drawn itself once is a race between the redraw and the shutter.
echo "shoot: running the light framing"
shoot_run --light

MISSING=0
for framing in roster desk gate needs wall putaway attach light; do
  if [ -f "$ROOT/shots/app-$framing.png" ]; then
    echo "shoot: shots/app-$framing.png"
  else
    echo "shoot: MISSING shots/app-$framing.png" >&2
    MISSING=1
  fi
done

[ "$MISSING" -eq 0 ] || exit 1
echo "shoot: now open them. A framing nobody looks at is a framing that rots."
