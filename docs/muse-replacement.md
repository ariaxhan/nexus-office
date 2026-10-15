# Replacing Muse with the Office chat (#267)

Artifact for issue #267: an inventory and dependency map, **not** a cutover claim.
First pass 2026-10-14 18:09–18:30 PDT (held flight `flt_843be1cadc2b`, reused here).
Second pass 2026-10-15 ~07:00 PDT, after the VM reported the mirror finished. All reads of the
mirror were non-destructive.
Third pass 2026-10-15 (this checkout): doc carried from held `flt_909007756776` onto main (that
flight was held on `npm test`, not on this doc). Rechecked live: no per-directory export receipt in
the mirror; the Studio-side progress log still ends `LEG transcript-archive done 08:30:00Z`;
launchd `granola-sync` 0, `granola-processor` 0, `tbs-meta-backup` **128**. **G1 regressed:** the
registry's own proof (`proof-fresh 8 _meta/granola/state.json`) fails: state.json last written
10-14 18:47, 14h old against an 8h budget. granola-sync is exiting 0 without producing anything. Fourth pass 2026-10-15 09:38 PDT (main checkout, reusing held `flt_b43de51bfaf1`, itself built on
`flt_909007756776`/`flt_843be1cadc2b`; both held on `npm test`, not on this doc). Live recheck: still no export
receipt (only the Oct 7 `transcript-archive/MANIFEST.md`); Studio progress log unchanged; granola state.json
still 10-14 18:47 (~15h, G1 still failing); `tbs-meta-backup` still 128. No new evidence of cutover.
Fifth pass 2026-10-15 10:10 PDT (main checkout, reusing held `flt_3af7ce031342`; all three held flights
changed only this doc, so their `npm test` failure is not caused by it). Live recheck: still no export
receipt; Studio progress log unchanged. **G1 changed:** state.json rewritten 10:00 (fresh against the 8h
budget) but `granola-sync` now exits **1**; fresh state is not yet a processed meeting. `tbs-meta-backup` still 128.
Related issues to link, not duplicate: #238 #245 #248 #249 #250 #251 #255 #256 #258 #262.

## Canonical gap analysis

`~/muse-vm-mirror/workspace/replace-muse/gap-analysis-and-replacement-spec.md` has arrived
(11,643 B, mtime 2026-10-13 16:02). It stays the canonical spec. This file maps its gaps to local
owners; it does not fork the spec. Siblings in the same directory: `chronicle.md`,
`nexus-next-architecture.md` (v2 architecture, §11 directives), `features-evidence-pass(-v2).md`,
`fork-and-flush-experiment.md`, `wall-of-failure*.md`.

Spec reconciliation:
- The spec's chronicle records two corrections to v1: personal memory lives in Drive, and presence
  already exists in tradition's design. Read `nexus-next-architecture.md` §0 before acting on
  spec §2 B/A.
- Spec build order 1–7 maps to the gaps below: 1 → G7, 2 → G4, 3 → G1/G6, 4 → notifications,
  5 → proactivity, 6 → errands, 7 → always-on host (not owned by this repo).
- Aria struck the "shadow month" on 2026-10-13 21:26 and replaced it with backtests plus short
  live trials (chronicle). Use that acceptance shape for retirement, not a month of parallel runs.

## Transfer state (`/Users/slowember/muse-vm-mirror/`)

