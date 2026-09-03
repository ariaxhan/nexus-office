# Next session, read this first

🔧 ready to build. Start in `/Users/slowember/Developer/Vaults/CodingVault/nexus-office`, one session,
no swarm. Spec of record: `docs/foundation.md` (Aria's design, not a proposal). Steps 1 and 2 are
merged (`6ef2991`): ledger schema v1, tower tick, script flights, crash_tower v0, 57 tests.

## Where it stands (2026-09-03, after the subtraction pass)

The vertical slice is the baseline: plan `morning-briefing` (declared outputs
`_meta/intelligence/morning-briefing-*.md` + `-latest.md`) lands into CollabVault main under the
launchd tower. Two real landings so far (`60d45cc0`, `c30de3ad`); the second survived `kill -9`
of tower mid-flight (launchd restarted it, the flight landed with only the declared files). One
more consecutive landing, then retire the scrape inside `email-runner.sh morning-briefing`.

Engine is 2097 lines (was 3395): objectives, observations, messages, gates are gone; a plan that
lands must declare outputs (paths or globs) and only those are artifacts and only artifacts land.
Rules now in force: intent over ceremony; do not repair legacy guards; make nexus smaller.

Next real work, in order: (1) third landing, retire the legacy scrape; (2) next airline the same
way (midday-pulse or research-digest: same script, one plan each, declared outputs); (3) step 4,
plists to plans one at a time, deleting each plist after its plan has landed real output.
Not next: more foundation features, more guards, more observability.

## Facts the next session cannot see

- `tbs-agy-keychain` is SKIPPED by Aria's ruling: the file-token fallback works and the bridge is
  slated for deletion. Spend nothing more on it unless Antigravity actually fails without it.
- hcom hooks are capped at 15s and fail open in `~/.claude/settings.json` (13) and
  `~/.codex/hooks.json` (5; added 2026-09-03 by the new session, the earlier claim was wrong: they
  had no timeout). `HCOM_TIMEOUT` is 15 in toml, AND every registered instance's `wait_timeout`
  in `~/.hcom/hcom.db` was reset from 86400 to 15 (`hcom config -i <name> timeout 15`): the toml
  value never touched existing rows. If hcom re-installs its hooks, re-check both.

- Branch sweep done 2026-09-03: 18 local branches deleted, each only after `gh` showed a merged
  PR whose head sha equalled the local tip (or `git cherry` showed nothing beyond main). The
  force-delete guard only consults `gh` for a literal branch name, not a shell variable in a
  loop, and it also matches the words inside a heredoc. Left alone because the local tip has
  commits past the merged PR head, which is a human's call, not a sweep's:
  tbs-www `aria/lesson-event-log` (5), tbs-landing `aria/mommyai-lesson21-memory` (15),
  tbs-curriculum `aria/l012-native-page` (2), nexus-office `pipeline/auto-issue-35` (1),
  thinking-brain-school `aria/care-antigravity-runtime` (2 past PR #39), `feature/w` (never a PR).
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
