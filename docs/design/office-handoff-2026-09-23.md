# Office redesign handoff — 2026-09-23

## 17:48 implementation and verification

The accepted four-tab design is now running on the installed Mac Office app and the phone web app. Both clients read the same live service and stored objects.

- Rebuilt and installed `/Applications/Office.app` with the repository's explicit dirty-build identity flag; confirmed it is the only registered Office app and opened it. A rendered Mac screenshot shows Watch, Feed, Ask, and Find, current coordinator summaries, the decision state, and the Ask shortcut.
- Opened the iPhone-sized Safari client against the real API and inspected Watch, Feed, and Ask at 368×800. Watch shows coordinator activity and freshness; Feed shows source-linked image/story objects and horizontally scrolling topic filters; Ask keeps one model-selectable conversation. The phone app uses the existing Tailscale Serve URL. A request to `https://office.tail4f309a.ts.net/api/health` returned healthy. No physical iPhone was attached for device-only checks.
- The real Feed now has For you and chronological Latest views. Save/love/dislike and replies persist; followed topics have their own view. The default For you order uses bounded category feedback, and Latest remains chronological. Issue and pull-request story links open the existing complete in-app GitHub detail with the exact GitHub URL.
- The independent `com.aria.office-feed` schedule refreshes existing source inputs hourly and runs at most one selected local model over a bounded batch. The current refresh covered 2,021 available source items and supplied 97 candidates; the live feed had 16 verified posts spanning world, politics, business, science/history, work, and listen. New posts were written by `local:devstral:latest`. The stale always-loaded `com.aria.mlx-serve` Gemma job remains retired. `ollama ps` showed no loaded models after the run.
- Tradition has 107 Office editorial `RunRecord`-compatible evaluations (Devstral 68, Qwen 3B 19, Qwen 7B 18, GPT-OSS 20B 2). Current evidence favors Devstral on publication yield, while model selection remains replaceable and feedback-driven. The Auto Ask choice uses its own small Thompson sampler; personal Codex Sol remains the default.
- The dispatch section reads the existing full email archive (847 editions remain available). Podcast cards reuse the established Office podcast manifest/player; 43 playable episodes are listed, while an older manifest record with a missing MP3 is omitted from the listening list.
- Ask uses one durable Codex/Claude conversation, defaults to personal GPT-6-Sol, and exposes currently available models from both signed-in accounts. Real Codex and Claude turns and a model switch were verified earlier in this continuation. The coordinator detail and existing task/GitHub controls remain reachable from Office.
- The focused Office suite passes **138 tests**. JavaScript syntax checks and Python compilation pass. The current mobile route, feed filters, email list, playable podcast catalog, model catalog, and Tailscale health endpoint all returned live data.
- One feed classification error (an astronomy paper tagged History because its source title contained “history”) was corrected in the stored post and the deterministic classifier now recognizes astronomy terms. A regression test covers it.

The standard `vaults live office` deployment command declined to run because the checkout contains unrelated dirty work. I preserved those changes and used the app's own Release installer with the recorded dirty-build marker. The Office app is now rebuilt/opened and the server was restarted manually. No unrelated paths were landed.

The implementation is ready for use. The remaining acceptance check is operational: use Office for several normal days and record any specific task that still sends you to a terminal or X. Physical-device background audio and lock-screen playback also need a real iPhone to verify.

## 03:30 implementation continuation

The **accepted** prototype and main commission are authoritative; the earlier notes below include an obsolete three-tab suggestion. The final tabs are **Watch, Feed, Ask, Find**. An active goal was created for the complete build. The user authorized implementation and stepped away; do not mark complete until Mac, iPhone, and the editorial pipeline are verified end to end. Current developer instructions prohibit spawning subagents.

