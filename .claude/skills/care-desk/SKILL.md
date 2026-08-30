---
name: care-desk
description: "Operate the TBS care desk: hello@ threads become reviewable draft issues in tbs-care, written by Codex, checked mechanically, never sent. Trigger on 'care desk', 'hello@', 'tbs-care', 'care sweep', 'draft a reply for a parent', 'replay', 'voice check', 'why was this thread filed/not filed', 'the Care card is red'. Load before touching intake's care source, the drafter, the voice files, or the ontology."
---

# The care desk

One thread in, one reviewable issue out. A person reviews; nothing here sends. Built
2026-08-29 from Aria's call with Tim (nexus-office #59); chronicle
`_meta/chronicles/2026/2026-08-29-care-desk.md`.

## The rule that shapes everything

**Claude never writes or edits the Korean or English writing. Codex does.** Drafts, the voice
standard, the writer brief, KB answers, exemplars: a Codex lane
(`_meta/services/codex-lanes/codex-lane.sh submit`, model `gpt-5.6-sol`; `gpt-5` is refused on
the ChatGPT account). Claude does plumbing, lookup, checks, review.

## Where things live

| thing | path |
|---|---|
| live source | `_meta/services/intake/care.py` (hello@ via `thinking-brain-school/scripts/microsoft-mail/cli.js`, allowlisted to `inbox`, `read`, `export`) |
| drafter | `_meta/services/intake/extract.py` `run_codex_care`, cwd = `CodingVault/tbs-care`, read-only |
| checks | `_meta/services/intake/care_voice.py` (spec: `tbs-care/.local/review/voice-checks.json`) |
| issue body | `_meta/services/intake/issues.py` `render_care`, `render_care_followup` |
| job | `com.aria.care-intake`, every 900 s, `intake.py --file --source care --max-file 10` |
| Office card | `client/sources/care.py` reads `_meta/services/intake/cache/care-last-run.json` |
| corpus | `tbs-care/knowledge/`: `VOICE.md`, `WRITER_BRIEF.md`, `VOICE_RANKING.md` (Aria's ranking and 15 rules), `VOICE_KOREAN_PRAGMATICS.md`, `voice-exemplars/`, `TAXONOMY.md`, `COVERAGE.md` |
| archive (local, git-excluded) | `tbs-care/.local/mail`, `.local/threads`, `.local/voice/shortlist.md` (154, Aria's numbering), `.local/replay/` |
| lookup protocol | `thinking-brain-school/.claude/skills/care-answer/SKILL.md` |
| scripts | `tbs-care/scripts/care-threads.py`, `care-taxonomy.py`, `care_ontology.py`, `care-replay.py` |

## Commands

```sh
python3 _meta/services/intake/intake.py --source care            # dry run on the live inbox
python3 _meta/services/intake/intake.py --file --source care     # what the job runs
python3 _meta/services/intake/tests/test_care.py                 # source, drafter parse, render, checks
cd CodingVault/tbs-care && python3 scripts/care-replay.py --since 2026-08-01 --limit 12   # drafts for review, never files
cd CodingVault/tbs-care && python3 scripts/care_ontology.py --sample 30                    # precision check on the lexicon
gh issue list -R Thinking-Brain-School/tbs-care --label care
```

## Things that bit once

- A reply sent from Apple Mail on the Mini gets a NEW Graph `conversationId`: 340 of 399
  "unanswered" threads had a later sent mail. `care.py` reads Sent by address first.
- `covered_by` title similarity sees only "Reply" in a Korean title: care dedupes by marker only.
- Tim's repos under `thinking-brain-school/repos/` are gitignored, so dispatch skips them; the
  discoverable checkout is the sibling `CodingVault/tbs-care` with a local-only
  `.agents/pipeline.json` (`issue_only`, `intake: ["care"]`).
- The ontology must recognise the parent's OWN words: a RE: thread carries our reply, which names
  every product.
- Voice is Tim's LATE replies (#145, #148, #150, #152, #154), not the warm June ones. Warmth is one
  specific detail from her mail, never a sprayed tone. Never automate #140.

## When the Care card is red

`never`: run a dry run once. `dark`: `node cli.js whoami --profile hello`; re-`authorize` if the
token died. `stale`: `launchctl list | grep care-intake`, then the job log under
`~/Library/Logs/nexus-jobs/`. `error`: the failing lane is named in the snapshot's `threads`.
