---
type: chronicle
status: active
created: 2026-09-07
---

# Conveyor recovery remains incomplete

- Recovered: the prior `tbs-finish` merge-conflict streak is no longer current. The eight latest ledger flights were produced at five-minute intervals through 2026-09-07 07:02 UTC.
- Recovered: `com.aria.tbs-work-graph`, `com.nexus.activate-requests`, `com.nexus.activate-creative`, and `com.nexus.activate-calibration` now have successful `jobctl` receipts.
- Still failing: `com.nexus.activate-experiment` attempted at 2026-09-06 21:45 UTC, exited 1, and has no successful receipt.
- Probe failure: `tbs-probe` is quarantined after five consecutive flights exceeded its 280-second budget. A successful product flight does not prove that the unattended health loop is restored.
- Source validation: the Nexus ledger integrity check passed. This record changes no runtime configuration or visual surface, so no lesson or UI captures are applicable.
- Delivery owed: diagnose and bound the slow probe path, repair `activate-experiment`, release the probe quarantine through the owning runtime, then obtain one fresh clean probe receipt before treating #146 as recovered.
