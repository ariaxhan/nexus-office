---
type: chronicle
status: active
created: 2026-08-27
---

# An unattended issue lane opened and drove Office on Aria's desktop

**What mattered:** `com.nexus.issue-dispatch` selected nexus-office issue 39 and its Claude lane ran `npm run shot`. That command starts Office with the fake demo floor and clicks through it; closing Terminal cannot stop a launchd-owned run.

**Shipped**
- Parked nexus-office in `.agents/pipeline.json` and recorded that visual checks must be headless or attended before it is unparked.
- Unloaded `com.nexus.issue-dispatch` and killed both active nexus-office dispatcher trees.

**Verified how:** The process list contained no nexus-office dispatcher, shot command, or Office build. A fresh `dispatch.sh --repo /Users/you/Developer/Vaults/CodingVault/nexus-office` reported `PARKED`, surveyed zero issues, and handled zero.

**Wrong or surprising**
- The lane prompt prohibited opening a browser or blocking, but the repo documentation called `npm run shot` headless even though it launches a visible macOS app and drives its UI.
- A GitHub webhook through the long-running Office server spawned a second dispatcher after the scheduled job was unloaded; parking the repo closes both entrances.

**Open:** The screenshot harness remains intentionally visible. Do not unpark the pipeline until that behavior is changed or guarded by explicit attended-session consent.
