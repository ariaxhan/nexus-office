---
type: chronicle
status: active
created: 2026-08-29
---

# The five bots can see the office, and Sphinx's answers move GitHub issues on their own

**What mattered:** Bot identity without shared live evidence was costume: a grep-based Sphinx reported zero blocked issues against 104 live ones. Now every bot turn carries the wall, and an answered Sphinx question comments, unlabels or closes the issue with nobody clicking anything.

**Shipped**
- nexus-office 92b5a5f, 3deb415: `client/chat.py office_evidence` attaches a 48k-char budgeted snapshot to every turn, waiting-on-human rows first, counts exact.
- tradition-harness 485e7e7: `dashboard/chat.py office_evidence_block` renders it as `<office_evidence>`, labelled data-not-instruction.
- nexus-office 05dd6c6, 6a0f811: Sphinx ends an answered question with a fenced `office-decisions` block; `World.bot_decisions` applies it as `unblock` or `close`, only for Sphinx, only for issues on the wall.
- tradition-harness 492cdb0: a bot turn is answer-mode, never an edit contract.
- Vaults 4ce2f3695, 10953cc4c: Sphinx identity carries the block contract.
- Cherry-picked onto main after the 08-28 history rewrite orphaned three PRs: 133e05e faces (#41), 6a69ecb settings (#44), 42825a1 + 9c8a36e readme/context (#54).

**Verified how:** `npm test` 749 python + Swift OK; harness `uv run pytest` 1241 passed. Live: Sphinx asked "how many ariaxhan issues wait on me" answered from `/api/world` (104 issues, 57 waiting; first pass saw 19 because the harness cut the block at 60k, fixed by producer-side budget). Live: answering Sphinx "#20: B" posted the comment as ariaxhan and dropped `waiting on human` on ariaxhan/nexus-office#20, roster row `decided: ok`. Both daemons restarted; state: merged on main, deployed on this Mac, working live. `/api/readme` ok for three repos after restart.

**Wrong or surprising**
- The three "disappeared" PRs were closed by GitHub when main lost their merge base (history rewrite 08-28 17:02), not rejected.
- The first live decision turn died: `classify_task_mode` read "close it out, keep the Worker" as a file-edit contract and the harness rejected a correct reply for lacking `write_file`.
- `python3 -m unittest tests.test_x` is not how the suite runs; `discover -s tests` is, and the two disagree on imports.

**Open:** #41/#44 remaining scope (resizable panes, presets, compare, persisted faces) in a builder lane at time of writing. #59 parked `blocked upstream` on tbs-care #2. `wrangler.jsonc` stays untracked by decision.