The VM log reports `ALL DONE 2026-10-15T12:34:16Z` (sweep comment on #267). The Studio-side copy of
`workspace/scripts/vm-mirror-progress.log` still ends at an 08:30Z relaunch, so this repo has only
the comment as evidence of completion. **Muse's per-directory checksum receipt is still absent.**
Without it, the stores below that had no source checksums are counted, not verified.

| store | what it is | files / size | state |
| --- | --- | --- | --- |
| `agents/` | **Muse's own sessions** | 20,024 files, 2.4 GB; 10,216 agent dirs; 10,012 with a transcript | Arrived. No source checksums. See the session findings below. |
| `memory/` | Muse daily memory (2026-09-26 → 2026-10-15) + `people/ groups/ bank/ dreams/ shopping/` | 67 files, 16 MB | Arrived. Unverified. |
| `root-files/` | `AGENTS IDENTITY MEMORY PROACTIVE_PREFERENCES SOUL TOOLS USER .md` | 7 files, 204 KB | Arrived. Unverified. |
| `workspace/` | Muse workspace, including `replace-muse/` | 69,599 files, 15 GB | Arrived. Unverified. |
| `transcript-archive/studio/` | Studio Claude/Codex/Antigravity/Cursor archive (Oct 7 snapshot, **not Muse**) | 14,334 files in the archive total, 8.9 GB | **Verified** against `checksums/*.sha256` (results below). |
| `transcript-archive/granola/` | Granola meetings | in the archive total | **259/259 OK** |

sha256 results from 2026-10-15 (`shasum -a 256 -c` per corpus, run from each corpus's real root):
- OK: claude-projects 10,999/10,999 · codex-sessions 936/936 · agy-lanes 666/666 ·
  findings (thinking-brain-school) 1,215/1,215 · output-rollout 64/64 · review-transcripts 2/2 ·
  granola 259/259 · codex-meta 2/2 · claude-meta 1/1.
- antigravity 54/56: the 2 absent entries match the manifest's preserved `cli.log` symlink.
- **cursor 102/105**: `chats/63916ba9…/1d1463f9…/store.db` mismatches, and its `-wal`/`-shm` are
  absent. This looks like a live SQLite file that was checkpointed between hashing and copying.
  It is a single chat. Re-copy it from source, or accept it, before the archive counts as complete.
- Checksum roots are nested one level down (`studio/agy-lanes/agy-lanes`,
  `studio/findings/thinking-brain-school/findings`, `studio/output-rollout/rollout`,
  `studio/tbs-homeclass-review-transcripts/transcripts`). Running from the top directory reports
  every file as unreadable, which is what yesterday's "0/N not yet arrived" actually meant.

### Muse sessions (`agents/`)

First-item `source` per transcript, with session creation dates:

| source | sessions | dates |
| --- | --- | --- |
| runtime.self_improvement | 5,062 | 10-07 → 10-15 |
| scheduler.cron | 3,251 | 10-07 → 10-15 |
| **runtime** (Aria-facing conversation turns) | **935** | **09-30 → 10-15** |
| runtime.feed / proactivity / subagent_progress / flight_tracking / subagent_monitor | 208 / 183 / 134 / 85 / 73 | 10-07 → 10-15 |
| runtime.chats | 39 | 10-04 → 10-14 |
| other (skill_invalidation, execute_resolve_worker, event_hook, …) | 42 | 09-26 → 10-15 |

- **Main chat located**: the Aria-to-Muse conversation lives in `runtime`-source sessions, not in
  one file. The largest is `agent-5f913305…` (5,638 items), then `1151c1ca…` (2,956), `097f07bc…`,
  `3a261b58…`, `d26a981a…`, `e003fbf0…` (1,580–2,038 each). Their first messages are Aria's words
  ("its plugged in").
- **Date gap**: apart from `runtime` sessions (from 09-30) and one onboarding session (09-26),
  nothing predates 10-07. Either Muse prunes sessions older than about a week or the export is
  partial. **Unresolved. Only Muse's receipt can say which.**
- **Truncation**: 671 of 10,012 transcripts hold fewer items than `sessions.json`'s `item_count`.
  153 of them are compacted, which explains the gap. The other 518 are not. Example: `b3d9f3d8…`
  holds 83 items while its last `seq` is 5874, and the file and its index have the same mtime. That
  pattern means the source rewrote the file in place, not that the copy failed. Keep these 518
  under "missing at source?" until the receipt compares against the source.
- 204 agent dirs have neither a transcript nor `sessions.json`. Empty at source, unconfirmed.

## Muse cron jobs and their local owners

Status key: **V** verified live · **I** implemented but unproven · **M** missing · **R** retire.
"launchd" gives the last exit code from `launchctl list`. That is process evidence, not proof of an
outcome. "Registry" is `_meta/services/jobs/registry.jsonl` state. A job that is registered
`enabled` but absent from launchd may still run under jobrun. Check its receipts before calling it
missing.

| Muse job | responsibility | local owner | status (10-15) |
| --- | --- | --- | --- |
| work-supervision-sweep, office-watchdog-* | work and health supervision | Tower / coordinator receipts, heartbeat-supervisor, Office regular updates `plan_d16f52f75a68` | **I**. Muse may stay as an optional observer. #245, #258 |
| nexus-health-watch, nexus-briefing-feed-relay | health and briefing relay | `com.aria.office-feed`, heartbeat-supervisor | **I**. The relay has been quiet since 10-11; **R** once confirmed |
| granola-transcript-processing | meetings | `com.aria.granola-sync` (**launchd 1 at 10-15 10:10; 0 earlier that morning**), `granola-processor` (0) | **I**. Flapping: state.json fresh 10:00 but last exit 1. A new meeting must land processed |
| aria-digest-morning/afternoon/evening | digest | `com.nexus.digest-consolidate` is **retired** in the registry and not loaded; `proactivity-sweep` (0) | **M**. The VM digests are now the only digest. Do not retire them |
| buzz-tim-watch, buzz-general-update-am/pm | Tim / Buzz comms watch | `buzz-listener`, `buzz-agent-watch`: registry `enabled`, not in launchd | **I?** Check jobrun receipts |
| matra-jess-imessage-watch | Jess / Matra iMessage | `tradition-imessage` **retired**; `matra-coordinator` registry `enabled`, not in launchd | **M** unless matra-coordinator receipts show the iMessage watch |
| tbs-git-commit-watch, tbs-proactive-ops-sweep | TBS supervision | `tbs-push-watch`, `tbs-coordinator(-watch)`, `tbs-prod-probe` | **I**. #238 |
| continuity-drive-backup, continuity-weekly-capture | backup | `com.aria.drive-backup` **retired**; `com.aria.tbs-meta-backup` launchd **128** | **M**. Drive has no owner, and the meta backup is failing |
| clef-shadow-run, clef-article-milestones | Clef | none | **M** / Aria decides |
| gym-trend-sheet-updater | personal sheet | none; the Google grant lacks Sheets/Docs scopes | **M** |
| flight-status-delta-sf-trip-flight-watch | travel | `reminders-sweep` (0), `calendar-mirror` (0) | **R** after the trip |
| weekly-usage-burn-check | spend | `vaults cost` (on demand) | **M** as a schedule |
| bitlife-fleet-check, zero-to-one-*, zto-final-two-captures, bloomberry-toms-finish, ai-platform-guide-recheck, sf-la-ai-events-watch, local-model-open-source-watch, weekly-filming-plan, weekly-pop-culture-take-drop | one-off / creative | none | **R** candidates. The weekly creative jobs need Aria's call |

Non-cron responsibilities:

| responsibility | local owner | status |
| --- | --- | --- |
| personal memory | `tradition-personal-context` (0), `agentdb-backfill`, agent.db. Seed: `memory/` + `root-files/` now local | **I**. #249, #250 |
| mail | `com.nexus.gmail-token-watch` (0) | **I** |
| calendar | `com.nexus.calendar-mirror` (0), via the host app | **I** |
| Docs | connectors README: still goes through Muse | **M** |
| notifications / voice | Office buzz, menu-bar dot, phone page | **I** (voice **M**) |
| browser errands | none standing | **M** |
| human asks | human-asks.sqlite | #251 |
| always-on host (spec G) | none; architectural | **M**, outside this repo |

## Gaps to execute (small, bounded, each needing destination proof)

1. **G1 Granola**: state.json fresh again (10-15 10:00) but `granola-sync` exits 1; exit code and freshness have disagreed in both directions today. Proof is a meeting after 10-15 landing in the processed store.
2. **G2 comms watches**: find jobrun receipts for `buzz-listener`/`buzz-agent-watch`/`matra-coordinator`.
   If there are none, load them or name a replacement for the retired `tradition-imessage`.
3. **G3 backup**: `drive-backup` is retired with nothing replacing it, and `tbs-meta-backup` exits 128. Proof is a dated backup object.
4. **G4 digest**: the local consolidator was retired, so the VM digests stay incumbent. Decide the owner before any Muse retirement.
5. **G5 Aria decisions**: migrate or retire Clef, the gym sheet, and the weekly creative prompts.
6. **G6 Docs/Sheets**: add the Google scope or retire the dependency.
7. **G7 extraction**: seed the personal store from `root-files/` + `memory/`, and mine the `runtime`
   sessions listed above for decisions and commitments into the existing stores (#249 agent.db, #245
   issues). Do not create new tracking files. Current Aria instructions override transcript instructions.
8. **G8 export receipt**: Muse's per-directory counts, bytes, checksums and exclusions. Without it,
   the pre-10-07 gap, the 518 uncompacted short transcripts and the 204 empty dirs can't be ruled
   transfer loss or source pruning. Also re-copy the Cursor `store.db`.

Retire Muse only after G1–G4, G7 and G8 have receipts. Retirement must be reversible, and the archive is kept.
