# Restart safety: controlled validation

The disposable webhook and human-ask cases run in `tests/test_webhook.py`,
`tests/test_serve.py`, and `tests/test_human_asks.py`. The live service stays on
its current revision until the complete Office gate passes and the stable
service wrapper has its release compatibility preflight.

## Controlled live sequence

1. **Accepted webhook before dispatch.** Point an isolated Office instance at a
   disposable webhook state directory and a fake dispatch runner. Pause its
   drainer before the debounce expires; deliver one correctly signed event.
   Confirm HTTP 200 only after a pending SQLite obligation exists. Stop that
   instance, restart it against the same directory, and confirm one dispatch,
   one settled obligation, and a duplicate redelivery that starts no second run.
2. **Pending obligation without notification.** Commit an event directly to the
   disposable mailbox and start a fresh Trigger. Confirm startup replay and
   settlement. Deliver two events for one repo before restart and confirm one
   debounced dispatch with both delivery IDs settled.
3. **Active Ask work.** Use the separate Ask worker and a disposable Ask database
   with a provider fixture that pauses after a file action. Restart only the HTTP
   service. Confirm the worker and provider turn continue, the request ID and
   conversation remain stable, and the file action occurs once. Then stop the
   worker in a separate disposable run: a turn with uncertain side effects must
   report interruption rather than replay the action blindly.
4. **Human asks across restart and redeploy.** Seed a disposable ledger with an
   open permission on a stopped flight. Confirm Needs You shows its original
   permission ID as stale before and after HTTP restart and switching between
   compatible release directories. Insert the matching closure event and confirm
   it disappears with a resolution receipt. Try an older release without the
   human-ask reader and a newer-schema store; the stable wrapper must refuse
   startup while an open ask would be hidden or the schema cannot be read.

For any live service restart, first capture the current Ask request/turn IDs,
worker PID, webhook pending IDs, and open human-ask IDs. Compare them after
restart. Do not manufacture a production permission solely for this test.

## Boundary

The webhook obligation is durable before acknowledgement and replayed at least
once until the local dispatch attempt is settled. If the service dies after an
external action but before settlement, replay can repeat that action unless the
external pipeline reconciles it by its own IDs or receipts. The webhook delivery
ID deduplicates intake; it is not an exactly-once guarantee for downstream
external side effects.
