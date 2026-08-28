---
name: whats-actually-running
description: "Before diagnosing any behaviour in this repo, prove the running thing is the built thing. Trigger on 'still not working', 'still no drag', 'nothing happened', 'wrong icon', 'not showing up', 'looping', 'X is broken', or any report where the symptom does not match the code you just read. Also trigger before believing a screenshot, a lane's finding, or your own reproduction."
---

# Prove what is running before you believe a symptom

**Run this first. It is one command and it answers the question.**

```sh
./scripts/whats-running.sh
```

Exit 0 means every running thing is current and the symptom is real. Exit 1 names
what is behind and how to fix it. Do not diagnose anything while it exits 1.

## Why this exists

On 2026-08-28 the answer was "no, it is not the built thing" **six times in one
session**, and every one presented as a code defect:

| symptom | actual cause |
| --- | --- |
| "the pipeline is entirely broken", no app icon | four commits never pushed |
| the Dock drew the wrong icon | 8 LaunchServices registrations, 6 loose copies |
| a build failed on a file that was fine | stale generated `.xcodeproj` |
| "still no drag", twice | `/Applications` built 00:22, code changed 00:48 |
| "README Not Found" | the running door was older than the route |
| three Offices in Launchpad | build products indexed by Spotlight |

Three separate blind fixes were written for the drag before anyone checked which
binary was being launched. The drag was never broken.

## The rule

A symptom is evidence about a RUNNING ARTIFACT, never about source. Source and
artifact agree only if something made them agree, and on this machine four
different things can break that: an unpushed commit, an uninstalled build, a
generated project file, and a long-lived server that loaded its routes hours ago.

## When the check is green and the symptom persists

Then it is real, and the next move is a **control experiment, not a patch**.

Build the smallest thing that isolates the mechanism and run it. For the drag it
was a 40-line SwiftUI app with two rows: one plain `.onDrag`, one with the
roster's exact structure. Both dragged fine, which killed the theory three
patches had been built on, in under a minute.

**Two consecutive patches to one file with no change in the symptom means stop
patching.** The next thing you write is a probe, not a fix.

## Driving the real app to get evidence

The app can be driven with synthetic events when a human is not available to
test, and this needs Aria's consent because it takes the screen:

- `cliclick c:x,y` for clicks. It **clamps to the primary display**, so it cannot
  reach a window on a second screen (negative coordinates).
- CGEvent posts at true global coordinates including negatives. A drag needs
  `mouseMoved`, `leftMouseDown`, many small `leftMouseDragged` steps, `leftMouseUp`.
- A drag made of `leftMouseDragged` events is **not a click**: tap gestures
  correctly ignore it. Use `cliclick` for clicks and CGEvent for drags.
- Read the window frame immediately before acting. This window moves and resizes
  between displays on activation, and stale coordinates silently hit empty space,
  which reads exactly like "the feature does not work".
- `ROSTER_DRAG_LOG=1` prints each stage of a roster drag to stderr.
