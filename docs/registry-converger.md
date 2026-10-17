# Registry converger (#305, pd-12)

Job cadence lived in four places: the vault source registry
(`Vaults/_meta/services/jobs/registry.jsonl`), the deployed copy beside jobrun,
the plist launchd loads, and prose in the row's `note`. The prose is now fields.
`nexus converge` makes all four agree.

## Fields (on the job row)

- `cadence_s`: the number of seconds between runs that the job is meant to keep.
- `migration`: `{from, to, at, ref, reason}`. A change of schedule kind, recorded as data.

Lifted by `nexus converge-migrate --registry R --before <pre-conversion registry> --write`.
A note moves into fields only when the pre-conversion interval equals the
current calendar cadence, so a false "cadence preserved" claim stays visible.
Applied 2026-10-16: 53 rows (every `interval->calendar conversion` note).
Backup: `registry.jsonl.bak-converger-20261016`.

## Converger

`nexus converge [--check] [--report P]` (report path also from `NEXUS_CONVERGER_REPORT`):

| divergence | envelope | record |
| --- | --- | --- |
| plist or deployed schedule differs from the source | `jobctl sync`, then re-read | `verified_fixed` (surface = plist/deployed path), or `escalated` / technical if sync fails |
| `cadence_s` contradicts the source schedule | none: someone has to decide which one is right | `escalated` / decision |
| a later pass sees it clean | n/a | `recovered` |

- Records are #300 repair records (`source=converger:job-registry`), one per job and surface. They are never text.
- If there is no plist or deployed row yet, the install is still pending jobctl's own try gate. That is not drift.
- Schedules are compared by their set of firing times, not just by cadence, so a shifted offset also counts as drift.
- `faults.escalate` now allows a decision-class escalation without a prior repair, because such a fault has no repair inside the envelope to try.

## Proof (2026-10-16)

- Live `nexus converge --check`: `clean: true`, 101 scheduled jobs. `com.nexus.canary-wedge-d` = 21600 on all of field, source, deployed and plist. The 300s value appears nowhere on disk; the 2026-10-15 calendar conversion had already fixed the schedule, and only the prose was left.
- Sandbox with a seeded divergence: the canary-d plist was rewritten to `StartInterval 300`, and repair used jobctl's real `render`. The record went detected → repairing (`jobctl sync`) → verified_fixed, and a second pass was clean.
- `tests/test_converger.py`: 20 cases.

## Schedule

Registry row `com.nexus.registry-converger`: hourly at :17, `cadence_s` 3600, proof = report fresh within 2 h.
The row validates. To install it once a release carries `nexus/converger.py`, run `jobctl try com.nexus.registry-converger`, then `jobctl sync --only com.nexus.registry-converger`.
