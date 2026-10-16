# Office watchdog source conversion (#304)

The first source is `office-watchdog-door`. The existing Studio job writes an
`office-door-shadow/v1` effect for each UTC half hour and reads it back after an
atomic publish. `python3 -m nexus watchdog-door-shadow --receipt PATH` now
validates that receipt and appends an idempotent `watchdog.observed` event to the
Tower ledger. A failed check also opens a `detected` fault record. A later
healthy observation marks a detected-only fault `recovered` without claiming
a repair. Late and out-of-order receipts are retained, but old evidence cannot
change current fault state. The hash checks accidental alteration, not source
authentication; the CLI trusts its local caller and must only receive receipts
from the protected Studio job state. It does not infer a repair or escalate a
technical defect to a person.

This is a source adapter, not a new owner. The Studio probe cannot prove its own
absence, arbitrate a Studio outage through MacBook, or inspect the phone path
from the VM. Muse's door body still owns detection, its one kickstart, and
surfacing. The source job's command and Muse cron are unchanged by this PR.

Before connecting the Studio job to the adapter: verify two same-window
comparisons against the Muse door state, including loopback and phone outcomes;
inject loopback refusal and phone-only failure; then prove fault records are
read from the Office destination. The external Cloudflare absence detector and
one-owner cutover are separate gates. The other three sources (dispatch,
daemons, comms) retain their current routes until this pilot has parity.

The active authority stays bounded: a later owner may make at most one
`com.aria.office-serve` kickstart for a failed loopback/stale snapshot, and only
after SSH plus MacBook path arbitration. Phone-only failure has no restart
authority. It must verify live health after an attempted repair before recording
`verified_fixed`; an unfixed technical fault stays in the machine record.
