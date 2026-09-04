# Foundation handoff, 2026-09-03

Read this first. Start in `CodingVault/nexus-office`. One session, no swarm.

**Standing rule: do not proactively improve infrastructure that is not demonstrably
interfering with real work.** Carry real work through tower; delete old machinery as it
becomes unnecessary. This is not a cleanup campaign.

## Installed / runtime state

- Tower: `com.nexus.tower` in launchd, KeepAlive, 5 s tick, `python3 -m nexus tower run`
  from this repo. Ledger `~/Library/Application Support/nexus/ledger.sqlite` (schema v3,
  see "Ledger v3 live migration" below).
  Flights under `.../nexus/flights/<id>/`, kept failure logs under `.../nexus/logs/`.
- Live plan `morning-briefing` (`plan_564bb5ad01ee`): runs
  `intelligence-scrape.sh morning-briefing` in a hangar clone of
  `Vaults/CollabVault`, lands to `main`, `every 86400` (next ~13:53 daily), timeout 540 s,
  max_retries 1, lease `repo:CollabVault`. Old plan `morning-briefing-scrape` is disabled.
- Kernel plugin 9.9.0 installed (`installed_plugins.json`), marketplace clones fast-forwarded
  (main, codex, codex-cli). The TBS silo clone was last seen at 9.8.2.
- hcom: all instance `wait_timeout` rows 15 s, toml 15 s, hooks capped 15 s and fail open.
- `intelligence-scrape.sh`: `INTEL_DIR` overridable, latest symlink relative.
  `sources.yaml` morning-briefing: Ars Technica and The Verge replaced by MIT Technology
  Review and Simon Willison (WebFetch cannot reach the former; curl can).

## Ledger v3 live migration, 2026-09-04 (#126, closes #71)

