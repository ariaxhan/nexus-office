# Office build brief: one place for the world and the work

## Outcome

Office should replace the two habitual detours: scrolling X to learn what is happening in the world, and opening terminals to ask whether coordinators are working. The four tabs are **Watch**, **Feed**, **Ask**, and **Find**. Watch supervises and holds decisions; Feed is a lively, mostly read-only world and work timeline; Ask holds one persistent Office manager conversation; Find gets to exact objects and history. The phone and desktop may lay these out differently; the information and actions must match.

The [interactive prototype](office-prototype/index.html) now shows a World stream with source groups and a persistent Ask Office conversation. It is an **archived, local design preview**. It does not poll news or coordinators, send messages, or resolve issue state.

## What the terminal conversations reveal

I read native user turns from personal and TBS Claude/Codex session stores modified since September 1, then narrowed to genuine short supervision exchanges from September 15–23. I excluded automated task notifications, coordinator prompts, and injected environment text. This is a qualitative use-case audit, not a count of all work. Matra conversations live under its project cwd in personal accounts; TBS uses both its dedicated account and personal sessions; Vaults has cross-project conversations in personal accounts.

| Observed question or intervention | Real need | Office behavior |
| --- | --- | --- |
| “How is the coordinator doing in terms of work?”, “status check on coordinator?”, “what did the coordinator do overnight?” | One concise account of current objective, progress, last useful action, output, and freshness | Ask Office answers across coordinators; Watch shows the same evidence without opening transcripts. |
| “Did it pick up Jess’s text on iMsg?”, “what about the messages Jess sent me?”, “is the coordinator taking it on?” | Trace an external request from source to owner to action | Show source message → captured item → assigned owner → read → action → verified result; show the missing link honestly. |
| “Tell it to start,” “is our coordinator doing the rest?”, “should I open a new window? I don’t wanna do that” | Direct work from the current surface | Ask Office can route a typed instruction or task with exact owner and a delivery receipt; no terminal handoff. |
| “Why do we have 17 on the zoom?”, “are we getting stuck again on the same held issues?”, “don’t assign waiting on Aria” | Detect stale or false holds and repeated loops | Watch groups holds by cause, age, owner, and next viable action; Ask Office can fix stale labels and steer the owner. |
| “Did we make the hotfix?”, “is it unified yet?”, “did you do it?”, “can we close any issues?” | Distinguish implementation from verified delivery | Answer separately: code changed, merged, deployed, checked in production, and issue closed. Link each receipt. |
| “Check the transcript… code and logs before asking Caleb”, “where is it wasting time?” | Diagnose confusion without reading a wall of raw text | Provide a short causal explanation with cited transcript/tool/log spans; retain full raw transcript one level deeper. |
| “Anything from me? Anything from Jess?”, “what else did Jess request, and did we do it all?” | Gather obligations and missing decisions across channels | Show a deduplicated obligation list with source, owner, status, and truly required human input. |
| “Should I have let the coordinator do it without opening you?”, “would it have caught the errors?” | Know whether the system can be trusted to catch and act | Show what checks ran, what they detected, what escaped, and who owns the follow-up. |
| “How long did that take end to end?” and requests for faster publishing | Real ETA and bottleneck reasoning | Use prior run durations and current phase to give a range, not a precise invented finish time. |
| “Don’t kill Matra,” “turn on the TBS coordinator again,” “safely bring coordinators to a stopping point” | Control lifecycle safely | Provide pause, resume, drain, and restart as named actions with current run and pending-work consequences. |

The samples also show a distinct **manager** need in Vaults: the user asked for a master coordinator that watches per-repo coordinators and Office itself. The Office manager should inspect, explain, route, and follow through; existing coordinators remain responsible for substantive implementation. It must not treat sending an instruction as completing the request.

### Acceptance questions drawn from those conversations

1. “What are Matra and TBS doing right now? What changed since I last looked?”
2. “Did TBS read my #546 instruction? What did it do afterward?”
3. “Did Jess’s message become work? What is still waiting on Jess or me?”
4. “Why has this issue been held twice? Can the coordinator proceed with a reasonable default?”
5. “Did the hotfix reach production, or is it only committed?”
6. “Assign this to the right coordinator, watch for its reply, and tell me when it is verified.”
7. “What is the remaining path and likely time to publish the next build?”

