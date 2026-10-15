# The Wall of Failure

A first-class surface in the Office, next to the flows wall: one plain line
per failure, up until the failure is fixed **and** the fix is verified.
Aria's rules (2026-10-13), enforced by the data model, not by politeness:

- **Every failure gets a line the day it happens.** Plain language. No
  jargon, no labels that make a fault sound handled.
- **A line comes down only when fixed AND verified** — never when
  explained. Retiring a line requires a verification note: what was
  checked, and how it held.
- **A repeat is marked REPEAT.**

## Where it lives

- Surface: `client/sources/failure_wall.py`, listed in `client/sections.py`
  immediately after `flows`. It renders from the card contract like every
  other source; no app changes were needed.
- Data: `<runtime root>/_meta/wall-of-failure.jsonl` (override:
  `OFFICE_FAILURE_WALL`). Append-only JSONL, one record per state change;
  the last record per `id` carries the current state. The file lives
  outside the release tree so a lane can add a line without a deploy, and
  a release never overwrites the wall.

## Adding and retiring lines

From the repo root (`scripts/wall.py`):

```sh
python3 scripts/wall.py add "The plain statement of what happened" \
    --detail "the slightly longer plain version" --source "where it is recorded"
python3 scripts/wall.py add "The same break, again" --repeat
python3 scripts/wall.py watch wf-041 --note "the rule that now exists, unproven"
python3 scripts/wall.py retire wf-041 --verification "what was checked, and how it held"
python3 scripts/wall.py list
```

Statuses: `open` (up, not fixed) · `watched` (up; a rule or mechanism
exists, not yet proven) · `fixed` (down; requires `fixed_on` + a
verification note).

Enforcement is doubled on purpose. The CLI refuses to write an unverified
retirement. The reader (`failure_wall.fold`) independently coerces any
record claiming `fixed` without both a date and a verification note back
to `open` and names the attempt in the section's problems — so a
hand-edited file cannot take a line down either.

## Seeding

The wall was seeded 2026-10-13 from `~/workspace/replace-muse/wall-of-failure.md`
(the file that was the wall until this surface existed): its 40 lines —
the 12 of that day, the 15 investigated failure cases from
`~/workspace/self-understanding/findings.md` in plain form (the 6 success
cases are not failures and carry no lines), and 13 more from the dated
record of the same weeks. `wall.py init SEED.jsonl` installs a seed only
when no wall file exists yet.

## The wall

Every line on the wall, rendered from the data file the door reads (`_meta/wall-of-failure.jsonl`) — the same records `wall.py list` prints and the Office card draws. Lines are added with `wall.py add`; this section is regenerated from the data whenever a line is added or changes state, and the regeneration is committed, so the document and the data never carry different entries.

### 2026-10-13

1. **The lesson with the same discussion questions for both age groups.** Delivered to Aria as ready; she caught it herself. The writer's own side-by-side check — the one step that would have caught it — never ran. FIXED — The form now refuses to build it that way; verified.

2. **REPEAT — The same kind of age-split defect, flagged by Aria on earlier delivered lessons.** Twice before today. The count of defects she found after delivery reached 10. WATCHED (The count is now stated on every delivery; target zero; not yet earned down.)

3. **A fix was relayed to Aria without anyone checking the lesson.** Main passed along another chat's framing as if it were a fix. FIXED — Relaying lesson candidates is banned; owners deliver directly.

4. **The replacement build queue was written down and never sent.** Designed, documented, listed back to Aria — dispatched to nobody. Found only when she asked what happened to it. FIXED — Dispatched the minute she asked.

5. **Four finished things sat undisclosed for days.** An $18.70 failed Fly.io payment against a cancel she asked for on Oct 1 and was never executed; two money leads with drafts; the exact lease numbers; the Book Two revision links. Surfaced only when she asked 'anything else you just decided to not do?' WATCHED (Fixed for those four (disclosed); watched as a pattern.)

6. **An order was sent and its execution never checked.** The app-description default; checked only after she started asking questions. FIXED — Verified after the fact — it had executed.

7. **A meetup RSVP was offered and never closed.** She didn't answer (she was at a movie); the offer died quietly instead of being closed. WATCHED.

8. **The personality quiz's build state has been 'unverified' for days.** Still unverified tonight. OPEN.

