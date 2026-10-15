# Self-repair — the number one rule, as code (issue #273)

Aria, 2026-10-14: "We need to fix the actual system so it repairs it always
always always always always — number one rule."

A fault, a repeated failure, or a quarantine triggers the system's own
repair in the same motion. Quarantine is a repair trigger, never an end
state. A failed repair escalates with evidence, never goes quiet. None of
this is prose: each rule below is a refusal in code, demonstrated on the
issue.

## The three shapes

1. **Refusal at her surfaces** — `nexus/surface_guard.py`, wired into
   `client/office_feed.py:publish` (work-category posts). Text asserting a
   broken/unrepaired state is BLOCKED at send time unless it carries a
   repair reference whose repair task is verified LIVE in the ledger at
   that moment — or the tier-3 escalation reference (≥3 attempts, the
   single act only she can do). Her surfaces carry completions and
   receipts; broken states live on the issue where the repair works them.
   `tower.status()` and the board card (`client/tower_board.py`) follow
   the same contract: a stopped plan reads REPAIRING with its repair task
   and attempt count while its repair is live, and counts as "needs a
   person" only when the repair is exhausted.
2. **Deletion of the parked state** — ledger schema v4. A plan's
   quarantine record REQUIRES `repair_task_id` naming a live repair task:
   the column, a BEFORE UPDATE trigger, and `quarantine_plan`'s own check
   all refuse a bare quarantine, and entering quarantine + opening the
   repair are one transition (`selfrepair.attach_quarantine`). Verified by
   attempting one bare, at the API and at the store: both refuse.
3. **Refusal at launch** — the Tower's launch path and jobrun's preflight
   (section 1c) verify the pointer positions of a command — the program,
   and a shell/interpreter's script argument — before anything runs. A
   dead pointer fails the flight at launch with code `dead_pointer` and
   opens its repair in the same motion: zero timeouts burned. Data
   positions (arguments, outputs the command is about to create, env
   values) are never judged: a guard that judges data refuses healthy
   work.

## The loop

- **Triggers**: quarantine (one transition, above); ≥3 consecutive
  failures with one error signature (repeat trigger — the repair opens in
  parallel, the plan keeps flying); a jobrun preflight filing
  (`nexus repair-file`); legacy quarantines are adopted on the next tick.
- **Repair tasks** are Tower work items on the `nexus-self-repair` plan,
  flown by `nexus repair-drain`. The failure packet rides in the task:
  plan/job, command, recent receipts, the error verbatim, the signature.
- **Envelope (the standing grant — pre-authorized, no ask at any tier)**:
  correct config values and binary paths (each correction verified: the
  new target exists and answers), clear stale state the mechanism itself
  owns, restart the worker, repair dependencies, re-run. Never: code
  semantics, money out, contracts, her voice, credentials, deleting
  data, irreversible acts. A fix outside the envelope is an escalation,
  not an improvisation.
- **Proof**: after the fix the worker re-runs as a proof flight on the
  normal machinery. "Repaired" = the worker's own completion receipt
  (produced, with its declared outputs). The repair task closes with
  that receipt flight linked. Proof flights never count in the plan's
  own failure history — the mechanism's re-runs are accounted by
  attempts, not by the streaks they are clearing.
- **Escalation**: a failed attempt re-opens with its evidence (attempt
  history on the task + the issue). Second failure → the owning
  coordinator (comment on #273). Third → Aria, in the only shape allowed
  to reach her: what broke, what was tried, the single thing only she
  can do. The tower's safety-net pass watches the two states that must
  never be silent: the repair plan itself stopped, and a repair abandoned
  without its escalation.
- **Standing down**: a person releasing a plan outranks the loop. A
  repair whose plan was released after it opened closes as stood down
  (delivered evidence: the release) — never as a failure, never
  silently. A repeat-signature repair never stops its plan unilaterally;
  the quarantine threshold owns that decision.

## The close-check, and the one honest limit

No repair closes whose fault class lacks its guard. Current guards:
dead binary/script path → shape 3 (Tower + jobrun). A brand-new fault
class has no pre-written refusal, so its FIRST occurrence can still
surface; its repair cannot close without installing the guard, so the
second cannot.

## For domain coordinators

- Your plans' quarantines attach repairs automatically. There is nothing
  to opt into and no relay to file through.
- Hand-parking a plan (a bare quarantine, a held task with no repair) is
  no longer representable. If your coordinator needs a plan stopped,
  stop it through the ledger like everything else — the repair opens
  with it, and "repaired" is proven by the plan's own receipt.
- If a repair of your plan reaches its second failed attempt, the
  escalation lands on #273 naming the plan; the third reaches Aria with
  the full history. Silence from the mechanism is a defect in the
  mechanism — file it on #273.
