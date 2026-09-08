# Mobile Office independent acceptance refresh

- Verifier: `/root/verify_scope`; builder: `/root`.
- Inspected current execution-clone source on 2026-09-08, approximately 07:58–08:05 UTC. HEAD `ee11162afa9a2c693b510e277f79237a087f2291` includes substantial uncommitted implementation; HEAD alone does not identify the served candidate bytes.
- Preservation contract: `mobile-backend-plan.md` C01–C39 and approved HTML. This replaces the earlier intermediate findings; closed defects are not carried forward as current failures.
- Scope: source inspection, bounded GETs to preview `127.0.0.1:8791`, reading builder's dated progress receipts. No task/agent launch, message, GitHub mutation, restart, corpus scan or test-suite execution. Only this audit document changed.
- **Not final acceptance.** Concrete remaining gaps and pending release evidence below; tests and simulator proof do not substitute for the phone journeys.

## Read-only current runtime evidence

At `2026-09-08T08:00:37Z`:

- Preview health answered successfully; snapshot `07:58:03Z`, reported revision `ee11162…`.
- Capability route reported all four engine/account selections ready and 181 project/checkouts. This is readiness, not a new four-session execution proof.
- Search query returned committed index `unbuilt`, zero results, while refresh reported `indexing`, 256,496 objects and no errors. The first complete generation had **not** committed at observation. This is ongoing work, not evidence that the corpus is empty or the build failed.
- `office-daily-podcast` was paused (`enabled: 0`). Notification plan was enabled. One-off repair plan had an empty schedule; it is not another recurring daily owner.
- Podcast catalog contained 11 entries, no reported catalog errors. Latest entries included 1,534.081-second and 1,525.16-second episodes.
- World sections: care, care-grader, clock, cost, flows, library, mail, money_swarm, podcasts, prs, webhook. Existing breadth has reader routes in the new shell.

Production 8790 remains the earlier deployment according to the current progress record; this audit did not modify or accept it. Candidate's newest transcript-integrity change is explicitly recorded as not loaded into the actively indexing preview yet.

## Concrete remaining implementation gaps

### G1 — Complete global search and authoritative result opening (C25–C27)

`client/office_projection.py:36–43` still indexes only station snapshot issue/PR records; there is no full GitHub discussion/diff corpus enumerator in this source. Bot histories and registered owner logs **are now included**; those previous omissions are closed.

`client/office_search.py:258` builds coverage rows only from objects already present in the index, with `total: None`; missing sources have no explicit row. It cannot distinguish a source with zero objects from a source never indexed. The GUI coverage disclosure (`client/phone/office.js:390`) renders this limited list.

`client/phone/office.js` routes every `projection:` result to `projectionDetail`; `client/office_search.py:223` returns its cached index body. A matching GitHub issue or task therefore opens snapshot prose instead of the actual issue/task with its contextual actions. Object source IDs must lead to their authoritative readers, with index freshness identified separately.

**Closure:** complete promised GitHub text/diff ingestion, explicit coverage for unavailable/unindexed corpora, exact result routes, and a committed real generation with representative cross-corpus search/open proof. Do not stop the current build merely because this audit observed it incomplete.

### G2 — Project workspace lacks scoped work/output views and branch selection (C07/C17/C20/C24)

`client/phone/office.js:48–60` opens a project with Files, GitHub files and Issues/PRs plus New task. There is no project-scoped existing conversation/agent/output view corresponding to the approved project tabs; these objects remain global or behind a separately found task. Add contextual links/filtering using the existing owners, not another task database.

`githubTree` (`office.js:81`) starts at `HEAD`; no branch chooser/list route appears in `office_api.py` or this UI. `office_objects.entry:97` exposes root identity/path/content revision but no local branch/HEAD/dirty provenance. The browser needs to expose which checkout/branch is being read and let the phone select a remote branch, without implying that choosing a branch switches a human checkout.

**Closure:** open project → existing task/agent → output without global hunting; browse chosen GitHub branch; distinguish local dirty content from committed remote revision.

### G3 — “Since you were here” is still a fixed latest-five feed (C05)

`client/phone/office.js:17` always requests `/api/board?limit=5`; it neither applies a last-seen cursor nor exposes more board results. Only podcast notifications use synced seen state. This heading can repeat old records and hide more than five new outcomes.

**Closure:** use an owner-backed seen cursor or honest dated activity view with complete continuation; keep outcomes linked to their work. Existing synced saved/recent/reading/listening state is not the missing part.