Every answer needs an **as-of time**, evidence links, an uncertainty statement when relevant, and a useful next action. If the evidence is unavailable, Office says so. A manager reply cannot silently invent run state or send a message.

## Feed: a real world desk in Office

### Reading experience

- Default **For you** blends world news, AI/research, discoveries, work, and occasional audio without repeating every minor internal event. One-tap views in the prototype are **Latest**, **Politics**, **World**, **AI**, **Science**, **Culture**, **Work**, **Listen**, and **Saved**. The phone scrolls those filters horizontally and keeps one readable column; desktop can use a narrow optional side rail for saved stories or “since last visit.” The first two give a useful choice between ranked and chronological reading.
- A post has one short idea, a publication/region/topic, event time, publish time, last checked time, a source link, and optional “what changed.” A source cluster opens a focused story page with chronology, linked coverage, competing interpretations where material, caveats, and corrections. A new article about the same event updates the story instead of flooding the timeline.
- Posts can be saved or appreciated through small icon controls with accessible labels and pressed state. Those gestures personalize reading and never silently start work. Issue/PR/work posts open full Office detail and expose exact GitHub links. Podcast cards open the player with chapters/transcript. Email editions remain readable and archived.
- Use playful editorial voice for discoveries and surprising context; use clear, restrained language for deaths, conflict, disasters, and other grave news. Fun comes from selection and rhythm, not gamifying tragedy.

### Independent scheduled news pipeline

The feed needs its own scheduled process. Email remains an **additional input and output**, not the feed clock. The existing email runner already gathers pre-scraped intelligence, live search, Hugging Face research, source URLs, and a rolling dedup ledger; it produces source-verified HTML editions. Preserve that process and the full email archive.

**Every feed post must be generated by a local model.** The collector, source fetch, URL validation, dedup keys, and publication gates may be deterministic code; no remote text-generation fallback may author a post. If local inference is unavailable, keep the source in a pending queue and show the last successful update time. Do not publish an unreviewed template or silently switch to a subscription/API model. Keep the existing email generator separate until its own migration is explicitly chosen.

The Tradition harness already has local Ollama and MLX model routes. Its registry lists `qwen2.5:7b-instruct` for cheap extraction/work, `devstral:latest` for heavier local work, and an MLX Gemma route; the local Ollama installation was checked and contains Qwen 7B, Devstral, GPT-OSS 20B, Qwythos 9B, and smaller models. The registry is capability metadata, not proof that a model is currently serving. Pin a local model per feed stage through the harness/provider boundary and reject a route whose `local`/`free_local` fields are false. Record the selected model id, provider, prompt version, source ids, latency, and validation result on every candidate. Run a bounded A/B evaluation on the same archived stories before choosing defaults: factual entailment, citation accuracy, nuance, duplicate handling, readable voice, latency, and posts per hour. Hold each candidate against the source text with deterministic and model-assisted checks; a failed candidate is withheld for retry or review.

The five configured Office voices are **roles**, not model names. Rune can propose discoveries and historical context; Relay writes verified work transitions; Parallax looks for missing perspectives, contradictions, and repeated coverage; North helps rank what matters across topics. Sphinx remains the decision specialist on Watch and should not manufacture news dilemmas. A local model can fill those roles, but the role prompt never overrides source verification or the local-only routing rule.

### Editorial work, model evaluation, and the Mac Studio

Use the Mac Studio as the always-on target. The user-provided hardware brief describes an M5 Max with 64 GB unified memory; treat that as the deployment target, then measure actual memory pressure and throughput on the machine. The current harness registry and installed Ollama models are an initial route, not the final editorial model choice. Benchmark the brief's Qwen3.5 27B 4-bit, Qwen3.5 35B A3B 4-bit, and Qwen3 VL 30B A3B 4-bit candidates if compatible local builds are available. Check model license, memory use, visual input support, structured output, and real throughput before adding them to the harness registry. Do not infer quality from model size or benchmark marketing.

Use the existing Tradition harness for provider/model routing, runs, receipts, and comparative evaluation. Feed jobs supply bounded source bundles and explicit roles. The harness should record inputs, outputs, chosen local model, duration, memory/energy sample, failures, and validation outcomes back into its model evaluation data. Office stores editorial objects and publication state, not a second model leaderboard. Pinning a role to a local model must be checked at execution, so ordinary `auto` routing cannot unexpectedly select a remote provider. Local-model failure leaves the source queued.