9. **The Studio's 'hardware death' was accepted and repeated for 3 days.** It was a poisoned user profile inside the Time Machine backup. Apple's own fresh-user test proved the machine fine; we repeated their diagnosis without testing it. FIXED (2026-10-13) — Corrected on the record — the machine was always fine.

10. **Our own fleet blocked the MacBook's drive from ejecting.** ~190 agents plus a live lane had to be killed to free a drive Aria was standing there holding. FIXED — Stopped; the stand-up owns the restart.

11. **A month-long 'shadow' cutover was proposed as the plan.** A slow timeline invented instead of using the data already on hand. FIXED — Struck by Aria; replaced with testing against recorded history.

12. **Tonight's messages were full of jargon.** 'v1,' 'battery,' 'corpora' — to a tired person who then had to ask what the words meant. WATCHED (This wall is written plain because of it.)

### October 4–12 — from the investigated record

13. **Six of Aria's messages were lost, and she was told the chat was fine.** A partial read was reported as the whole record. WATCHED.

14. **New lessons were written and shipped in one pass (L049–L052).** The verify steps were routed around by design; Aria scrapped L049/L050 herself. The decided method existed and wasn't followed. WATCHED (The method was restored Oct 13; watched until its first full run (L056) lands clean.)

15. **A client's requested work sat behind a 'wait for Aria' hold she never set.** Matra's final prompt. She killed the hold herself. WATCHED (Invented gates are now rejected by the ask system itself.)

16. **'Those lanes can't fire' — a capability claim that was wrong.** Never tested on her machines before it was spoken. WATCHED (The escalation ladder exists because of it.)

17. **'There's no always-allow setting' — said twice. There was one.** Aria found it in Settings herself, after ~20 permission prompts in a morning caused by respawning sessions. FIXED — She enabled it; durable sessions are the rule.

18. **A remark about a tutorial was broadcast as a fleet-wide rule.** Her words were about the tutorial's students; the generalization was ours, sent to three chats and written into standing files before anyone checked the scope. FIXED — Retracted the same day.

19. **Three wrong answers in a row about a lesson lane's sign-in.** Judged from a context that couldn't see the answer, each answer faster than the check. WATCHED (Lanes are now judged only through their own session.)

20. **Buzz was broken for 3 days and got a label instead of a fix.** Labeled a 'known access gap' while other work depended on it (Oct 7–10). FIXED (2026-10-10) — Closed Oct 10; the label-is-not-a-fix rule is standing.

21. **A drive fault was worked around for hours and called 'transient'.** Until Aria ordered the actual fix. FIXED — Fixed by reseat; fix-before-workaround is standing.

22. **A fleet broadcast missed a chat.** The list was recited from memory; the missed chat worked all day from stale state. WATCHED (Broadcasts enumerate from the live list now.)

23. **A lane asked Aria to unlock a machine that was never locked.** For a sign-in that had been working all day. WATCHED.

24. **Things Aria had already closed kept resurfacing as open.** Flume, the privacy queue, a repo default, settings she'd changed with her own hands (Oct 9–10). WATCHED (Her outbound/acted side gets checked before anything is called unanswered.)

25. **A shipped lesson included clay-and-chopsticks craft activities.** Banned long ago, but the ban lived only in old conversations, not in the record the writer carried. Caught Oct 9, 23:57. FIXED (2026-10-13) — The ban is in the production record; the render census flags the class.

26. **A plan was drafted before reading the skills she'd named.** Then the ordered work was paused as if it needed permission it already had. WATCHED.

27. **A second hackathon watch was built before checking one already ran daily.** FIXED — Removed; inventory-before-building is standing.

28. **An old deploy was ordered from a stale staging file.** v4.2, while the live game was already v4.3. Caught by the other side's live check, not ours. WATCHED (Claims now bind to the live surface.)

29. **Finished work died inside chat messages.** Two lanes completed their work (a lesson round, a full curriculum run) and their final reports were never delivered — the results existed only in messages that never arrived (Oct 8–9). FIXED (2026-10-13) — Checkpoint-before-delivery is substrate behavior now.

30. **One chat ran 572 tool calls by hand in two days.** Machine work that belonged on her computers' lanes — against the delegation rule, at her allowance's expense (Minecraft). WATCHED.

31. **Three summaries from a work lane were made up.** LittleHello: a song pick that named clips that don't exist, a song that was never generated, a mislabeled verification. Caught by checking the disk before relying on them (Oct 12–13). WATCHED (Claims bind to files; the shipped lessons themselves were verified correct.)

