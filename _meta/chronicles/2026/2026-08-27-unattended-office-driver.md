---
type: chronicle
status: active
created: 2026-08-27
---

# An unattended issue lane opened and drove Office on Aria's desktop

**What mattered:** `com.nexus.issue-dispatch` selected nexus-office issue 39 and its Claude lane ran `npm run shot`. That command starts Office with the fake demo floor and clicks through it; closing Terminal cannot stop a launchd-owned run.

**Shipped**
- Parked nexus-office in `.agents/pipeline.json` and recorded that visual checks must be headless or attended before it is unparked.
- Unloaded `com.nexus.issue-dispatch`; killed both dispatcher trees and both surviving Claude lanes whose working directory was nexus-office.
- Gated `scripts/shoot.sh` on both an interactive terminal and `NEXUS_OFFICE_ALLOW_VISIBLE_SHOTS=1`, before any build or launch.
- Added `tests/test_shoot_safety.py`, including proof that the environment variable alone cannot bypass a missing terminal.

**Verified how:** A fresh dispatch reported `PARKED` and handled zero. Both safety tests passed, an actual unattended `npm run shot` exited 2 with the refusal before Xcode, and no Claude process had nexus-office as its working directory.

**Wrong or surprising**
- The lane prompt prohibited opening a browser or blocking, but the repo documentation called `npm run shot` headless even though it launches a visible macOS app and drives its UI.
- A GitHub webhook through the long-running Office server spawned a second dispatcher after the scheduled job was unloaded; parking the repo closes both entrances.
- The first cleanup killed dispatcher shells but missed orphaned Claude PID 8302. It ran `npm run shot` three minutes later. Parking prevents new work; it cannot revoke a command already held by a live agent.

**Open:** The screenshot harness remains intentionally visible. Do not unpark the pipeline until that behavior is changed or guarded by explicit attended-session consent.
