#!/bin/sh
#
# Is the thing you are testing the thing you built?
#
# On 2026-08-28 the answer was no, six separate times in one session, and each
# one cost real time because the symptom looked like a code defect:
#
#   · four commits never left the machine, so every pipeline lane opened the app
#   · eight LaunchServices registrations of one bundle id, so the Dock drew the
#     icon of a copy nobody installed
#   · a stale generated .xcodeproj, so a build "failed" on a file that was fine
#   · /Applications built at 00:22 against code changed at 00:48, twice, which
#     is the whole reason "still no drag" was reported against a fixed build
#   · a running server older than the route it was being asked for, reported as
#     a missing README
#   · build products indexed by Spotlight, so Launchpad showed three apps
#
# None of that is visible in source. All of it is visible here.
#
#   ./scripts/whats-running.sh
#
# Exit 1 if anything on this machine is behind what is committed, so a lane can
# gate on it. Nothing here writes.
set -u

cd "$(dirname "$0")/.." || exit 1
ROOT="$PWD"
STALE=0
say() { printf '%s\n' "$*"; }
# `date -r <epoch>` is BSD; on a machine with GNU coreutils first on PATH the
# same flag means "read this FILE" and prints an error. python3 is already a
# hard dependency of this repo, so the formatting goes through it.
when() { python3 -c "import sys,time; print(time.strftime('%m-%d %H:%M', time.localtime(int(sys.argv[1]))))" "$1" 2>/dev/null || echo unknown; }
bad() { printf '  ✗ %s\n' "$*"; STALE=1; }
ok()  { printf '  ✓ %s\n' "$*"; }

say "the app"
APP="/Applications/Office.app"
if [ ! -d "$APP" ]; then
  bad "not installed; ./scripts/install.sh"
else
  BUILT_AT=$(stat -f %m "$APP/Contents/MacOS/Office" 2>/dev/null || echo 0)
  # FILE TIMES, not commit times. A commit made after the install can still be
  # already inside the binary, because install.sh compiles the working tree; the
  # first version of this check compared against the commit and called a correct
  # build stale. What decides is whether any source is newer than the binary.
  NEWER=$(find app/Office app/Demo app/project.yml -newer "$APP/Contents/MacOS/Office" -print 2>/dev/null | head -3)
  if [ -n "$NEWER" ]; then
    bad "installed $(when "$BUILT_AT"), but these are newer. You are testing old code: ./scripts/install.sh"
    printf '      %s\n' $NEWER
  else
    ok "installed $(when "$BUILT_AT"), newer than every source it is built from"
  fi
fi

say "copies"
LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
COPIES=$("$LSREG" -dump 2>/dev/null | grep -o '/[^ ]*Office\.app' | sort -u | grep -v '^/Applications/Office.app$')
if [ -n "$COPIES" ]; then
  bad "more than one Office is registered; the Dock and the icon may use any of them:"
  printf '      %s\n' $COPIES
  say "      fix: ./scripts/install.sh (it unregisters build products and deletes them)"
else
  ok "/Applications/Office.app is the only one registered"
fi

say "the door"
PORT="${OFFICE_PORT:-8790}"
PID=$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -1)
if [ -z "$PID" ]; then
  bad "nothing is listening on 127.0.0.1:$PORT"
else
  STARTED=$(ps -p "$PID" -o lstart= 2>/dev/null)
  STARTED_AT=$(python3 -c "
import sys, time, datetime
try: print(int(datetime.datetime.strptime(sys.argv[1].strip(), '%a %b %d %H:%M:%S %Y').timestamp()))
except Exception: print(0)" "$STARTED" 2>/dev/null || echo 0)
  CLIENT=$(git log -1 --format=%ct -- client/ 2>/dev/null || echo 0)
  if [ "$STARTED_AT" -gt 0 ] && [ "$STARTED_AT" -lt "$CLIENT" ]; then
    bad "serving since $(when "$STARTED_AT"), but client/ changed $(when "$CLIENT"). It cannot answer a route it never loaded."
    say "      fix: launchctl kickstart -k gui/\$(id -u)/com.aria.office-serve"
  else
    ok "serving since $(when "$STARTED_AT"), not older than client/"
  fi
fi

say "the commits"
if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
  git fetch -q origin 2>/dev/null
  AHEAD=$(git rev-list --count '@{u}'..HEAD 2>/dev/null || echo 0)
  if [ "${AHEAD:-0}" -gt 0 ]; then
    bad "$AHEAD commit(s) never pushed; anything cloning this repo gets older code"
  else
    ok "nothing unpushed"
  fi
fi

say ""
[ "$STALE" -eq 0 ] && say "everything running is current." || say "something running is behind the code. Fix that before believing any symptom."
exit "$STALE"
