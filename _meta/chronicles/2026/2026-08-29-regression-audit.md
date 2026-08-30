---
type: chronicle
status: active
created: 2026-08-29
---

# Office and Developer/Vaults regression audit passed

**What mattered:** The shipped migration did not break the Office, launchd fleet, mobile desk flow, hcom desk launches, or custom-agent chat.

**Verified**
- Nexus: 767 Python tests and the configured Xcode tests passed.
- Job runtime: 78 guarantees passed; 47 enabled jobs validated; zero unhealthy; no sync drift; no installed managed plist names `Documents/Vaults`.
- Migrated sources: 29 Python, 2 JSON, 69 plist, and 44 shell files parsed cleanly.
- Live Office: local and tailnet `/` and `/api/world` returned 200; the server process and launchd root name `Developer/Vaults`.
- Phone at 390×844: 80 desks, zero floor Start controls, five bots, Work/Context and README rendered, no console errors or overlay.
- Live triggers: the Office started a headless Codex desk agent to ready/listening, then it was stopped; North accepted a browser turn and replied `READY`.

**Surprising**
- The bundled Browser connection still fails on its blocked `node:process` import. Playwright 1.62.1 with system Chrome supplied the rendered proof.
- Optional hcom cross-device relay points at an old unreachable broker and remains red. Local hcom and the Tailscale Office phone path both worked; the relay config predates this migration.

**Open:** Repair or retire the optional hcom relay separately; it is not on the Office phone path.
