# Mobile Office independent remaining-work audit

- Verifier `/root/verify_scope`; refreshed 2026-09-08 at approximately 09:44 UTC.
- Contract: `mobile-backend-plan.md` C01–C39 and approved HTML. This supersedes previous G1–G7 findings; fixed items are not still blockers.
- Source: execution clone at production `a1977f138303a4c2c523ae85be6016d9f5612f81`, plus explicitly unshipped notifier correction. Harness production `9dc98b9` according to release/proof records.
- Bounded read-only localhost production GETs, current source and named progress receipts inspected. No restart, model call, message, GitHub mutation, broad corpus scan or test suite. Only this document changed.
- **Incremental production delivery is established; full C01–C39 acceptance remains open.** “Implemented route” below is not certification of every device/provider behavior.

## Current production evidence

At `2026-09-08T09:44:08Z`, `/api/health` reported `a1977f1…`, healthy, snapshot `09:39:12Z`. Four engine/account seats were ready; 181 projects/checkouts available.

Both daily podcast and notifier plans were enabled with `quarantined_at: null`; daily schedule `06:00`, notifier every300 seconds. The repair plan has an empty schedule. The earlier “daily is paused” finding is closed.

Production search returned132 “harbour” matches from a committed357,251-object generation. Refresh was indexing358,917 objects, no refresh errors at observation. GitHub coverage:45 indexed,4 indexing,1 fetching,43 unbuilt,3 errors. In-progress counts are observations, not failure evidence.

Release records establish443/9443→8790 and harness9dc98b9. The recorded real bot receipt proof `/tmp/office-bot-live-upload-proof.json` shows tool consumption of the retained file and correct hidden token; do not repeat the old “only queued” claim. Production geometry `/tmp/office-production-polish.log` covers four tabs/settings at320/390/430/1024 and130% with no overflow/JS errors. These are retained builder receipts, not new verifier-rendered/device proof.

## Finite implementation closure list — implemented, pending deployment

Follow-up independent source review: all R1–R5 routes below now implemented. No outcome waived. Existing production observations above remain historical a1977f1 evidence, not proof that the pending changes are serving. Fresh normal complexity gate passed:72unchanged,0regressed,0added; `/tmp/office-final-inventory-independent-gate.log`. Earlier independent seeded-parent failure receipt remains valid for this unchanged gate. Full parent96034 is builder-owned and running separately.

### R1 — Implemented: provider cursors and large-diff fallback

Collector follows validated GitHub Link cursors and rejects cycles. HTTP406 falls back to exact base/head local Git commits with external diff/textconv/replacement objects disabled. Existing complete-cache transaction keeps last-good data on failure. Initial diff and continuation use head/base guards; first-page cache identity includes both. Diff display uses bounded65,536-character pages. Builder reports all seven observed406 cases passed actual local fallback, including armature#1; earlier independent fallback and pagination source/fixture reviews passed.

**Remaining:** deploy, let real corpus complete/retry, and verify searchable records/current opens across the affected repositories. Missing exact local commits remain an explicit failure; do not label them complete.

### R2 — Implemented: local checkout provenance

Read-only object provenance now reports branch, HEAD commit, dirty state and observation time; non-Git collections remain distinct. Root/source policy is reused and browsing does not switch checkout branches. Actual Git fixture verified dirty-vs-committed behavior.

**Remaining:** production reader proof after deploy.

### R3 — Implemented: GitHub selected-line controls

Remote reader now sends selected first/last lines through existing exact commit/blob context snapshots. Whole-file, PR-change and local selection routes remain intact; immutable upload receipt preserves source metadata.

**Remaining:** production selected-range→composer→retained-context journey after deploy.

### R4 — Implemented: declared source coverage

Declared inventory captured within each rebuild now distinguishes checked-empty, unavailable, failed and partially indexed sources; unknown totals remain unknown. Its summary commits with generation metadata. Native archive enumeration refreshes and reports traversal failures. Media-kind/error-ID matching closes the previously identified false-empty case. Before the first generation, explicit unbuilt source rows prevent zero-source invisibility.

**Remaining:** deployed committed-generation coverage proof, including empty/missing/error fixtures and actual per-repository collection status.

### R5 — Implemented: late-edition notification eligibility

Timezone-aware generated_at permits an older-dated episode generated after notifier creation. Malformed/naive timestamps do not bypass the cutoff. Existing transactional edition deduplication is unchanged; no regeneration or external-message delivery added.

**Remaining:** deploy and inspect the late-edition Office inbox receipt. Daily owner was already enabled in the last production observation.

## Remaining acceptance evidence

These are completion checks, not reasons to undo the incremental production deployment:

1. Physical iPhone on the real443/9443 production connection: selected account→new task→steer→inspect file/diff→open result, Mac locked but awake; reconnect with an unsent draft intact.
2. Physical background/lock-screen podcast controls and haptics. Native Simulator audio proof and successful installation do not prove these physical behaviors.
3. Representative common bot image consumption through the new receipt path. Real bot text-file tool consumption is already proven; earlier native Claude/Codex image proofs concern different adapters.
4. Isolated real GitHub action/reconciliation workflow required by the plan; source and fixture tests do not establish provider behavior. Existing policy restrictions remain intact; no additional permissions or speculative controls are demanded by this audit.
5. Final source/runtime parity and independent gate receipt after the remaining changes. Existing production geometry and seeded-red/clean-green receipts remain evidence; rerun only affected proofs, then record the final preserved39-item contract.

Historical Substrate generation receipts cannot be manufactured. Its new provenance UI explicitly identifies the source, catalog, configured producer/history and unavailable exact publication evidence; this closes the former missing-reader implementation finding. Existing unsupported jobctl actions likewise need honest reasons, not a new generic process-control API.

## C01–C39 preservation matrix

| ID | Status | Remaining requirement / current route |
|---|---|---|
| C01 | Implemented route | Four tabs and restorable detail/conversation routes; final phone journey evidence. |
| C02 | Implemented route | Production revision/freshness, reconnect and last-good feeds present. |
| C03 | Implemented route | Refreshing global exact-ID attention and permissions present. |
| C04 | Implemented route | Separate observed/addressable/owned populations and live polling present. |
| C05 | Implemented route | Board seen cursor and pagination now replace latest-five limitation. |
| C06 | Implemented route | Existing Office source breadth retained in linked surfaces. |
| C07 | Implemented route | 181 discovered project/checkouts; project-scoped tasks/agents/outputs now present. |
| C08 | Implemented route | Production launch path and exact task/session receipts; final phone workflow proof. |
| C09 | Implemented route | Four production seats ready; earlier actual engine/account proofs retained. |
| C10 | Implemented route | Durable request/payload dedupe and reconnect recovery present. |
| C11 | Implemented route | Durable task conversation/delivery and resumed execution; legacy hcom queue remains labeled. |
| C12 | Implemented route | Completed task/native/bot histories and backward pagination present. |
| C13 | Implemented route | Explicit account/session identity and bound transcript continuation present. |
| C14 | Implemented route | Common bot uploads now deployed; real bot file tool-consumption proven. Representative bot image/device proof remains. |
| C15 | Implemented route | Exact attempt controls and engine permission/input responses present. |
| C16 | Implemented route | Bot/hcom/GitHub reply drafts and restorable conversations now fixed. |
| C17 | Implemented; deploy R2 | Remote branch chooser and commit-bound reads present; local provenance implemented, pending production proof. |
| C18 | Implemented route | UTF-8 chunks, full revision, numbered source and selection present. |
| C19 | Implemented route | CAS/diff/retained conflict draft and save present. |
| C20 | Implemented; deploy R3 | Immutable GitHub file/change context and retained source now present; remote line-selection now implemented, pending production proof. |
| C21 | Implemented route | Full detail/timeline/comments, creation and reply present; isolated-provider mutation proof remains. |
| C22 | Implemented route / R1 | Interactive head-bound reviews/checks/changed files present; large-diff fallback implemented; deployed corpus closure pending. |
| C23 | Implemented route | Typed actions/reconciliation and exact-head merge policy; final provider workflow proof remains. |
| C24 | Implemented route | Project-scoped output links and artifact readers now present; final output inspection journey. |
| C25 | Implemented route | Production global search returns actual matches; corpus closure R1/R4. |
| C26 | Implemented; deploy R1/R4 | Provider-limit remedies and declared coverage implemented; real corpus/deployment evidence remains. |
| C27 | Implemented route | Incremental index, generation cursors, current authoritative opens and revision-bound continuation present. |
| C28 | Implemented route | Readable collections, saved/recent and memory source present. |
| C29 | Implemented route | Substrate source/catalog/pipeline/producer history plus explicit missing provenance now exposed. |
| C30 | Implemented route | Production catalog, range audio, chapters, sources, selected controls and position sync present. |
| C31 | Pending deployment R5 | Daily owner now enabled and unquarantined; late-edition notifier correction pending. |
| C32 | Device evidence | Native media ownership/controls present; actual physical-phone background/lock-screen proof required. |
| C33 | Implemented route / device evidence | Bounded health and connected production; final locked-awake remote-device journey. |
| C34 | Implemented route | Owner-qualified logs/lanes/history/rotation and log search present. |
| C35 | Implemented route | Typed supported-owner controls and reconciliation present; no invented jobctl verbs required. |
| C36 | Implemented route | Global theme/custom background/accent/contrast persistence present. |
| C37 | Implemented route / device evidence | Production geometry passed 320/390/430/1024 at130%; final physical/default preference proof remains. |
| C38 | Device evidence | Native haptics and reduced-motion capability handling present; physical haptic observation required. |
| C39 | Implemented route | Shared settings, speed, remembered-position opt-out/reset and playback continuity present. |

All39 capabilities remain required. No outcome waived, removed or newly deferred. Full goal completion is not asserted.