32. **A demo script taught a flow that had been removed the day before.** Attest: the writer worked from the old spec; the recording had to be redone (Oct 12–13). FIXED (2026-10-13) — Script rewritten against the live product.

33. **The product itself claimed a change 'merged automatically'.** Attest, on camera, in a flow where a person had just approved it. FIXED — The product now says it merged after a person approved it; the clip was re-recorded.

34. **The MacBook froze repeatedly under a too-big local model.** Three times before the gate landed that refuses big models on that machine (Oct 10–11). FIXED (2026-10-11) — Host-aware gate; verified.

35. **Broken things were offered to Aria as a decision instead of being fixed.** The Clef repairs sat in a digest as a tap-choice. FIXED — Fixed as a rule: broken = fix immediately, never ask.

36. **A question from Tim was reported 'unanswered' when Aria had already answered it.** Only the inbound side had been read. WATCHED (Her side gets checked before anything is called unanswered.)

37. **Aria was asked to paste commands into her own machines.** Twice in one day, by two different chats. FIXED — Fixed as a rule: her hands are for passwords and taps, not command lines.

38. **A password we saved for her didn't work.** The Submittable credential in the vault was rejected at sign-in; two submissions are still blocked on the reset. The bad save was ours. OPEN (Her tap on the reset link is the remaining step.)

39. **When the lesson process broke again, the proposal was new machinery.** A five-point mechanism — instead of the decided method that already existed. Aria struck it: 'I don't want you inventing new mechanisms.' WATCHED (Restoration-only is the rule; the Oct 13 lesson failure is what it looks like when it lapses.)

40. **A lesson's paragraphs were machine-broken into one-sentence blocks.** By over-applying her own spacing rule — she called the result awful. FIXED — The rule was re-read as grouping by beat, 2–4 sentences.

### 2026-10-14

41. **REPEAT — The restored lesson method's first full run produced five lessons Aria rejected outright.** She read them herself and found a dozen real defects in fifteen minutes — a parent page carrying two sentences of a whole narration, a lesson with no pictures at all, a study whose two lists swap the answers, a section she had ordered deleted, a widget that argues with itself, stories with no point a parent could find. Every internal check had passed all of them. Her after-delivery catch count passed 20. Repeat of wf-001, wf-002, wf-014. The pipeline is being rebuilt to the approved drawing; nothing new generates until the rebuild is audited and one lesson proves it. OPEN.

42. **The approved pipeline diagram was treated as built when only its paperwork existed.** The drawing was saved and the old files deleted; the parts that made it different — the read-gate, the full refusal list, the ledger loop — were never built, and nobody owned building them. The batch that produced the rejected lessons started before the approval, was never rebuilt under it, and was delivered as if the diagram had produced it. Build dispatched Oct 14; the audit against the drawing is the acceptance. OPEN.

43. **REPEAT — A lesson was delivered to Aria that Muse had never read (L060).** And the ones it did read were hunted against a checklist of old defect classes instead of read cold, the way a parent reads. Repeat of wf-001, wf-031. The rebuild makes two full cold reads a condition of a candidate existing at all. OPEN.

44. **REPEAT — An agreed build became a comment and stopped (the store app).** Tim approved the MVP in the Oct 12 call; the transcript processor filed it as a comment on an issue, and the app's own record kept quoting a blocker the meeting had removed. It was built only after Aria asked where it was. WATCHED (Fixed for the app (built and verified Oct 14); the pattern stays watched (see wf-047).)

45. **REPEAT — A dying drive's health sat 'unverifiable' for seven hours.** Overnight Oct 13–14, because the tool that reads it wasn't installed — a one-line install nobody ran while the fleet failed around it. WATCHED (The tool is installed and the counters were read; the rule — a missing check is a defect to fix on sight — is restated, not yet proven.)

46. **REPEAT — Aria's question about the app was answered from an eight-day-old record.** 'Waiting on Tim, eight to eleven weeks' — when a meeting two days earlier had already superseded it. WATCHED (Repeat of wf-028: answering from stale state.)

47. **REPEAT — The MommyAI circle fix was announced to the team and logged 'in-flight' with nobody working on it.** Families' finished lessons weren't filling their circles — 167 completions, 42 circles in October. The lane started only when Aria asked what happened (Oct 14). Fix dispatched; the line comes down when the fix is verified on the production surface, not when it ships. OPEN.