- Retired `com.aria.mlx-serve` through the Vaults job registry and synced launchd. No Gemma process remains. Actual current machine reports **24 GB RAM**, despite the advisory's 64 GB target. Ollama `ps` was empty after bounded runs.
- Added `client/office_feed.py`: SQLite feed posts, provenance/local-model publication gate, categories/formats, save/love/dislike/replies, model run receipts and MAB scores. API routes are in `client/office_api.py`; three focused tests pass.
- Added `client/office_feed_runner.py`: reads existing private `_meta/state/editorial-inputs/inputs.sqlite3`, pairs different publishers for world/politics stories, balances topic selection, calls Tradition's Ollama provider in a bounded batch, samples installed local models, and unloads afterward. Early dry/live runs over-skipped and rejected exact quotes; prompt and evidence gate were improved to use indexed source-backed snippets. A new live run was active as this note was written; poll exec session **93798** if still active, inspect posts for factual quality before scheduling. No cloud generation fallback.
- Added `client/office_ask.py`: one durable SQLite chat. Codex app-server direct personal account, default `gpt-6-sol`, live `model/list`; Claude Code personal account, live `/model` alias catalog, direct resumable `claude -p`; explicit switch marker and bridge context. Isolated real Codex and Claude e2e turns passed. A production HTTP Ask status turn about TBS/Matra returned current evidence, times, issue links and receipts. APIs: `/api/ask`, `/api/ask/models`, `/api/ask/send`. Manager instructions now target short routine status answers and in-app coordinator links.
- Updated mobile `office.html`, `office.js`, new `office-v2.css`, and service worker to the four tabs. Watch uses genuine gate/permission queue with Previous/Next/full list, live coordinator rows, separate issue follow-up, Ask shortcut. Feed uses real API, category filters, sources, save/love/dislike/reply and existing podcast reader. Ask is the shared chat; Find retains older detail/actions in collapsed sections. Syntax checks pass. Chrome rendered QA found dark mode contrast bugs in Ask/Watch; CSS fixes applied. More visual/interaction QA needed.
- Live Mac `OfficeApp.swift` now embeds the shared Office interface in native WKWebView, keeping native window/menu bar dot; demo mode keeps older views. `xcodegen` was installed and project regenerated. Debug build passed with explicit `OFFICE_DEV_DIRTY_BUILD=1` after adding a dirty-dev identity opt-in to `scripts/write-build-identity.sh`. Mac install and rendered QA remain.
- Restarted `com.aria.office-serve`; HTTP health, feed, model catalog, and CSS routes worked. `vaults live office` refused because the repository is dirty. Unrelated dirty files are `nexus/lanes.py`, `nexus/lease.py`, `nexus/work.py`, `tests/test_lanes.py`, `tests/test_tower_contract.py`, and `graphify-out/`; preserve them.

That was the work remaining at 03:30. The verification record above supersedes it. See `_meta/commissions/2026-09-23-office-rebuild.md` and `docs/design/office-build-brief-2026-09-23.md` for the full product contract.

## North star

The user should be able to **observe, ask, decide, direct, and verify all ordinary work from Office without opening another terminal window**. The immediate pain is supervising coordinators: the user opens new terminals to ask what they are doing, whether instructions were read, whether an issue is done, and what happens next.

## User preferences and decisions

- Keep the current mobile app's warm paper palette, serif wordmark, and general visual character. Desktop and phone may differ sensibly.
- Reduce text and buttons on the first screen. Preserve complete capability behind focused detail views.
- Main navigation is now **Watch, Feed, Ask, Find**. The user explicitly added Ask as the fourth tab. It holds one persistent manager conversation; Watch shortcuts open that same conversation.
- **Watch** shows coordinator state, current objective, last meaningful activity, silence/freshness, shipped work, blockers, inbox read state, and decisions at the top.
- Decisions use the existing low-effort multiple-choice pattern: exact question, 2–3 options, visible consequences, marked recommendation when supplied. Present **one at a time as a revolving stack**, with Previous/Next and an optional full list. The user can visit any decision without answering earlier ones; answering records a receipt.
- **Feed** is primarily for reading, like a fun, personal work-and-world timeline. It needs its own scheduled news process, with much broader world coverage and different publications/perspectives; email remains one input. Prototype filters now include For you, Latest, Politics, World, AI, Science, Culture, Work, Listen, Saved. Posts can be source-linked stories, figures, timelines, or audio cards, with more media formats in the build contract. Save and appreciation use compact icon controls; reactions do not steer work.
- Every **feed post** must be written by a pinned **local model** through the Tradition harness/provider layer. The user supplied an advisory brief proposing the Mac Studio as an always-on editorial engine, explicit editorial passes, embeddings, visual selection, followed threads, and continuous local model evaluation. Use it as guidance within the existing infrastructure. Sphinx/North/Relay/Rune/Parallax are Office roles, not model identities. The pipeline queues candidates when local inference is unavailable; it cannot silently choose cloud generation.
- Reuse the **existing podcast audio pipeline** and Office media routes. English audio currently uses pocket-tts/Alba; Korean uses the Qwen3 TTS voice lab. Existing episodes have script, MP3, duration and manifest, but no verified word-level timestamps; do not invent clip offsets.
- **Keep generating and archiving full emails.** Add short posts as an additional output from the same editorial inputs, preserving the originating edition, source URL, date, and caveats. Do not replace the emails or treat their HTML as the sole data record.
- Issues and PRs should open complete **in-app detail** (body, timeline, discussion, checks/diff/reviews as appropriate) with an explicit exact GitHub link. Preserve repo, number, revision/head SHA.
- **Find** locates projects and objects. No permanent directory tab is wanted.
- A persistent **Ask Office** conversation is the key missing capability. It should be accessible on Watch and from everywhere without becoming a fourth tab. It answers cross-project status questions with evidence and freshness, routes small typed actions (issue/PR edits, coordinator steering, task starts), and follows up on conditions. Substantial implementation goes to existing coordinator/task owners. Show accepted → read → acted on → outcome honestly; never call queued work complete. ETA answers should state uncertainty.

