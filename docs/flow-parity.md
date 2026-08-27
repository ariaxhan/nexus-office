# Where every `flow` number lives now

`_meta/services/flow` printed six panels of the vault's pulse. Nothing called it any
more, and the office already showed most of it. This is the audit that had to pass
before it could be deleted: every fact flow reported, where the office holds the same
fact, and the two values side by side.

**37 facts. 23 already matched. 8 had no home and were ported. 6 differ on purpose,
and the reason is in the note.**

Flow was run once at `2026-08-27T07:23:47Z`. The office snapshot is
`world.generated 2026-08-27T07:21:46Z`, and the three ported sources were re-read
directly at about `07:33Z`, which is why one capture stamp is a run newer.

| panel | what flow reported | where it lives now | flow said | the office says | note |
| --- | --- | --- | --- | --- | --- |
| header | when this was measured | `world.generated` | `07:23:47Z` | `07:21:46Z` | two different runs, two minutes apart |
| capture | is the phone path alive | `mail.couriers[capture].state` | `live` | `live` | **ported** |
| capture | files queued in the iCloud drop folder | `mail.couriers[capture].waiting` | `0` | `0` | **ported**. `.gitkeep` is never counted |
| capture | items ever moved in | `mail.couriers[capture].delivered` | `2` | `2` | **ported**. Not the same as `on_disk.capture`, which counts filed notes |
| capture | when the watcher last completed a run | `mail.couriers[capture].last_run` | `07:20:15Z` | `07:30:16Z` | **ported**. One five-minute run newer |
| meetings | notes the granola sync holds | `mail.couriers[granola].delivered` | `51` | `51` | **ported** |
| meetings | when the sync last ran | `mail.couriers[granola].last_run` | `05:55:35Z` | `05:55:35Z` | **ported** |
| meetings | the sync's last error | `mail.couriers[granola].error` / `.state` | none | `""`, state `live` | **ported**. An error makes the state `failing`, note 1 |
| dispatch | kill switch | `world.killed`, `pipeline.kill_switch` | `false` | `false` | |
| dispatch | last full run of the runner | `world.heartbeat`, `pipeline.heartbeat` | `07:07:00Z` | `07:07:00Z` | now also a card row: "last full run" |
| dispatch | last decision per repo | `world.stations[].outcome/detail/at` | 22 repos | 72 desks | note 2 |
| dispatch | today's outcomes | `world.today` | 155 over 4 outcomes | 560 over 7 outcomes | note 3 |
| dispatch | how many repos it reached | `pipeline.covered.repos` | `22` | `22` | **ported**, with `covered.receipts` beside it |
| intake | can intake be read | `mail.state` | `gated` | `ok` | same answer, better word |
| intake | meetings cached | `mail.counts.cached` | `53` | `72` | note 4 |
| intake | watermark | `mail.watermark` | `2026-08-25` | `2026-08-25` | |
| intake | items filed | `mail.pigeonholes[].filed` | `3` | `3` (all granola) | the office splits it per feed |
| intake | meetings excluded by Aria | `mail.excluded` | `3` | 3 named meetings | names, not a bare count |
| intake | items the last run decided | `mail.counts.items` | `0` | `0` | |
| intake | items that could file | `mail.counts.would_file` | `0` | `0` | |
| intake | declined | `mail.held.declined` | `0` | `0` | never added to the two below |
| intake | blocked, by reason | `mail.held.blocked` | `{}` | `{}` | |
| intake | is the summary stale, and why | `mail.stale`, `mail.stale_reason` | `true`, "granola: 0 of 51 on disk; capture was not in the last run" | identical | the headline in both |
| intake | when intake last ran | `mail.last_run` | `2026-08-25T18:30:44Z` | same | |
| intake | what is on disk per feed | `mail.pigeonholes[].on_disk` | granola 51, capture 2 | granola 51, capture 2 | |
| intake | which feeds the last run covered | `mail.pigeonholes[].in_last_run` | `["granola"]` | granola yes, capture no, email no | |
| intake | intake's own cache count | `mail.counts.cached` | `72` | `72` | flow carried this AND the wrong 53 |
| intake | meetings the last run excluded | nowhere | `0` | — | note 5 |
| issues | open issues | `world.stations[].issues` | `100` | `174` | note 6 |
| issues | how many are waiting on a person | `world.stations[].issues[].bot_last` | `53` | `154` | note 7 |
| issues | grouped by repo | `world.stations` | by repo | one desk per repo | the desks are the grouping |
| issues | how fresh the list is | `stations[].fetched_at`, `stations[].issues_error`, `world.github` | fresh, 1m old | `07:21:46Z`, no error, 4972 points left | the office also says what the fetch cost |
| jobs | jobs watched | `clock.checked` | `45` | `45` | |
| jobs | switched off | `clock.counts.off` | `8` | `8` | |
| jobs | unhealthy | `clock.alarm` | `2` | `2` | the office splits it: 1 stale, 1 never ran, 0 failing |
| jobs | per-job state, detail, attempt, success, rc | `clock.jobs[]` | 45 rows | 45 rows | the office adds schedule, command, owner, budget, watch |
| jobs | jobctl did not answer | `clock.state` | that string | state `timeout` | |