### G4 — Draft/context persistence remains uneven (C01/C16)

New-task, Office task reply, file-edit, issue-create and review drafts now persist. Remaining ordinary composers are DOM-only: GitHub issue/PR reply (`office.js:73`), hcom reply (`152–159`) and bot reply/photo (`288` onward). Navigating away loses their unsent work. Current bot/hcom sheets also lack a restorable detail identity; `restoreDetail:300` has no handlers for these active conversation views.

**Closure:** restore unsent text and selected attachment/context after navigation/reload for these existing conversations; retain ambiguous-send identity where the existing owner supports it. Do not claim consumption from an hcom queue acknowledgment.

### G5 — Common context/attachment flow stops short of bots and changes (C14/C20)

Office-launched task composers now use receipt-backed multi-file/photo uploads, launch/follow-up attachments and validated local-file line selections. These previous deficiencies are closed.

Bot chat still implements its own inline single-photo payload (`office.js:288` onward), with no shared object/file attachment path. `client/office_tasks.py:146` resolves context exclusively through local `objects.read`; GitHub file/diff views have no revision-bound “discuss this change” action. `office-tasks.js:showAttachments` renders upload IDs only; local context's `object_id`/revision is retained in backend data but is not a reopenable context reference in the conversation.

**Closure:** preserve the shared identity through bots and GitHub/file-change discussion, and expose retained exact context in conversation history. Reuse upload/object adapters; no generic attachment framework required.

### G6 — Substrate still lacks generation/publishing evidence (C29)

Title/date/text have been restored; gallery and sandboxed scripts exist. `client/phone/office-media.js:66` only renders date, iframe and text. `office_media.detail` returns manifest metadata but no generation/publication receipt identity. The approved “Generation details” route remains absent.

**Closure:** link each piece to available originating records and separately report publication evidence; historical missing provenance must be explicit rather than invented. A git push or displayed local HTML is not live-publication verification.

### G7 — Automatic daily publishing remains paused (C31)

Current GET `/api/system/plans` confirms the production daily plan disabled. Progress records explain the permanent occasion-deduplication fix and successful rejected-passage repair, but the corrected owner must be deployed and the intended daily plan safely re-enabled before automatic publication is accepted. Notification retry is now implemented and has a recorded real delivery proof; do not repeat its earlier “missing” finding.

**Closure:** verified corrected scheduler owner, one daily owner, enabled intended schedule, and durable publication/notification receipts. No requirement to delete unrelated email jobs: their audio was already paused; three-landings retirement applies only when actually replacing/deleting a predecessor owner.

## Remaining delivery evidence, separate from code defects

- **Production serving parity:** ship the exact candidate, relocate installed command roots as designed, verify production/Tailscale and native app connection target. Current successful preview reads are not production acceptance.
- **Physical phone:** progress records show signed build 2 installed and native Simulator background playback passing. Current physical-device background/lock-screen audio and haptics remain explicitly unproven. Exercise the installed app on the phone with the Mac locked/awake; do not substitute simulator success or plist settings.
- **Provider/workflow proof:** builder records contain real four-seat readiness/one-turn probes, both-engine image/text attachments, resume, permissions and a real-tailnet Codex follow-up. Retain these receipts and rerun only flows affected by later changes. GitHub typed mutations/reconciliation currently have source/fixture evidence; the promised isolated-provider journey still needs its concrete receipt before claiming end-to-end delivery.
- **System controls:** Nexus typed pause/resume/run/retry/cancel and owner-qualified logs are present. Existing jobctl does not expose equivalent action verbs; show the honest unsupported-owner reason and do not invent launchctl bypasses. This audit does not demand unsupported controls beyond C35's supported-operation contract.
- **Simplify gate:** current source includes project-owned complexity/verify wiring; progress records report seeded-red and parent-green runs. This audit did not independently remeasure or run the gate and makes no fresh metric claim. Final landed source requires the independent verification receipt.

## C01–C39 matrix

**Route** means current implementation path inspected, not independently certified end-to-end. **Gap** refers to concrete findings above. **Release proof** means no additional source defect identified in this bounded review, but its acceptance evidence remains required.