## Existing code and evidence inspected

- Native Mac: `app/Office/OfficeApp.swift`, `Views/RosterView.swift`, `Views/CoordinatorsView.swift`, `Views/DeskThreadView.swift`, `Views/SessionsView.swift`, `Model/Api.swift`.
- Current mobile: `client/phone/office.html`, `office.js`, `office-coordinator.js`, `office-tasks.js`, `office-settings.js`. `/` serves this client; `/classic` serves the older `phone.js` page (`client/serve.py`).
- Both use the local Office server. Mobile exposes deeper `/api/tasks/*`, `/api/search/all`, `/api/github/*`, `/api/system/*`, `/api/media/*` routes; Mac often calls older snapshot/session/context/search routes. `client/office_api.py` maps the current mobile routes.
- Existing Mac decision component at `app/Office/Views/DeskThreadView.swift:980` already gets the low cognitive load pattern right.
- Local `/api/coordinators` was read on 2026-09-23 around 07:44 UTC: TBS was running with one unread priority instruction and recent HomeClass changes; Matra was idle after a successful run, blocked on Xcode for phone proof. These are a dated snapshot, not current status.
- Actual editorial output was inspected under `/Users/slowember/Developer/Vaults/_meta/logs/email-{morning-briefing,midday-pulse,research-digest}-2026-09-22.html`. It covers external current events, AI papers, Hugging Face models, physics, archaeology/history, science discoveries, and internal work with source links and bounded claims. The runner is `/Users/slowember/Developer/Vaults/_meta/services/runner/email-runner.sh`; it gathers pre-scraped/live sources, HF research, dedup context, then generates and sends the HTML email. The email archive has raw digests/entities/topics under `/Users/slowember/Developer/Vaults/_meta/services/email-archive/`.
- Existing coordinator UI already exposes overview health, current work, lanes, commits, a long chronological run conversation, an inbox composer, and a changes view. The missing piece is one Office-level conversation that synthesizes and follows through across these objects.

## Files produced or changed in this conversation

- `docs/desktop-mobile-unification-audit.md`: code-based desktop/mobile capability audit, third-interface proposal, corrected Watch/Feed/Find navigation, editorial-output rule, and terminal-replacement north star/gap map.
- `docs/design/office-prototype/index.html`, `prototype.css`, `prototype.js`, `watch.css`: standalone interactive static prototype. It keeps the mobile palette; Watch contains coordinator cards and a one-at-a-time sample decision stack; Feed has short external-world posts adapted from archived Sep 22 emails plus work and podcast examples, topic filters, source links, Save/Love state; Find searches sample objects. Issue and PR examples open in-app detail with exact GitHub links. All displayed data is **demo/snapshot**, not live or connected to the production Office API. The prototype was opened in Chrome and Cursor. `node --check` passed after the latest JS edits.
- `docs/design/office-handoff-2026-09-23.md`: this handoff.
- `docs/design/office-build-brief-2026-09-23.md`: transcript-derived supervision use cases, local-only world feed and editorial architecture, category and multimedia contract, existing podcast integration, build order and proof.
- `docs/design/office-prototype/experience.js`, `experience.css`, `ask-feed.css`, `media.css`, `watch-ask.css`: extra prototype layer for the World desk, four-tab Ask, icon controls and varied post formats. Prototype content remains dated/demo; no local-model generator or live feed is wired yet.

## Remaining acceptance checks

1. Use Office for several normal days and log each remaining terminal/X detour as a concrete acceptance case. This is needed to test the north star against the user's real routine.
2. If a physical iPhone becomes available, verify Tailscale access and background/lock-screen podcast playback there. The simulator/browser layout and remote API path are verified; hardware playback is not.
3. Keep inspecting local editorial grades and source diversity as real saves, reactions, and replies arrive. The current published feed is live and source-backed, but human preference data is still sparse.

## Workspace cautions

- At the first `vaults ctx`, the checkout had unrelated dirty changes in `nexus/lanes.py`, `nexus/lease.py`, `nexus/work.py`, `tests/test_lanes.py`, `tests/test_tower_contract.py`, and untracked `graphify-out/`. Do not overwrite them.
- Files outside `nexus-office` above were read only; this task did not change the email pipeline or production Office.