## The six that differ, and why

1. **A granola error is now a state, not a field nobody read.** Flow printed a red dot
   and nothing else. The office turns `last_error` into `state: failing`, which takes the
   mail card's headline and raises `needs`, so a sync that has been 429-ing all day stops
   looking like a sync with nothing to fetch.
2. **22 repos versus 72 desks.** Flow read the last 200 receipt lines, so it only ever saw
   the repos touched in roughly the last two sweeps. The office keeps a desk for every repo
   the runner has ever reached.
3. **155 versus 560 decisions today.** Flow counted the UTC calendar day over the last 4000
   receipt lines, and its printout dropped `survey` and `no-issues` from the line it showed.
   `world.today` is a rolling 24 hours over the whole file, because a calendar day resets to
   zero in the middle of Aria's afternoon. Checked at one instant, `pipeline.covered.receipts`
   and the sum of `world.today` are both **536** over the same **22** repos, so the two
   readings of that file agree exactly.
4. **53 versus 72 cached meetings.** Flow listed the cache directory one level deep and
   counted `owners.json`, `last-run.json` and the `email/` folder as meetings while missing
   everything inside it. The office asks intake, which counts `*.json` recursively and
   subtracts its own bookkeeping. 72 is the right number; 53 was never right.
5. **`excluded_meetings` has no home, deliberately.** It is how many the *last run* skipped,
   which is `0` whenever that run happened to skip nothing, even while three meetings stand
   permanently excluded. Flow carried it in `--json` and never printed it. The office ships
   the three exclusions by name instead.
6. **100 versus 174 open issues, 53 versus 154 waiting.** Flow ran one `gh search issues`
   across two owners with a hard `--limit 100`, so today it never saw `acme-second-account` at
   all, and it guessed at "waiting on you" with a regex over label names. The office asks
   per desk, across 72 repos and 6 owners, with no cap, and decides waiting by whether the
   pipeline bot had the last word, which is the thing the pipeline actually acts on.

## What was added to make this true

- `client/sources/mail.py` grew **couriers**: the mobile capture watcher and the granola
  sync, each with its state, what it has delivered, what is queued in front of it, when it
  last ran and its last error. A pigeonhole counts what arrived; nothing above could tell
  you whether anything was still arriving, and `never fired` reads exactly like `idle`.
  A stopped courier takes the card's headline off "nothing waiting to be filed" and raises
  `needs`.
- `client/sources/pipeline.py` grew **`heartbeat`** and **`covered`**: the last sweep that
  actually finished, and how many repos and decisions it reached in the last 24 hours. It
  deliberately does not tally outcomes, because `world.today` already does that from the
  same file and two tallies over one file are two numbers that will disagree.
- `client/sources/clock.py` was not touched. Every fact in flow's JOBS panel was already
  there, and the three headline numbers match live.

`_meta/services/flow` is deleted. Its two leftover state files,
`~/.local/state/vaults-flow.html` and `vaults-flow-issues.json`, went with it.
