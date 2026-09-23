# Desktop and mobile Office: surface audit and unification plan

**Date:** 2026-09-23
**Scope:** Native macOS Office in `app/Office`, current mobile Office served from `client/phone/office.html`, and the older `/classic` phone view. This is a source audit. It does not claim a live device walkthrough or that every production data source is fully populated.

## North star: direct all work from Office

The product succeeds when the user can **observe, ask, decide, direct, and verify work without opening a terminal window**. The strongest signal of failure is opening another terminal merely to ask whether a coordinator is working, whether an instruction was read, whether an issue is finished, or what to do next. Office must answer those questions from current evidence and provide the relevant action in the same conversation.

### One Office conversation

Give **Ask Office** its own tab and one durable conversation across desktop and mobile, with exact links to the coordinators, tasks, issues, PRs, files, runs, and source material it cites. It should handle three kinds of request:

1. **Answer:** “What are the coordinators doing?” “Did #546 get done?” “Why is Matra idle?” It gathers live status, last activity, inbox read state, receipts, and blockers, then answers concisely with freshness and evidence links. It says when evidence is missing or a source is stale.
2. **Direct:** “Tell TBS to prioritize #546.” “Create an issue for this bug.” “Ask Matra for an ETA.” It previews the target and effect, then uses the existing typed action routes and shows accepted/read/acted-on status. It must not equate a queued message with a completed task.
3. **Follow through:** “Let me know when the review build is ready.” “Check whether the PR passed.” It records a durable watch condition and brings the outcome to Watch/Feed; a fresh chat window is not required to remember the request.

The assistant is an **Office manager**, not another implementation coordinator. Its default job is to inspect, explain, route, and track work. It may perform small authorized Office actions—issue/PR edits, task starts, coordinator steering, saved watches—through the same typed commands the UI uses. Substantial code work goes to the existing coordinator/task owners with an exact objective and receipt. If a question requires a command or diagnostic not yet modeled as a route, a bounded sandboxed run with visible command, output, and provenance is a later capability; the first release should cover the repeated supervision and directing journeys before adding a general shell surface.

### Terminal-replacement gap map

| Why a terminal is opened today | Office must provide |
| --- | --- |
| Ask “what is happening?” | Live coordinator summary, last meaningful activity, health, silence age, current objective, recent shipped changes and source freshness |
| Ask whether an instruction landed | Accepted → read → acted on → outcome, tied to the exact message and run; honest unknown state if consumption cannot be proven |
| Ask “did X get done?” | Object-specific answer from issue/PR/task/run/deploy receipts, including the difference between committed, built, staged and live |
| Ask “how long will Y take?” | Evidence-based estimate or explicit uncertainty, current blocker, owner and next checkpoint; never invented precision |
| Correct direction | Natural-language request mapped to an exact coordinator/task/issue, with a short action preview, idempotent send and receipt |
| Edit work records | Create/update/comment/label/close issues and review PRs in app, or ask Office to do the typed action and show the resulting object |
| Inspect proof | In-app full issue/PR/file/diff/log/artifact readers with exact revision and source links |
| Recover from a failure | Explain the failing owner and supported retry/pause/cancel actions; display the attempt and reconciliation result |
| Remember to check later | Durable watch/follow-up conditions, notifications and a concise Feed post when a condition changes |

### Chat response contract

Every answer should lead with the answer, then show **what it checked**, **as of when**, and **the next useful action**. When Office takes an action, the response includes the exact target, request ID or receipt, and a link to the resulting object. Decision requests still use the revolving multiple-choice stack on Watch; Ask Office can explain a decision and open it, but should not hide it inside chat history. The same conversation and its drafts persist across Mac and phone.

### Acceptance journeys