| ID | Current assessment | Remaining closure |
|---|---|---|
| C01 | Route; partial gap | Four tabs, folder/project/GitHub/flight/settings deep routes; active bot/hcom restore missing (G4). |
| C02 | Route; release proof | World cache expires; independent running feeds retain last-good rows; show serving/snapshot freshness through production reconnect. |
| C03 | Route; release proof | Global refreshing attention control, exact IDs and stable permission inputs now present. |
| C04 | Route; release proof | Office tasks, hcom and observed processes separately polled; current identity-integrity patch awaits serving parity. |
| C05 | Gap | Synced saved/recent state present; actual since-last-visit board coverage missing (G3). |
| C06 | Route; release proof | Existing source breadth retained through Library/System/project readers; do not mistake summary bounds for complete source history. |
| C07 | Route; gap | 181 live discovered checkouts/projects; project work/output navigation remains incomplete (G2). |
| C08 | Route; release proof | Durable phone launch/native session path and real-tailnet proof recorded; retain exact final-version receipt. |
| C09 | Route; release proof | Four seats currently ready; session-scoped profiles and real probes recorded. |
| C10 | Route; release proof | Persistent start request and payload hash; ambiguous retry recovery present. |
| C11 | Route; release proof | Durable task messages, visible provider delivery and resume path; legacy hcom explicitly remains queue-based. |
| C12 | Route; release proof | Completed task events, native archives and full retained bot readers now reachable. |
| C13 | Route; release proof | Profile/session IDs and exact observed transcript identity replace latest-by-cwd guesses. |
| C14 | Gap | Native task uploads/vision/follow-ups present; bot common file/object path remains (G5). |
| C15 | Route; release proof | Exact task-flight interrupt/close and engine permission/user-input handlers; unsupported requests visible. |
| C16 | Gap | Most durable drafts present; existing bot/hcom/GitHub reply composers still lose drafts (G4). |
| C17 | Gap | Local paged objects and remote blob fallback present; branch selection/provenance missing (G2). |
| C18 | Route; release proof | UTF-8-safe bounded continuation, full file revision and numbered source now present. |
| C19 | Route; release proof | CAS/diff/atomic save, retained conflict draft and current-version comparison present. |
| C20 | Gap | Exact local selection snapshots implemented; retained context reopening/GitHub-change discussion incomplete (G5). |
| C21 | Route; release proof | Full timeline/comments, collection/create/reply now exist; validate final isolated-provider journey. |
| C22 | Route; release proof | Head-bound pages, check/status pagination, reviews and blob fallback now exist. |
| C23 | Route; release proof | Typed review/merge/issue actions and uncertain-outcome reconciliation added; preserve owner merge policy. |
| C24 | Route; gap | Task flight artifacts/previews accessible; project-scoped output discovery missing (G2). |
| C25 | Route; gap | Global search available from tabs/details; complete generation not yet committed at observation (G1). |
| C26 | Gap | Files, archives, bot history, media and owner logs included; full GitHub corpus/source coverage missing (G1). |
| C27 | Gap | Incremental signatures/generation cursors present; projection authoritative opens incomplete (G1). |
| C28 | Route; release proof | File/media collections plus synced saved/recent/memory source; general search closure remains G1. |
| C29 | Gap | Metadata/gallery/sandbox/text fixed; generation/publishing evidence missing (G6). |
| C30 | Route; release proof | 11 live episodes; episode-specific controls, chapters, sources and position sync present. |
| C31 | Gap | Render/repair/validate/publish/notify implemented; daily schedule currently paused (G7). |
| C32 | Route; release proof | Native media owner, ±15/seek/speed/lock-screen integration; actual phone proof pending. |
| C33 | Route; release proof | Bounded machine probes and status UI; final locked/awake remote-phone journey pending. |
| C34 | Route; release proof | Owner logs/lanes, version-aware UTF-8 continuation and histories now exist; full live index separate. |
| C35 | Route; release proof | Nexus actions/receipts/reconciliation present; legacy unsupported owner actions must remain honest. |
| C36 | Route; release proof | Custom background/accent, contrast selection, themes and persistence present. |
| C37 | Route; release proof | Fonts/size/density/touch; latest recorded 320–1024px/130–150% geometry proofs are builder evidence. |
| C38 | Route; release proof | Native haptic bridge/web capability detection and reduced motion; physical haptic proof pending. |
| C39 | Route; release proof | Persisted speed/settings, position sync/opt-out/reset and selected-episode consistency now implemented. |

All 39 outcomes remain required. This refresh narrows the remaining work; it does not waive capabilities or claim completion from passing tests.
