# Dispatch records (#301, first landing: broker queues)

Queues are rows that code polls. Nobody has to notice anything.

## Store
- Table `dispatch_records` in the Tower ledger, created on first use (no schema-version bump, so older readers are unaffected).
- Fields: `record_id, kind, queue, payload, state, created_at, state_at, claimed_at, dispatched_to, receipt, verification, attempts, escalation`.
- States: `filed → claimed → receipt → verified → closed`, plus `escalated` with `{act_needed, kind}` (the same shape as #300).
- Every transition writes a `dispatch.state` event in the same transaction.
- **Triggers enforce the rules inside sqlite**, whoever is writing:
  - only the arrows in `dispatch.TRANSITIONS` are allowed;
  - a record can only be inserted as a fresh `filed` row;
  - `receipt`, `verified` and `closed` need a receipt;
  - `verified` and `closed` need a `done` receipt and a passing surface read;
  - `escalated` needs an `act_needed`;
  - attempts never go down, and identity fields never change;
  - rows can't be deleted.

## Poller
- `dispatch.poll` runs on every Tower tick. The Tower is `com.nexus.tower` on jobrun. You can also run one pass by hand with `nexus dispatch poll`.
- **filed + worker**: claim, increment attempts, launch the worker detached. A sqlite guarded claim allows one active holder per queue. The worker gets the record in `NEXUS_DISPATCH_RECORD`, its id in `NEXUS_DISPATCH_RECORD_ID`, and the selected absolute ledger in `NEXUS_LEDGER`.
- **filed, no worker, past 10 min**: escalate as `config`.
- **claimed past the worker's `bound_s`** (default 30 min): stop and confirm the previous holder ended, then re-dispatch on age alone. After `MAX_ATTEMPTS` (3), escalate as `hands`.
- **paused Tower**: reconcile receipts and surface verification; do not launch, retry, or refile work.
- **receipt**: run the record's `verify` argv.
  - rc 0 → `verified`, then `closed`.
  - Non-zero rc, or a `failed`/`partial` receipt → back to `filed` (the attempts count is kept).
  - A `blocked` receipt → `escalated`, with the receipt's `next` as the act needed.
- The worker's only write is `nexus dispatch receipt <id> ...`, in the #291 grammar. Prose is rejected.

## Broker adapter (#263 Studio, #264 MacBook)
```sh
nexus dispatch file --queue broker-studio|broker-macbook \
  --requester ... --act ... --shell-check ... --surface ... \
  --verify '["argv","that","reads","the","surface"]'
```
- `verify` is required. A request with no code-readable completion surface can't be filed.
- Default worker is the holder lane: `account-router.sh run code-judgment -p <holder prompt>`. It is registered only on the host that owns the queue's device:
  - `broker-studio` on `arias-Mac-Studio`;
  - `broker-macbook` on `arias-MacBook-Pro-2`.
- Override per queue in `~/.nexus/dispatch-workers.json` (or the file named by `$NEXUS_DISPATCH_WORKERS`): `{queue: {argv, bound_s}}`. In `argv`, `{prompt}` and `{record_id}` are substituted.
- Each machine's ledger serves its own device. File MacBook requests on the MacBook. If a `broker-macbook` record is filed on the Studio, no worker there can take it, so it escalates as `config`.
- #263 and #264 stay as the historical record.

## Commitments (#308, Granola)
- `nexus dispatch commit --key --owner --act --source --surface --verify '<argv>' --stall-s N`: kind `commitment`, queue `commitments`. Idempotent on `key`.
- No launched worker. The poller claims a filed commitment for its owner (`dispatched_to=owner:<who>`). Many can be held at once; the one-holder rule is for device queues.
- While it is claimed, code reads the surface every 30 min (`COMMITMENT_CHECK_S`), and each read is a `dispatch.surface_read` event. rc 0 → a `done` receipt from `surface` → `verified` → `closed`. Past `stall_s` → `escalated` (`hands`, owner + act).
- An owner can also write a receipt by hand with `nexus dispatch receipt`. It is verified against the same surface.
- The Granola processor (`Vaults/_meta/services/granola_processor.py`) files records once its audit passes:
  - every issue and build order is a record, surface `repo#n closed`, bound 7 d / 14 d;
  - every executed Aria item with a path is a record, surface `path exists`, bound 1 d;
  - the record ids are kept in `state.json` and `receipts.jsonl`;
  - a failed filing marks the meeting `error`.
- The TBS coordinator-inbox row is gone: the record is the queue.
- `--emit-records` sends the archive through the same path.
- `needs-aria` items stay in the digest because they have no code-readable surface.
- Acceptance: `scripts/granola-records-demo.sh`.

## Acceptance
Run `scripts/dispatch-demo.sh`. It uses a sandbox ledger, the real poller and real detached workers. Output from 2026-10-16:
```
dsp_a308b39a6c77 broker-studio  closed    attempts=1 broker-studio:pid=18139 pass
dsp_e7b92879d120 broker-macbook escalated attempts=3 {'act_needed': 'starved act -- by hand on broker-macbook (no receipt after 3 dispatches)', 'kind': 'hands'}
walk: filed claimed claimed receipt verified closed
UPDATE ... state='receipt'  -> Error: no state past claimed without a receipt
UPDATE ... state='verified' -> Error: verified needs a done receipt and a passing read of the surface
```
Regression coverage: `tests/test_dispatch.py`.

## Not in this landing (Phase 2, filed separately)
- human_asks adapter
- chat-carried substrate orders
- digest / needs-Aria items
- Buzz asks