- From a locked-awake Mac and phone, ask “What are TBS and Matra doing?” and receive current, source-linked statuses without opening any terminal.
- Ask “Did #546 get done?” and get a state that distinguishes an issue comment, a commit, a staged build, and a production verification.
- Say “Tell TBS to prioritize #546, then tell me when it reads the message and when the review build is ready.” See one routed message and two later receipts, even after reconnect.
- Ask Office to open or edit an issue/PR, inspect the in-app object, and follow its exact GitHub source link.
- Answer several decisions in any order with Previous/Next, return to Ask Office, and verify each decision receipt.

This north star takes precedence over matching the current Mac or mobile navigation. A feature that still requires a terminal for ordinary supervision, direction, or verification is an Office gap.

## Executive finding

The two main clients share the same local Office server, but they are different products at the UI layer. The Mac app is a roster and conversation room built around a periodic world snapshot. The current mobile app is a five-tab operations hub with direct routes for tasks, files, GitHub, search, media, system controls, and retained history. The Mac API client still calls older, narrower endpoints such as `/api/search`, `/api/context`, `/api/automation`, and `/api/session/start`; mobile also uses the newer `/api/search/all`, `/api/tasks/*`, `/api/files/*`, `/api/github/*`, `/api/system/*`, and `/api/media/*` families. This is the main reason their capabilities diverge. [Mac API](../app/Office/Model/Api.swift) · [Mobile entry point](../client/phone/office.js)

The current proposal to add the mobile hub's five destinations and detailed controls to Mac would preserve the main UX failure: too much text, too many buttons, and too much navigation. **The recommended direction is a third, newly designed Office interface** backed by the existing capabilities. Desktop and mobile should share one interaction model, with layouts adapted to their screens. The current clients become references for capability coverage, not templates for the new UI.

The new interface must give both clients the **same objects, destinations, states, and available actions** through one capability contract while presenting only the next useful layer at a time.

**2026-09-23 user-workflow correction:** The first prototype should optimize for supervising coordinators. The user currently opens additional terminals to check whether coordinators are alive, working on the right priority, reading instructions, and producing real changes. The main view should answer those questions immediately. Work and Find remain secondary paths. The interactive prototype is at [office-prototype/index.html](design/office-prototype/index.html). Its coordinator data is a labeled snapshot from the local Office, not a live feed.

**Decision interaction:** Preserve the existing Office multiple-choice pattern when a coordinator or workflow needs a decision. Show the exact question, two or three choices, the consequence of each, and a clearly marked recommendation when supplied. Keep full context one click away and show a delivery receipt after selection. Free-text steering remains available for exceptions; it should not be the default way to answer a structured decision. The prototype contains a clearly labeled example decision and does not send it to a coordinator.

**Navigation correction:** The main tabs are **Watch, Feed, Ask, Find**. Watch owns coordinator visibility and decisions. Decisions appear as a revolving stack, one at a time, with Previous/Next and an optional full list; answering one never gates access to another. Feed is primarily read-only and combines world news, AI, work events, daily emails, podcasts and automation updates. Ask is the Office manager conversation. An item opens its full in-app reader or object detail; issues and PRs also expose the exact GitHub URL. Find locates any object or project. The prototype's email, podcast and decision entries are labeled design examples, not live user content.

**External editorial content correction:** Keep generating and archiving the full morning, midday, research, and other emails. The feed has its own scheduled collector for a much fuller stream of politics, world news, AI, science, culture and work. Email editorial inputs and verified sources also feed the post queue, but email timing does not limit the feed. **All feed post text is generated with pinned local models through the Tradition harness; if local inference fails, the post waits.** Each post carries source URL, publication and check times, provenance, caveat, and an email edition link when applicable. Save and reaction state belong to the post, while the email remains the complete record. The [build brief](design/office-build-brief-2026-09-23.md) specifies the source, model and publication contract.

## What is actually shipped