The editorial passes should be small and separable: **scout** finds potential value outside usual interests; **story editor** identifies actual change and duplicates; **skeptic** marks unsupported or disputed claims; **visual editor** chooses whether source media explains the story better than prose; **context editor** retrieves relevant prior stages; **surprise editor** finds the genuinely strange or beautiful detail; **breadth editor** checks topic/geography/publication balance; **desk editor** decides whether and how to publish. Most candidates should be rejected. These are pipeline jobs, not eight permanent chats or eight visible tabs. Several jobs can share a model, and deterministic checks can replace a model pass when they do the job better.

The current Tradition code has vector indexing, but that alone does not establish a continuously updated Office-wide embedding service. Start by reusing its retrieval/index primitives where suitable, and add a small persistent local embedding worker only after measuring gaps. Embed articles, prior stories, Office events, podcast scripts/transcripts, saved items, follows and questions for deduplication and related-story retrieval. Preserve deliberate exploration outside inferred interests. A **followed thread** tracks a concept or evolving question and surfaces material changes, not every mention.

Keep a representative set of real source bundles and known duplicate/correction cases. Compare candidates on factual fidelity, source use, novelty, clustering, visual choice, writing, structured-output reliability, latency, throughput, peak memory, energy, and failure rate. Include quiet days and consequential breaking stories. The success measure is whether the feed repeatedly surfaces things worth knowing, seeing, hearing or thinking about that the user would otherwise miss—and whether adjacent objects feel meaningfully different.

### Multimedia objects and the existing podcast pipeline

The feed object has a `format` chosen from **story, source image, chart, gallery, timeline, quote, paper, listen, question, curiosity, work, or followed-thread update**. Formats are editorial choices grounded in available source material. A chart needs underlying numbers and axis/scale; an image needs a source, rights/credit, and caption; a quote needs exact text and attribution. Do not generate a fake documentary image for a real event. Text remains available for accessibility and quick scanning. The prototype demonstrates a source-backed numerical figure, a small timeline, and a listening card; richer media needs the production asset service.

Use the existing podcast system for audio. `podcast-generate.py` already renders English audio locally with **pocket-tts/Alba**, ffmpeg, duration checks, script and MP3 artifacts, and a manifest. `podcast-localize.py` renders Korean locally with the Qwen3 TTS voice lab. `office_media.py` already serves manifest episodes and their audio/script through `/api/media`, `/api/media/detail`, and `/api/media/content`; the mobile client has a player. Reuse those artifacts and routes in Feed. The existing daily podcast scripts currently have their own authoring path; keep that working. Any **new feed audio edition script or post text** must be authored by a pinned local model to satisfy the local-only feed rule, then sent through the established audio render/publish steps.

For “worth hearing” segments from podcasts and interviews, add timestamped transcript alignment and stable `media_id + start_ms + end_ms` references before showing precise clips. Existing manifest episodes expose a script and duration, but do not prove word-level timestamps. Until alignment exists, offer the whole real episode rather than an invented `38:14–42:26` clip. Local transcription of external audio can be added after the existing episode reader is sound; benchmark Whisper on the target Mac and store source, language, timing, and transcript confidence. Audio segments should open the same player and retain listening position across clients.

Category proposal beyond the prototype: **Business & economy**, **Health**, **Climate & environment**, and **Arts & internet culture** are useful once source volume supports them. Keep them behind a “More topics” chooser at first so the tab does not become a wall of filters. **Politics** includes government and elections; **World** is the broad geographic lens and can include politics, science, and culture. **Science** covers discoveries and research outside AI; **Culture** covers history, art, books, and strange finds. Category assignment is multi-label, so a South Sudan election story appears in Politics and World without creating two posts.

Proposed stages:

