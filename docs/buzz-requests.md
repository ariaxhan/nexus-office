# Buzz requests as records (#309, pd-10)

The listener writes a record for each request post, and that record is the intake. An agent reading the channel is not.

## Record
Table `buzz_requests` in the Tower ledger. It is created on first use, next to `dispatch_records` (#301).
- `post_id, channel, author, thread_id, posted_at, text, thread_state, state, state_at, work_order`
- `thread_state` holds both sides of the thread: `posts[]`, `requester_last_at`, `aria_last_at`, `last_by`.
- States:
  - `unanswered` / `answered`: always computed from `thread_state`. Aria's last reply at or after the requester's last post means `answered`.
  - `converted`: terminal. It holds the `work_order` (a dispatch record id).
- SQLite triggers refuse:
  - a state that `thread_state` does not imply;
  - `converted` without an existing dispatch record;
  - leaving `converted`;
  - changing identity fields;
  - deletes.

## What counts as a request
A thread root in `#general`, `#workroom` or `#queue` that is not by Aria and matches `office_buzz.channel_ask` (addresses Aria and asks).
- `#human-decisions` is read-only and has no records.
- This module never posts. Posting rules are unchanged.

## Listener
`python -m nexus.buzz_requests listen [ledger]` makes one pass over the live Buzz session (`tbs buzz read` via `client/office_buzz._read`). Run it on the MacBook, where the Buzz identity lives.
- Every pass refreshes every known thread. An Aria reply flips the state, and each flip writes a `buzz.request` event.

## Conversion
`buzz_requests.convert(led, post_id, queue, act, verify)` files a #301 record:
- kind `buzz-request`
- payload `{channel, post_id, author, text, thread_state, act, verify}`
- the queue is the owning lane's queue

From there `dispatch.poll` dispatches, verifies and escalates.

## Not wired yet
- No launchd/jobrun schedule for `listen`.
- No `nexus` CLI verb.
- Nothing calls `convert` automatically. Today the caller picks the owning lane.
- `dispatch.poll` is not yet called from the Tower tick; run `nexus dispatch poll` by hand or on a schedule.

Tests: `tests/test_buzz_requests.py`.
