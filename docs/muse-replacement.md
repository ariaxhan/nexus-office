# Replacing Muse with the Office chat (#267)

Artifact for issue #267. This is an inventory and dependency map. It does not claim cutover.
Evidence was read 2026-10-14, 18:09 to 18:30 PDT. Every read of the mirror was non-destructive.

## Canonical gap analysis

`~/workspace/replace-muse/gap-analysis-and-replacement-spec.md` is **not local**:
`muse-vm-mirror/workspace/` is still empty. This file keeps the inventory until that spec arrives.
When it lands, reconcile the two and continue the spec. Do not fork it.

## Transfer state (`/Users/slowember/muse-vm-mirror/`)

| store | what it is | state |
| --- | --- | --- |
| `agents/` | **Muse's own sessions**: 8,923 agent dirs, created 2026-09-30 to 2026-10-14 | **In progress.** 4,311 have a transcript; 4,612 dirs exist with no `.jsonl` yet. One `.jsonl` has no `sessions.json`. |
| `transcript-archive/studio/` | Studio Claude/Codex/Antigravity/Cursor archive (Oct 7 snapshot, not Muse) | Checked against the archive's own `checksums/*.sha256` (results below). |
| `transcript-archive/granola/` | Granola meetings | **259/259 sha256 OK** |
| `memory/` `root-files/` `workspace/` | Muse memory, root files, workspace (holds the gap spec) | **Empty.** Not yet sent. Cannot tell transfer-pending from missing-at-source until Muse posts its item-1 receipt. |

sha256 results (`shasum -c` per corpus, zero mismatches anywhere):
- Fully OK: claude-projects 10,999/10,999; granola 259; codex-meta 2; claude-meta 1.
- antigravity 54/56. The 2 absent entries match the manifest's preserved `cli.log` symlink.
- **Still arriving:** codex-sessions 375/936 present at check time (382 by 18:30, files still landing). agy-lanes was 0/666 at check time; all 666 files were present minutes later, but their checksums were not re-run.
- **Not yet arrived:** cursor 0/105, findings (thinking-brain-school) 0/1,215, output-rollout 0/64, review-transcripts 0/2. The directories exist and are empty. The Oct 7 manifest records all of these as secured at source, so treat them as in transfer, not missing.

The Muse **main chat** (Aria-to-Muse conversation) is not identifiable yet. Of the 4,311 sessions read, the first-item sources are self_improvement 2,154, scheduler.cron 1,351, runtime 444, proactivity 94, feed 91, chats 19, and a few minor others. Export completeness stays **unverified** until Muse's checksum receipt arrives and the remaining sessions land.

Re-run the check with `/tmp/muse267/verify.sh` (scratch copy; it is a read-only loop of `shasum -c`).

## Muse cron jobs and their local owners

Counts and date ranges come from the transcripts transferred so far, which are only a partial window.
Status key: **V** verified live · **I** implemented but unproven · **M** missing · **R** retire.
"launchd" means the last exit code in `launchctl list`. That is process evidence, not outcome proof.

