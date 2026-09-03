# Next session, read this first

🔧 ready to build. Start in `/Users/slowember/Developer/Vaults/CodingVault/nexus-office`, one session,
no swarm. Spec of record: `docs/foundation.md` (Aria's design, not a proposal). Steps 1 and 2 are
merged (`6ef2991`): ledger schema v1, tower tick, script flights, crash_tower v0, 57 tests.

## Do, in order (risk class in brackets)

0. [cheap, reversible] crash_tower case: radio (a stub that blocks forever on every call) hung
   while a flight exits; assert the flight terminates, records its result, releases leases, and a
   second flight runs. Principle: communications may fail; lifecycle must continue. Then, as
   tower takes over presence and stop, remove the hcom Stop/SubagentStop/SessionEnd hooks.

1. [expensive, reversible] Step 3, landing for script flights: hangar clone, spool to branch, push,
   `verified → applying → applied` reconcile against GitHub. Prove on one real airline first: the
   CollabVault intelligence jobs (`_meta/services/intelligence-scrape.sh` via `email-runner.sh`)
   currently write untyped files into the human checkout; make them a plan whose flight lands a
   commit. Old job stays until three consecutive landed flights.
2. [expensive, reversible] Step 4, migrate plists one airline at a time; `jobctl` and the 43
   plists remain until each plan has landed real output. Never delete a mechanism the real-work
   lane is using (see Coexistence in foundation.md).
3. [architectural, already reviewed] Step 5, aircraft for claude, codex, antigravity in hangars;
   resolver and reviewer flights; gates as the last rung.
4. Step 6 radio, step 7 watchers and the Office's five columns, step 8 deletions with a test each.

## Facts the next session cannot see

- `tbs-agy-keychain` is SKIPPED by Aria's ruling: the file-token fallback works and the bridge is
  slated for deletion. Spend nothing more on it unless Antigravity actually fails without it.
- hcom hooks are capped at 15s and fail open in `~/.claude/settings.json` (13) and
  `~/.codex/hooks.json` (5; added 2026-09-03 by the new session, the earlier claim was wrong: they
  had no timeout). `HCOM_TIMEOUT` is 15 in toml, AND every registered instance's `wait_timeout`
  in `~/.hcom/hcom.db` was reset from 86400 to 15 (`hcom config -i <name> timeout 15`): the toml
  value never touched existing rows. If hcom re-installs its hooks, re-check both.

- Kernel 9.8.2 is installed in both silos; the new session runs it. First act: delete the 21
  squash-merged local branches the old guard could not classify (`git branch -D` now consults
  `gh`): tbs-www 8, tbs-landing 3, tbs-curriculum 4, nexus-office 6, thinking-brain-school 3
  (`aria/care-antigravity-runtime`, `aria/pipeline-issue-11`, `feature/w`).
- `autobranch.sh` is retired; the Vaults root stays on main. `wip-mirror` post-commit stays.
- hcom holds ~120 dead registrations. `hcom --go reset` clears them but the real-work lane is on
  hcom; do it only when `hcom list` shows nothing active but yourself.
- A crash_tower finding: a `Popen` child nobody waits on is a zombie whose pid still answers
  `kill -0`; tower double-forks so flights outlive tower. Keep that property.
- Schema gaps the builder recorded: pause state and `on:` high-water marks are derived from
  events, no `state` table. Decide in step 3 whether a `state` table is a field or a primitive.
- Coexistence: a separate lane ships product in these folders. Never revert or sweep its changes.

## What not to repeat today
- Gating a shell step on `| tail -1` (tests tail's status); the house failure is reading the
  status of the wrong process in a pipeline. Three fixes today were that bug.
- Reproducing a keychain failure with a write call: it raises a GUI dialog on Aria's screen.
- Letting a `&&` chain with `git tag` fail silently and continuing to delete the branch.
