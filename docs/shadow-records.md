# Cloudflare shadow of the record layer (#310)

The Studio ledger is the authority. The shadow only copies it, judges each copy with its own rules, and keeps the parity evidence. Nobody reads the shadow operationally: no UI, no routing, no cutover, nothing written back.

## Resources (personal "aria" account `efe6d980…`, all `nexus-shadow-*`)
| what | name | id |
| --- | --- | --- |
| Worker | `nexus-shadow` | https://nexus-shadow.ariaxhan.workers.dev |
| D1 | `nexus-shadow-records` | `70b3fe14-4df1-42c5-8e5c-00a8ba539351` |
| Queue | `nexus-shadow-dispatch` | `77e292d91eff4442acd4d33731321951` |
| R2 | `nexus-shadow-receipts` | bucket |

- Config: `shadow/wrangler.jsonc`. Schema: `shadow/migrations/`. Secret: `SHADOW_TOKEN`. The Studio copy is `~/.nexus/shadow-token` (mode 0600).
- Deploy from zsh, so `~/.zshenv` supplies the token: `cd shadow && npx wrangler@4 deploy`.
- No collision with Attest (`attest-*`), Office (`nexus-office`) or fleet-schedules (`fleet-*`).

## Shared event schema `nexus.shadow.record/v1` (pinned)
```
{schema, namespace: office.dispatch | office.fault, record_id, revision, event_id,
 record_json, sha256, transitions: [{seq, at, from, to, attempts?, studio_outcome}],
 origin, observed_at}
revision = sha256(record_json + canonical(transitions))[:16]
event_id = <namespace>:<record_id>:<revision>
seq      = the Studio ledger event id of the step
```
- `record_json` is `canonical(record)`: sorted keys, no whitespace. The record is exactly what `dispatch.get` / `faults.record` return, so D1 holds it byte for byte. Ingest recomputes `sha256` and refuses on a mismatch.
- Content addressed: a resend is a no-op (`INSERT OR IGNORE`).
- This is the same key shape as fleet-schedules' `(namespace, occurrence_key, generation)`: namespace and record id, plus a version. `office.*` namespaces belong to this stream only. The shadow does not schedule anything, and it owns no effect.

## Authority by phase
| phase | authority | shadow |
| --- | --- | --- |
| parity (now) | the Studio ledger, in every conflict | copy + judge; zero operational readers |
| graduation | Aria decides, separately, from `/parity` | unchanged |
| any cutover | needs the fleet-schedules #7/#8 effect-proof and fencing gates first | not authorised by #310 |

## Path
1. **Producer.** `nexus/shadow.py` keeps a cursor over ledger event ids, for `dispatch.state` and the `repair.*` / `watchdog.recovered` events. Every record those events touch is sent as its current revision.
   - This is one seam for every producer, present and future, so no producer needs its own hook.
   - The Tower calls `shadow.tick` on every tick, for the Studio ledger only. It is never fatal.
   - The first push sets the cursor to the newest event; there is no backfill unless you pass `--from-id 0`.
   - The cursor moves only past a successful send.
2. **Ingest** (`POST /ingest`, bearer). Each envelope goes to the D1 `records` table, then an R2 receipt is written at `receipts/<ns>/<record_id>/<revision>.json`, then its `event_id` goes on the queue.
3. **Consumer.** `shadow/src/parity.js` restates the poller's rules and the ledger triggers, and does not import them, so any drift between the two shows up.
   - For each dispatch step: the Studio outcome, and the set of outcomes the shadow's rules allow from the prior state. Some branches depend on facts outside the record, such as a registered worker or elapsed time; for those, the shadow allows every outcome its rules permit.
   - Gate verdict: is the arrow legal, and do the trigger fields hold?
   - Faults: the arrow from the previous revision, plus the field rules for each state.
   - Launches go to `simulated_invocations` with `fired=0`, enforced by a CHECK. Nothing is ever fired.
4. **Comparator.** A cron every 10 minutes (or `POST /compare`) judges anything the queue missed, then appends a `parity_runs` row.

## Readout
- `nexus shadow parity [--record-id ID]` (or `GET /parity`, bearer) returns:
  - the latest summary;
  - the run count and when the first run happened;
  - the last 20 mismatches;
  - one record's pairs.
- This running summary is the evidence for the graduation decision.

## Limits (stated, not hidden)
- A record is sent as of push time. When several steps happen between two pushes, they arrive as one revision with every step listed. The receipt content of a step that was later refiled is gone from the snapshot, so for that step the shadow allows all three receipt branches.
- Fault records advanced only by `task.state` (the drain starting) are copied on their next `repair.*` event.
- Comparator results are not yet on the Office control room. Publishing them there is a follow-on.

## Acceptance
- Command: `scripts/shadow-demo.sh`. It runs the real producers on a sandbox ledger, writes to the live shadow, and reads the results back.
- Regression: `tests/test_shadow.py`. It runs the deployed `parity.js` under node, on envelopes the Studio built.