| Muse job (runs, last seen UTC) | responsibility | local owner | status |
| --- | --- | --- | --- |
| work-supervision-sweep (6, 10-14), office-watchdog-door/daemons/dispatch/comms (35, 10-14) | work and health supervision | Tower / coordinator receipts, `com.aria.heartbeat-supervisor` (launchd 0), Office regular updates plan `plan_d16f52f75a68` | **I**. Per the authority update, Muse may stay as an optional external observer. Tracked in #245 and #258. |
| nexus-health-watch (94), nexus-briefing-feed-relay (128, last 10-11) | health and briefing relay | `com.aria.office-feed` (0), heartbeat-supervisor | **I**. The relay has been quiet since 10-11; probably already superseded, so **R** once confirmed. |
| granola-transcript-processing (305) | meetings | `com.aria.granola-sync` (**exit 1**), `com.aria.granola-processor` (0) | **M in practice.** The sync is failing: Granola auth is missing (per `vaults status tbs`). |
| aria-digest-morning/afternoon/evening (20) | digest | `com.nexus.digest-consolidate` (0, live trial), `com.nexus.proactivity-sweep` (0) | **I**. The VM digests are still the incumbent, so there are two digests until one is retired. |
| buzz-tim-watch (318), buzz-general-update-am/pm (9) | Tim / Buzz comms watch | registry `com.aria.buzz-listener` and `com.aria.buzz-agent-watch` are **not loaded** in launchd | **M** |
| matra-jess-imessage-watch (254) | Jess / Matra iMessage watch | registry `com.aria.tradition-imessage` is **not loaded**; `com.aria.matra-coordinator` is not loaded | **M** |
| tbs-git-commit-watch (44), tbs-proactive-ops-sweep (1) | TBS supervision | `com.aria.tbs-push-watch` (0), `tbs-coordinator(-watch)` (0), `tbs-prod-probe` (0) | **I**. Tracked in #238. |
| continuity-drive-backup (8), continuity-weekly-capture (1) | backup and continuity | `com.aria.drive-backup` (registered, **not loaded**); `com.aria.tbs-meta-backup` (**exit 1**) | **M** for Drive, **bad** for meta backup |
| clef-shadow-run (83), clef-article-milestones (1) | Clef project | none found | **M**, unless Aria confirms it is retired |
| gym-trend-sheet-updater (14) | personal / health sheet | none found; the Google grant lacks Docs/Sheets scopes (connectors README) | **M** |
| flight-status-delta-sf-trip-flight-watch (6) | travel reminder | `com.nexus.reminders-sweep` (0), `com.nexus.calendar-mirror` (0) | **I** for the mechanism, **M** for this specific watch. Likely a one-off, so **R** after the trip. |
| weekly-usage-burn-check (4) | spend | `vaults cost` (on demand only) | **M** as a scheduled job |
| bitlife-fleet-check, zero-to-one-codex-check(-2), zto-final-two-captures, bloomberry-toms-finish, ai-platform-guide-recheck, sf-la-ai-events-watch, local-model-open-source-watch, weekly-filming-plan, weekly-pop-culture-take-drop | one-off or creative | none | **R** candidates (one-offs). The weekly creative prompts need Aria's call. |

Non-cron responsibilities:

| responsibility | local owner | status |
| --- | --- | --- |
| personal memory | `com.aria.tradition-personal-context` (0), `com.nexus.agentdb-backfill` (0), agent.db | **I**. Tracked in #249 and #250. |
| mail | `com.nexus.gmail-token-watch` (0) | **I** |
| calendar | `com.nexus.calendar-mirror` (0), using the host app (the Google grant lacks the Calendar scope) | **I** |
| Docs | the connectors README says Docs still goes through Muse | **M** |
| notifications / voice | Office buzz (`client/buzz.py`), menu-bar dot, phone page | **I** |
| browser errands | no Studio-side scheduler | **M**. Run interactively through a browser connector. |
| human asks | human-asks.sqlite | Tracked in #251. |

## Gaps to execute (smallest bounded tasks, each needing destination proof)

1. Granola auth repair, so `granola-sync` exits 0 and a new meeting lands in the processed store.
2. Load `buzz-listener`/`buzz-agent-watch` and `tradition-imessage`, or name their replacements. Proof: a test message is observed and a receipt appears in Office.
3. Load `drive-backup` and fix `tbs-meta-backup` exit 1. Proof: a dated backup object exists.
4. Choose one digest: retire the VM digests only after `digest-consolidate` has a dated delivery receipt.
5. Decide Clef, gym sheet and weekly creative prompts with Aria: migrate or retire.
6. Docs: add the Google scope, or retire the Docs dependency.
7. After the transfer finishes: re-run the checksums, consume Muse's item-1 receipt, locate the main chat sessions and the gap spec, then extract decisions and commitments into the existing stores (#245 issues, #249 agent.db). Do not create new tracking files.

The cutover acceptance in #267 still holds. Retire Muse only after gaps 1 to 4 and step 7 have receipts.