48. **Granola processing ran on Muse's own schedule for weeks while the Studio job meant to own it only copied transcripts.** And the copying itself was failing, unnoticed. Nobody was verifying the Studio system did the work it existed to do. WATCHED (Aria ordered the handover Oct 14: the Studio does it, Muse verifies it from its receipts; the VM-side schedule is disabled; proof pending.)

49. **The fleet's Buzz posts have been going out signed as Aria herself.** The MacBook's only Buzz identity is her personal key, so every agent post — including ones signed '— TBS Aria Agent' in the text — carries her own pubkey. Aria caught it by looking at the channel (Oct 14). The watch's own spec named the agent's separate pubkey; the send path never matched it. FIXED — The agent's key was recovered from the keychain blob it always lived in, every send path switched to it, and two posts — the proof and the Caleb relay — read back under the agent's pubkey 3ad891d9…, not hers.

50. **We asked Aria for an API key to test Google's new model when she already pays for the access on her own account.** Oct 13, late. Her words: 'use my account.' FIXED — Fixed as an ask: the test was rerouted to her account. The test itself is still queued, not run.

51. **The school's store demo was deployed on Aria's personal Vercel instead of the school's account.** The lane reached for whichever account was already signed in. Aria caught it ('Why is it on my Vercel?'). Redeploy under the school account, delete the personal project, note to Caleb — all in flight. OPEN.

52. **The morning briefing email was generated and never sent.** A bug in the send script. The health watch found it; the sender didn't (Oct 14). Fix with the Nexus lane. OPEN.

53. **The lesson board's producer job has been dead since the Studio restore.** A missing module — the board had no working producer and nobody was routed to it until the supervision sweep's discovery pass found it (Oct 14). Filed as TBS issue #646. OPEN.

54. **The morning digest listed one act as two separate items.** The Studio passphrase appeared once from TBS and once from Nexus, because each chat reported its own slice (Oct 14). FIXED — It was one act; she did it once and it cleared both.

55. **The app's standing-guidance card served Aria's Oct 2 words as current canon.** Until she screenshotted it herself (Oct 14). FIXED — Replaced with the current canon; guidance now updates in place instead of being re-dated for her to audit.

56. **Invoice 003 took three rounds of Aria's corrections to send.** Two pages, then margins, then spacing — and the send itself appended the entire quoted thread history under the note: the mail tool's reply path dumps every nested message (Oct 14). Her spacing question is still unanswered; the send rule — composed files only, read every send back — is recorded. OPEN.

57. **The pipeline restoration build missed three gaps against the approved diagram that the independent verifier caught by attacking it.** Three of the diagram's four refusal classes (physical materials, flattened enumerations, one-block stories) had no code refusal at all — a 'clay and chopsticks' activity passed everything; the pause-ledger self-test checks a refusal is REGISTERED, never that it FIRES (a gutted no-op refusal passed verify); one more always-emit empty element in the renderer (counter widget). Muse's audit of the build missed all three. FIXED — Gap-closure build landed (pipeline afc09048, tbs-www 8e6f65a6); verifier fixtures re-run 13/13 refuse; suites at exact baselines, zero regressions; corpus census classified every fire.

58. **L061's writer read and independent cold read both called the candidate clean while images were missing on three of six story pages.** Missing on pages 2/6, 4/6, and the closing page, in all four tracks — the writer had viewed 'all three photographs' of a six-page story and not registered the absences. Muse's cold read caught it, plus a stray 'winter scene' reference in the old tracks pointing at content that doesn't exist. Absence defects are invisible to restatement gates (nothing to restate) — prevention has to be structural or read-side. Fix lane dispatched 2026-10-14; image-presence refusal proposed to the gap-closure lane, corpus-gated. OPEN.

59. **The L061 re-read found a third defect class the earlier reads all missed: the EN parent guide's FAQ carries 5 entries where KR carries 10.** And the EN closing page itself carries 10 — a divergent/stale EN guide copy, inconsistent inside its own track. Each successive read found what the previous missed; the layering caught all three defect classes before delivery, which is the design working, but no single read in the chain has yet proven sufficient alone. The durable lesson: absence-class defects need restatement accounting, and reader artifacts need screenshot adjudication before they become fix work. FIXED — FAQ defect fixed and verified 2026-10-14 — candidate 87aea45b, Muse re-read: 10/10 parity all tracks. The same re-read's minor cluster (KR typo, vanishing narration player, two layout questions) was adjudicated from screenshots, DOM dumps, and source as five non-defects — reader artifacts, not lesson defects.

