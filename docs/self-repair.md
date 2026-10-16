# Self-repair — the number one rule, as code (issue #273)

Aria, 2026-10-14: "We need to fix the actual system so it repairs it always
always always always always — number one rule."

A fault, a repeated failure, or a quarantine triggers the system's own
repair in the same motion. Quarantine is a repair trigger, never an end
state. A failed repair becomes an escalated record, never prose. None of
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
- **Faults are records, not text (issue #300)**: the only fault
  artifact is the repair record in `nexus/faults.py`, a pure function of
  the ledger's `repair.*` events: `{fault_id, signature, detected_at,
  source, state, repair, verification, escalation}`, state `detected ->
  repairing -> verified_fixed | escalated`. A failed attempt re-flies
  silently. The third failure sets `state=escalated` with one line in
  `escalation.act_needed` and a `kind` (hands, decision, money,
  irreversible, identity). Nothing is posted to GitHub, filed as an
  issue, or written to human_asks; silence is the success signal. A
  repair task still unflown after `DETECT_WINDOW_S` is flown by
  `verify()`. The tower's safety-net pass records two faults of its own
  as escalated: the repair plan itself stopped, and a repair abandoned
  without its escalation.
- **Reading and emitting records**: `nexus faults list [--state
  escalated]` (JSON) is what main consumes. Watchdogs outside the
  tower emit and advance records with `nexus faults report --signature
  S --source watchdog:NAME --state detected|repairing|verified_fixed|escalated
  [--fault-id ID] [--action A] [--surface S] [--act-needed LINE --kind K]`
  instead of opening issues or printing FIX-REQUIRED lines.
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
- A repair of your plan that fails is retried silently; the third failure
  sets its record to escalated with the one act it needs
  (`nexus faults list --state escalated`). Nothing else is surfaced.
