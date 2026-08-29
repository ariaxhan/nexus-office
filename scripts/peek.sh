#!/bin/sh
#
# One picture of the LIVE Office window, as it is right now.
#
# `npm run shot` photographs the demo floor. This photographs the real one:
# the installed app, the real door, whatever Aria is actually looking at. Use
# it after `nexus open`, after a hook fires, after any "it is not showing"
# report, before believing the code.
#
#   ./scripts/peek.sh [out.png]     default: shots/live.png
#
# Two things that cost an hour each, handled here:
#   - `screencapture -l` returns "could not create image" from a sleeping
#     display, with exit 1 and no hint. caffeinate -u wakes it first.
#   - The window id comes from CGWindowList, not from System Events, which
#     returns nothing for a SwiftUI-hosted NSWindow without a title bar.
set -eu

cd "$(dirname "$0")/.."
OUT="${1:-shots/live.png}"
mkdir -p "$(dirname "$OUT")"

WID="$(swift - <<'SWIFT' 2>/dev/null
import CoreGraphics
let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID) as! [[String: Any]]
for w in list where (w["kCGWindowOwnerName"] as? String) == "Office" {
    let b = w["kCGWindowBounds"] as! [String: Any]
    if (b["Width"] as! Double) > 400 { print(w["kCGWindowNumber"]!); break }
}
SWIFT
)"
[ -n "$WID" ] || { echo "peek: no Office window on screen (is /Applications/Office.app running?)" >&2; exit 1; }

caffeinate -u -t 5 &
sleep 1.5
screencapture -x -o -l "$WID" "$OUT"
echo "peek: $OUT"