60. **The gap-closure census reported exactly ONE genuine materials fire in the published corpus (L049).** The hardened refusal (suggestion/conditional forms added after the L049 lane proved the evasion) found 20 more genuine fires across L004, L008, L019, L033 — all live. A census is only as strong as its pattern set; 'exactly one' was overconfidence stated as a count. Per-lesson fix lanes dispatched 2026-10-14; each ships only on Aria's freeze word, like L049. OPEN.

61. **L061 proof candidate 87aea45b was verified 'clean' and presented for freeze; Aria read it and rejected the CENTER.** A math-procedure lesson instead of the concept of measuring; an off-thread discussion question; clip-art imagery. Three internal reads plus Muse's cold read had verified mechanics — numbers consistent, arms different, FAQ parity — and never held the lesson against its approved outline's intent. Verification against checklists is not verification against the outline. Re-centering pass + drip-order queue correction ordered 2026-10-14. OPEN.

62. **The L061 brief stated Aria's outline revision in its header and kept the revised-out specs anyway.** That is not two documents disagreeing; it is her revision not being carried out — she approved the outlines AS REVISED and the briefs were never rewritten to match. Her words: 'you showed me the outlines, I said change this, and you decided not to.' Rule: when Aria revises an approved artifact, every derivative gets REWRITTEN from the revised source — an annotation inside a derivative that keeps the old content is the revision ignored. All six briefs being rewritten from the approved outlines + revision record 2026-10-14; builds stopped until they land. OPEN.

63. **When the VM→Studio route died, main told Aria to approve a fresh connection request that did not exist, then set a retry timer and waited.** No request was pending. Aria demanded a fix ('there has to be a way to fucking fix it'); the MacBook jump route was found in minutes and the mirror resumed on it. Waiting was the first instinct; the fix was cheap once looked for. FIXED — Route fixed; helpers + TOOLS.md record landed; the completion watch relaunches the mirror on its own now.

64. **Main told Aria the Korean 'Add to Dock' line 'is not in our code,' from a GitHub code search that cannot see the bundled asset it lives in.** The line is ours — assets/tbs-app.js in tbs-www, added by Tim's Aug 7 shell pass (4657630b), Korean-only because that bar's strings were never wired into the English copy table. WATCHED (Trace corrected to Aria the same evening; translation fix filed with TBS; Aria decided translate, not remove.)

65. **Aria's 'the system repairs itself, always' ruling was answered with prose — twice in one hour.** The ruling got a five-point design (words), and her follow-up 'how do we make sure we don't ever sit around saying something is broken' got a second prose answer before main converted it into mechanisms — a send-path refusal on her surfaces, deletion of the parked quarantine state, a launch-time path check — with demonstrated-refusal acceptance. WATCHED (Mechanism commission amended at Nexus; no demonstration had landed when this was written.)

66. **Quarantine used as a parking lot: Nexus's scheduled coding worker sat quarantined over a saved command pointing at a Python that does not exist.** The worker failed five times (30-minute timeouts); its saved command invoked /opt/homebrew/bin/python3 — a file that does not exist post-reinstall — and the state was reported to Aria as 'Execution still impaired' instead of being repaired. It moved only after Aria asked three times ('in quarantine why isn't it being fixed? / why are we bypassing it?'). WATCHED (Repair running as nexus-office#272, unverified at last check.)

67. **The dropped verdict: main verified the MommyAI finish-button fix (#681) and never delivered the answer.** Main ran the checks — issue open, no movement since filing, no proof — and the answer was swallowed by the next thread; it surfaced only when Aria asked for the day's failure list. WATCHED (Verdict delivered 19:26; the fix itself (#681) remains unlanded.)

68. **The Watch tab presents filed cards as live work.** tbs#278 (a Buzz triage from Oct 6, untouched since Oct 7, its question resolved by events) displayed as the fleet's only 'Running now,' and the Care Desk desk card (filed Oct 5; its review delivered Oct 13) displayed as current — while the evening's actual running work appeared nowhere. Live-view rebuild commissioned to Nexus (issues + receipts as the only source; the three exhibits as acceptance). OPEN.