Controlled repair of the live ledger against reviewed merge
`4a22bbe24293c30249160b0ffc621a0f0fed0276` (#132), run from the installed checkout with the
tower stopped. Do not rerun it and do not restore a backup over it: `bak-2` predates flights
that have since landed.

Background: cancelled flight `flt_88142dd1ded2` moved the live ledger from v1 to a PR #129
style v2 at 2026-09-04T08:28:45Z, leaving a foreign key on the dropped `objectives` table.
Read-only audit found zero legacy objective rows and zero non-null objective links, so no
relationship values were lost.

| | pre (v2, after WAL checkpoint) | post (v3) |
|---|---|---|
| `user_version` | 2 | 3 |
| plans | 10 | 10 |
| tasks | 223 | 223 |
| flights | 230 | 230 |
| landings | 3 | 3 |
| artifacts | 225 | 225 |
| events | 2153 | 2154 |

Event delta by kind: `ledger.migrated` +1 (2 to 3, at 2026-09-04T08:52:46Z). No backfills.

Shape after: seven application tables (plans, tasks, flights, artifacts, landings, events,
leases). Plans and tasks carry a plain `objective` TEXT field; no `objective_id` column and no
foreign key targets `objectives` anywhere in `sqlite_master`.

Backups, all under `~/Library/Application Support/nexus/`:

| file | SHA-256 | is |
|---|---|---|
| `ledger.sqlite.bak-1` | `16f9e2847c25cb2efb7df729773930c8b5f57d5a22cabd999f047fe76ff64568` | v1, untouched |
| `ledger.sqlite.bak-2` | `e396d0e599320951d637fd79330e299ae4e452d2c5c7b84e1bd8950944669d24` | pre-repair v2, created by the first controlled open |

The pre-repair database hashed to the `bak-2` value above, so the backup is byte-exact.

Integrity: `PRAGMA integrity_check` ok, `PRAGMA foreign_key_check` empty,
`Ledger.integrity_check()` clean, `python3 -m nexus status` works from the installed checkout.
A disposable copy of the v3 ledger admitted `task_e6efb915583e` with foreign keys enabled and
stayed integrity-clean.

Tower: resumed 2026-09-04T08:53:24Z from installed checkout `4a22bbe2` (job wrapper PID 10775,
Python PID 10786, installed PYTHONPATH). Post-resume probe flight `flt_85b691a0bc66` (plan
`plan_d16f26a88b90`) ended `produced`, ok, 42.6 s wall.

## Tower invariants proven live (not in tests only)

- A flight's result lands as a commit on the real remote: CollabVault `60d45cc0`, `c30de3ad`,
  author "nexus tower", `landings.applied_sha` equals the remote tip.
- Tower `kill -9` mid-flight: launchd restarts it, the flight still lands.
- Only declared outputs land: second landing carried exactly two files; CollabVault's own hook
  litter (`.session_id`, `actions.jsonl`, `_meta/.runtime/*`) stayed out.
- Human tree fast-forwards only when clean and on the branch; otherwise left alone and
  recorded (`landing.human_tree` outcome `dirty`).
- A failed flight keeps its evidence: log copied beside the ledger and recorded as an artifact
  row, structured error carries `exit_code`, and the workspace is not cleared until that
  persistence succeeded.
- A released quarantine counts only flights after the release.
- Radio (`nexus/radio.py`) is bounded and off the lifecycle path; a radio that never answers
  changes nothing about a flight (`tests/crash_tower/test_radio_hang.py`).

## Deleted this session

- Kernel 9.9.0: `guard-bash.sh`, `autocorrect-bash.py`, `autocorrect-tool-input.py`,
  `syntax-coach.py`, `route-request.sh`, their generator bindings, 94 tests, gate entries.
- hcom lifecycle hooks in `~/.claude/settings.json`: Notification, PermissionDenied,
  PermissionRequest, PostToolUseFailure, SessionEnd, StopFailure, SubagentStart, SubagentStop.
- Engine: objectives, observations, messages, gates (tables, methods, tests); the
  objective-narrowing policy; undeclared-output artifact discovery (`changed_paths`).
- 18 verified squash-merged local branches across five repos.
- `autobranch.sh` (previous session).

## Hook counts now

| surface | count |
|---|---|
| kernel plugin hooks | 19 (was 24) |
| hcom hooks, Claude | 5: pre, post, prompt, session-start, stop-poll (was 13) |
| hcom hooks, Codex | 5, capped 15 s |
| `~/.claude/settings.json` total | 23 |
| `Vaults/.claude/settings.json` | ~40, untouched |

## Engine size and state

`nexus/`: ledger 812, tower ~620, flights 180, landing 125, radio 81, cli ~250 = about 2100
lines (was 3395). Tables: plans, tasks, flights, artifacts, landings, events, leases.
Tick: expire leases, budgets, reap, reconcile vanished, retry, sweep, reconcile applying
landings, land verified, quarantine, schedule, accept, launch. 68 engine tests
(`tests/test_ledger.py`, `test_tower.py`, `test_landing.py`, `tests/crash_tower/`).
The other ~936 tests in `npm test` are the Office app's own suite.

## Declared-output contract

A plan that lands (`--repo`) must declare `--output <path or glob>` (relative to the hangar);
`plans add` refuses otherwise. The runner records only matching files as artifacts; a declared
output with no match fails the flight (`missing_output`); landing commits exactly the artifact
paths and nothing else (`nothing_to_land` if none). Plans without a target still record
whatever they left behind, and end at `produced`.

## Legacy mechanisms intentionally left in place

- hcom messaging hooks (5 Claude, 5 Codex) and hcom itself: the real-work lane is on it.
  Stop-poll is bounded at 15 s. hcom lifecycle removal waits for step 5 (interactive sessions
  as aircraft). `hcom --go reset` only when `hcom list` shows nothing active but yourself.
- `jobctl`, `jobrun`, the 18 `com.nexus.*` plists, `email-runner.sh` (its own morning-briefing
  scrape included) until each plan has landed real output. Note: `jobrun` currently has a bash
  syntax error at line 216 (`unexpected EOF`); legacy jobs may be failing. Migrate, do not fix.
- Every hook in `Vaults/.claude/settings.json` and the non-hcom hooks in
  `~/.claude/settings.json` (guards for silo commands, patch churn, integration auth, google
  identity, verify-live, install-guards): none fired falsely this session.
- `vault-meta-frontmatter-guard` pre-commit in kernel-claude (blocked one release commit over a
  file another process wrote; worked around by not staging `_meta/`).
- `tbs-agy-keychain`: skipped by ruling; nothing more unless Antigravity fails without it.

## Pending: third landing, then retire the legacy scrape

Two of three consecutive landings done (`60d45cc0`, `c30de3ad`). After the third
(`nexus status` shows a third `landed` flight for plan `morning-briefing`, remote tip equals
its `applied_sha`), remove the morning-briefing scrape step from `email-runner.sh` so the email
reads `morning-briefing-latest.md` from the landed commit. Then the next airline the same way.

## Known pre-existing failures, do not chase

- kernel-claude `tests/run-tests.sh`: `detect_vaults finds primary`, `retrospective has output
  format`, `retrospective has cluster analysis`, `retrospective queries current learning
  schema`, `commands have github layer`. Failing before this session.
- kernel-claude has 7 Dependabot alerts on main.
- Six local branches with commits past their merged PR, left for Aria: tbs-www
  `aria/lesson-event-log`, tbs-landing `aria/mommyai-lesson21-memory`, tbs-curriculum
  `aria/l012-native-page`, nexus-office `pipeline/auto-issue-35`, thinking-brain-school
  `aria/care-antigravity-runtime`, `feature/w`.
- CollabVault human checkout is dirty with its own hook runtime files; landings still apply,
  the tree just is not fast-forwarded until it is clean.

## Rules in force (Aria, 2026-09-03; also in `~/.claude/CLAUDE.md` and `Vaults/CLAUDE.md`)

- Direct intent outranks ceremony: understand, do, verify, report. No specs, councils,
  findings files, guards or approval pauses unless the task requires them. Verify the requested
  outcome, not the machinery. Continue to the next obvious ready task.
- Never ask Aria to choose routine next steps. "Fix applied" means verified on the runtime path.
- Legacy guards: do not repair, never improve a text-parsing guard; migrate or remove when
  tower makes the failure unreachable, else the smallest bypass, recorded for deletion. Count
  interference in `_meta/state/legacy-interference.jsonl` (11 rows this session, all before the
  deletions). Airline migration outranks infrastructure fixing.
- Complexity budget: the successful flight is the baseline; make nexus smaller, not safer
  against hypothetical failures. Communications may fail; lifecycle must continue. No watchdogs.
- Do not proactively improve infrastructure that is not demonstrably interfering with real work.

## Next highest-value step

1. Confirm the third landing; retire the scrape inside `email-runner.sh morning-briefing`.
2. Next airline, same shape, one plan with declared outputs: `midday-pulse` or
   `research-digest` (same script, same repo). Old job stays until three landings.
3. Then step 4: one plist to one plan at a time, delete each plist after its plan has landed
   real output. Nothing else.
