---
type: chronicle
status: active
created: 2026-09-04
---

# Conveyor recovery

- Cause: the deployed `jobrun` predated its valid source and ended with an unmatched quote.
- Repair: `jobctl validate` passed, `jobctl sync` replaced the deployed runtime, and its syntax and drift checks passed.
- Scheduling: sync loaded `com.aria.tbs-auto-pull`; all five reported jobs were started from launchd.
- Mirror: `tbs-curriculum` had thousands of missing tracked files. It was replaced from its remote; the redundant damaged copy and reproducible caches were removed to restore 5 GB free.
- Proof: the deterministic conveyor probe completed without an unhealthy-loop report.