1. **Collect** every 10–15 minutes from a configurable mix of reputable international, regional, specialist AI, science, and primary sources. Add a faster 3–5 minute pass for high-signal breaking sources if rate limits and costs allow. Accept email editorial records, existing intelligence cache, official announcements, research feeds, and later user follows as inputs. Track source failures and last successful poll.
2. **Normalize** URL, canonical URL, title, author, publisher, publication time, language, region, topic, and retrieved text/excerpt. Store the source record before summarizing; keep source provenance and fetch time.
3. **Cluster** related reports into one evolving event. Deduplicate syndicated copies and email overlap, but keep materially different reporting and local perspectives as separate linked sources. Detect major updates and corrections.
4. **Verify and write locally** a concise post from attributable claims. Distinguish reported claims, official claims, independently established facts, and unknowns. Require a working source link for every external post. For consequential breaking claims, seek independent corroboration or mark the single-source status prominently; never fill a quiet feed with invented urgency. The local-only model gate and source-backed validation apply equally to email-derived, internal work, podcast, AI, and world posts.
5. **Rank** for timeliness, relevance, geographic and publication breadth, novelty, and source quality. Cap repeated AI or US-only stories so world coverage remains broad. Let the user tune more/less by topic or region, with a transparent chronological Latest view available.
6. **Publish** only changed story versions with idempotent keys, timestamps, receipts, and an editorial audit trail. Support retract/correct without erasing the original version. Serve updates via polling or push to both clients.

The initial service should target useful coverage throughout waking hours, with a clear “last updated” indicator. Measure source and region diversity, duplicate rate, correction rate, time from source publication to Office post, and fraction of posts with readable original links. These are operating targets to tune with actual use, not a promise that every world event will be covered.

### Minimum data and API shape

`source(id, canonical_url, publisher, author, published_at, fetched_at, region, language, rights_metadata, media_refs[])`; `story(id, topics[], region, status, first_seen_at, updated_at, correction_state)`; `story_source(story_id, source_id, relation, viewpoint_label)`; `post(id, story_id, version, format, headline, body, media_refs[], claim_status, published_at, generated_at, source_ids, email_edition_id?, local_model_id, prompt_version, validation_receipt)`; `user_post_state(post_id, saved, reaction, seen_at)`; `followed_thread(id, concept, query_embedding, last_material_update_at)`. Email editions and podcast episodes keep their own full objects and link to posts. Audio segments use `media_id`, `start_ms`, and `end_ms` only when timestamp alignment has been verified.

Read routes: paginated `GET /api/feed?view=&cursor=`, `GET /api/feed/stories/:id`, `GET /api/feed/sources/:id`, `GET /api/feed/editions/:id`. Write routes: save/reaction/seen state. The scheduler publishes through a server-side ingest path, not from either client. Both Mac and phone read the same feed service.

## Ask Office and Watch build contract

The prototype’s Ask tab now has suggested low-effort questions, an ongoing local demo conversation, concise answer cards, sources, and an instruction draft. In production it needs one durable thread and draft shared across clients. It should answer first, then offer the smallest useful action. Questions should resolve coordinator, issue, PR, run, external request, and deployment identities before answering.

The manager read layer joins `/api/coordinators`, coordinator run/inbox records, tasks, native sessions, issues/PRs, commits, deployment checks, messages, and search. The action layer uses typed commands such as `send_instruction`, `create_task`, `edit_issue`, `close_issue`, `request_review`, `pause_coordinator`, and `resume_coordinator`. Each command has exact target identity, preview, idempotency key, authorization boundary, and receipt. The same information appears in Watch and Ask Office.

For instruction tracking, store distinct states: **accepted by Office → queued for coordinator → read by coordinator → acted on → outcome verified**. For external requests, include **captured from source** before assignment. For delivery, track **committed → merged → deployed → checked** separately. Never collapse these into “done.” Watch should lead with the decision stack and a compact coordinator row; detail opens activity, inbox, work, and raw transcript. An unavailable/stale observer cannot look like an idle coordinator.

## Implementation order and proof

1. Build the shared feed store, source records, clustering, and read API; import archived editorial records for an initial backfill. Keep email sending unchanged.
2. Run the independent collector and local-model writing pipeline on a bounded schedule, with source verification and corrections. Prove every published post has a local-model receipt. Publish real posts, then replace prototype data and show freshness honestly.
3. Build the Office manager read layer and cross-project answer path. Prove the seven acceptance questions against current data and exact links.
4. Add typed actions and follow-up watches; prove accepted/read/acted/verified transitions on real coordinator runs.
5. Connect Mac and phone to the same services and complete issue/PR, email, podcast, and transcript readers. Test rendered desktop/phone and physical phone behavior.

Before claiming the terminal and X detours are replaced, use Office for several normal days. Record every time a terminal or X was still needed, what question it answered, and whether Office could have answered with the available evidence. Turn those misses into acceptance cases.