| Surface | Entry and navigation | Character |
| --- | --- | --- |
| Mac | One window with roster, selected detail, optional second comparison pane; menu bar status dot and permission sheet | Persistent machine room; fast triage and side-by-side work |
| Mobile | Today, Work, Coordinator, Library, System tabs; global search, new task, settings, restorable detail sheet, persistent player | Broad remote operations and reading hub |
| Classic phone | `/classic`: one long page with gate, catch-up, agents, live processes, needs, bots, desks, automation, wall | Legacy alternate interface; currently still served |

The server maps `/` to the new mobile Office and `/classic` to the older page. [Route mapping](../client/serve.py#L169-L176) · [Mobile shell](../client/phone/office.html) · [Classic shell](../client/phone/index.html)

## End-to-end capability map

**Key:** ✓ visible and actionable; V visible/read-only or narrower; — no equivalent destination found in this client. “Mobile” means the current five-tab Office, not `/classic`.

| Domain | Mac visibility and function | Mobile visibility and function | Parity judgment |
| --- | --- | --- | --- |
| Attention and permissions | Global Needs view, menu bar counts, live permission sheet with exact gate ID; issue decisions from desk cards | Today attention queue combines task permissions, gates, and issues; detail attention button refreshes every 10 seconds | Overlap in raised hands. Mobile adds engine task permissions; Mac has stronger ambient menu bar signal. |
| Daily overview | Roster shows work board, feed, coordinators, bots, wall, desks; reports appear in bot surfaces | Today shows reports, coordinators, needs, running work, recent activity, latest podcast and Substrate | Same facts are spread across different hierarchies. Neither offers one identical overview model. |
| Projects and checkouts | Desks are mainly repo/world snapshot identities; hidden/pinned groups, reorder by drag, face color | Project roster merges local checkout discovery and world stations; project detail has Files, Conversations, Agents, Outputs, GitHub, Issues/PRs | Mobile exposes actual checkout identity and output routes; Mac owns richer desk organization. |
| Agents and tasks | Bots, hcom sessions at a desk, transcript, reply, start Claude/Codex; global coordinator and feed | Bots, addressable sessions, observed processes, durable tasks, engine/account choice, task histories, exact attempts, outputs, permissions and controls | Major mobile lead. Mac start path is the older session endpoint and does not present the durable task model. |
| Conversations | Bot threads, global/repo feed, session snippets, coordinator steering; in-place reply | Bot/history/archive/task/coordinator conversations, pagination, uploads, draft restore, deep links | Both converse. Mobile has broader history and durable task context; Mac has better continuous multi-pane monitoring. |
| Files and editing | Desk Context tab, local Markdown tree/read, autosave editor, recent documents; search over older local context endpoint | Local and GitHub trees, revision/source/line views, selected context for agent, safe save with conflict draft, outputs and downloads | Mobile leads in source coverage and provenance. Mac has a direct autosave workflow but narrower file types and identity. |
| GitHub issues | Snapshot cards, comments and typed decisions inside desks | Full detail/timeline/comments, create/reply/labels/state, collection pagination | Overlap for common issue triage; mobile reaches complete history and creation. |
| Pull requests | Snapshot cards with head/base, merge state, linked issue navigation and decision actions | Checks, changed files, paged diff/fallback, inline reviews, exact-head review and checked pipeline merge | Major mobile lead in inspection and review. |
| Search | Roster search and `/api/search` for local desk context | One global filtered `/api/search/all` across files, conversations, GitHub, events, logs, podcasts, Substrate, with coverage and pagination | Major mobile lead. Search results need one object-link contract across both clients. |
| Library and media | Wall section detail; podcast playback appears in section view; local context files | Files/documents, saved/recent, podcasts with player, Substrate, lesson previews/outlines, media sources and position | Mobile has a real Library destination; Mac offers fragments. |
| System and automation | Work board: tracked product acceptance and recent runs; wall cards show mail, flows, cost, jobs and other source summaries | Machine health, jobs/logs/receipts, plans and commands, paged runs/events/log lanes/artifacts, section summaries | Mac is strong at glanceable state; mobile is much stronger at drill-down and control. |
| Personalization | Layout minimal/focus/compare, native type controls, appearance, colors, desk face/pins/hiding | Shared preferences for theme/background/accent, reading/interface fonts, size/density/touch, haptics/motion, media and position | Both have settings but no coherent cross-client preference model. Device-specific options should remain local. |
| Recovery and freshness | Polling continues with window closed; menu bar dot; stale snapshot notices | Connection/revision, last-good data, restorable detail URLs and drafts, search coverage, retry receipts | Both signal freshness differently. A shared status vocabulary is needed. |

### Important limits on this map

- A visible control is not proof of an end-to-end successful journey. The prior mobile acceptance audit records remaining physical iPhone checks for task work, background audio, and haptics, plus incomplete search collection. [Mobile independent audit](design/mobile-independent-audit.md)
- The Mac app’s source makes its hcom session visibility explicit: a process outside hcom is not treated as an addressable conversation. Mobile also distinguishes observed processes from controllable sessions. [Mac sessions](../app/Office/Views/SessionsView.swift#L3-L17) · [Mobile system](../client/phone/office.js#L214-L224)
- The old `/classic` route is a third user experience. Keeping it active without a clear migration policy increases drift even if the two main clients converge.

## Detailed surface inventory

### Mac

1. **Global frame:** resizable single window, roster/detail split, optional compare pane, minimal floor layout, menu bar dot, settings popover, exact gate sheet. [App root](../app/Office/OfficeApp.swift#L146-L259)
2. **Roster:** work board, feed, coordinators, bots, wall sections, grouped desks, local files, put-away drawer, pin/hide/reorder, desk face colors. [Roster](../app/Office/Views/RosterView.swift#L12-L65)
3. **Detail destinations:** bot thread, desk Work/Feed/Context, wall section, Needs, coordinators, global feed, Automation. [Selection](../app/Office/OfficeApp.swift#L220-L258) · [Desk](../app/Office/Views/DeskThreadView.swift#L38-L121)
4. **Actions:** gate answers; bot, coordinator, feed and hcom messages; Claude/Codex session start; issue decisions; desk pin/hide; Markdown save; podcast playback and local preferences. [API client](../app/Office/Model/Api.swift)

### Mobile

1. **Global frame:** five tabs, search, new task, connection badge, settings, detail sheet with back/search/attention, media player. [Shell](../client/phone/office.html)
2. **Today:** daily reports, coordinators, needs, active work, recent activity, latest media. [Today](../client/phone/office.js#L15-L35)
3. **Work:** projects/checkouts, durable task conversations, bots, archives and addressable sessions; project panels for files, conversations, agents, outputs and GitHub. [Work and projects](../client/phone/office.js#L38-L79)
4. **Coordinator:** coordinator roster and steering conversations. [Coordinator module](../client/phone/office-coordinator.js)
5. **Library:** files, saved/recent, podcasts, Substrate, lessons/outlines and wall source summaries. [Library](../client/phone/office.js#L199-L213)
6. **System:** health, machine metrics, observed processes, jobs, plans, run history, logs/events/artifacts and source summaries. [System](../client/phone/office.js#L214-L240)
7. **Cross-cutting:** global filtered search, pagination and coverage; GitHub detail/review/merge; file revision and context attachment; persisted preferences and drafts; restorable detail URLs. [Search](../client/phone/office.js#L243-L248) · [Settings](../client/phone/office-settings.js)

## Why the experiences drifted

1. **Different information architecture:** Mac organizes around people, desks and wall cards; mobile organizes around Today/Work/Coordinator/Library/System. The same item lacks a stable shared destination.
2. **Different API generations:** Mac decodes the world snapshot and older endpoints; mobile uses newer detailed object and command routes. Adding visual components to the Mac without updating its data model will leave it shallow.
3. **Different identity granularity:** Mac often keys a desk by repo slug and a session by hcom name; mobile uses checkout, task, attempt, artifact, revision and provider object IDs. This prevents reliable cross-client deep links and action parity.
4. **Different depth rules:** Mac favors an always-visible overview with truncated or summarized records. Mobile often fetches paged full detail. Both are useful, but summaries must lead to the same authoritative object.
5. **Three live frontends:** `/classic` remains available beside the new mobile hub and native Mac app. Its separate `phone.js` keeps a parallel navigation and interaction model alive.

## Proposed unified product contract

### One map, adaptive presentation

The five existing mobile tabs are an implementation inventory, **not the proposed new navigation**. The third interface has four primary places: **Watch, Feed, Ask, Find**. Watch answers what needs a decision and what coordinators are doing. Feed is the primary reading stream for world and work. Ask answers questions and directs work through one manager conversation. Find searches every corpus and leads to the same object views. Project, Library, media and System remain complete destinations reachable through object links and a compact place switcher. A persistent **New** action starts a task or creates the relevant object after context is selected.

Mac presents a compact left rail and one main canvas, with an optional second pane when the user deliberately compares two objects. Mobile presents one canvas and a small bottom navigation. Both use the same card and detail hierarchy. Preserve the Mac menu bar status and mobile playback/touch support as platform affordances.

Every destination should expose the same underlying collection, filters, count, freshness, pagination, detail fields, and supported actions. A user opening a project, task, file, issue, PR, run, podcast, or bot conversation from either client should land on the same object ID and authoritative revision. **Parity is in the reachable depth, not in showing every control at once.**

### New interaction model: quiet first, complete on demand

1. **Watch leads with one decision at a time**, then shows concise coordinator state, meaningful activity and blockers. The full decision queue is one click away. No dashboards of every healthy source by default.
2. **Open an object to see a focused detail view.** The first screen answers: what is it, what changed, what can I do next? A small action menu holds secondary verbs; history, provenance, logs and raw data sit in named disclosure sections. The interface never drops those capabilities.
3. **One primary action per context.** A task might show “Reply”; a pending permission “Allow once”; a PR “Review”; a file “Edit.” Additional valid actions appear in an overflow menu grouped by intent. Disabled actions explain the actual capability reason.
4. **Search is the universal way in.** One field searches objects and commands. Results show type, owner and freshness; filters appear only after results, when they can help. Search coverage has a concise status with a full inspection view.
5. **Project is a hub, not a wall of cards.** Its header states project health and current work. Inside are a short active list and a compact switcher for Tasks, Files, GitHub, Outputs and Activity. Opening an item preserves project context.
6. **System lives behind status.** A calm status indicator opens machine health, jobs, plans, runs, logs and recovery controls. Problems surface on Watch; routine healthy telemetry does not consume Watch.
7. **Conversation stays conversational.** Messages and attachments dominate the view. Transcript controls, execution attempts, token/account metadata and raw logs are available through a context drawer, not interleaved with every turn.
8. **Progressive disclosure has a ceiling.** Every feature must be reachable in at most three meaningful steps from Watch, Feed, Ask or Find; no feature is removed to make the first screen quieter.

### Information density rules

- One prominent decision per viewport; one sentence per row; no JSON or raw status dumps in primary UI.
- Default lists show active/recent items, then a clear “View all” path with filtering and pagination.
- Use consistent state words and icons across clients. Show timestamps where freshness affects a decision, otherwise keep them in detail.
- Use whitespace and grouping to establish hierarchy. Avoid nested cards inside cards and repeated button rows.
- Show complete data in dedicated readers: full issue timelines, PR diffs, source files, logs, archives, reports and media transcripts remain available.

### Example journeys

| Need | Quiet path | Full depth remains available |
| --- | --- | --- |
| Answer an agent | Home → Needs you → permission → Allow once/Deny | Exact task, request ID, command/context, history and receipt |
| Start work | New → choose project and agent/account → write prompt → Start | Checkout/revision, task/attempt, output, logs, permissions and reconnect state |
| Review a PR | Work/Find → PR → Review | Checks, files, diff, inline comments, head SHA, merge eligibility |
| Read or edit a file | Work/Find → file → Read/Edit | Source/revision, line selection, conflict diff, save receipt |
| Diagnose automation | Home alert or Status → run → inspect | Full logs, lanes, events, artifacts, retries and owner controls |

### Design validation before implementation

Prototype the third interface in a standalone interactive shell with realistic dense data: dozens of projects, simultaneous agent activity, multiple pending permissions, long issue threads and a failing automation. Test desktop and phone widths with the same task scripts. Measure time and steps to locate, understand and act, plus perceived clutter. Keep the old clients available until the new option passes these journeys. A static mockup or a sparse demo would hide the very problem being solved.

### One object/action contract

- Canonical reference: `{kind, owner, id, project_id?, checkout_id?, revision?}`. Keep repo slug separate from checkout identity; keep task separate from execution attempt and process PID.
- Read envelope: object/collection, observation time, source revision, freshness, coverage, cursor and capabilities. Both clients render stale, incomplete and unavailable states consistently.
- Actions: use typed server commands with capability reasons, stable request IDs, idempotency and receipts. Both clients distinguish accepted, delivered, running, produced, published and confirmed. Exact gate/permission IDs, file revisions and PR head SHAs bind sensitive actions.
- Navigation: each canonical object has a link that opens in both clients. Mac links should open the native app when available; web links should remain valid on phone.

The existing mobile backend plan already articulates much of this contract. Reuse its object/read/command model as the common backend rather than inventing a third client-specific model. [Backend plan](design/mobile-backend-plan.md#L100-L127)

## Delivery sequence and acceptance

| Phase | Concrete work | Proof of parity |
| --- | --- | --- |
| 0. Freeze inventory | Build a machine-readable route/action matrix from both clients and the server; mark every row as full, read-only, unsupported or unavailable. Decide whether `/classic` redirects or stays archived. | Every visible control has an owner endpoint and canonical object; no hidden third feature surface. |
| 1. Third-option prototype | Build the Home/Work/Find shell and focused object detail views with realistic dense fixtures at desktop and phone sizes. | Core journeys are discoverable without a wall of text or controls; full depth remains reachable in three steps. |
| 2. Shared identity and actions | Bind the new shell to canonical project/checkout/task/object links, capability reasons and action receipts. | Open the same sampled object on both devices and compare identity, revision, freshness and outcome. |
| 3. Complete feature migration | Bring every existing Mac and mobile capability into the new object views, readers or context menus; keep an explicit parity checklist. | Every row in the capability map has a reachable route and paired read/write journey where supported. |
| 4. Preferences and recovery | Define shared versus device-local settings, draft keys, last-opened objects, attention state and reconnect behavior. | Leave a draft, disconnect, resume on the other client where applicable; no duplicate action or lost draft. |
| 5. End-to-end acceptance | Run desktop and physical-phone journeys against one production revision, including task launch/steer/output, gate/permission, file conflict, GitHub review, search coverage, media playback and system retry. | Paired journey receipts with object IDs and server outcomes; physical iPhone checks remain mandatory for audio/haptics. |

### Priority

Start with the **third-option prototype and its dense-data usability test**. Then bind it to the existing mobile object/action routes and fill any backend gaps. The old Mac and mobile clients provide a coverage checklist while the new interface is built.

## Definition of unified

For every user-visible object and action, a parity checklist should answer: **Can I find it? Can I see its full current detail and provenance? Can I do the same supported action? Can I tell whether it worked? Can I return to it after interruption?** An explicit unavailable or read-only capability is acceptable only when the underlying owner truly cannot perform it; a client omission is a parity defect.

No source changes were made as part of this audit. Existing working-tree changes under `nexus/` and `tests/` were left untouched.
