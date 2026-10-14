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
