---
type: chronicle
status: active
created: 2026-08-29
---

# The phone can start and reach every Office agent

**What mattered:** Codex auto-registration was stale, and a desk launch held HTTP open until the agent died. Global hooks plus hcom background launch made the Office the controller instead of a terminal window.

**Shipped**
- `729e9c3` on `main`: phone desk controls, background Claude/Codex launch, truthful Mac copy, regression tests.
- Global Claude/Codex hcom hooks enabled; Release installed; Office daemon restarted.

**Verified how:** `npm test` passed 764 Python/Swift tests; offscreen `app-sessions.png` inspected; the live tailnet route started a reachable Codex session in 0.68 s; all five bots replied `<id> ready`; `origin/main` carried `729e9c3`.

**Wrong or surprising**
- The old Office request ran beyond 90 s while hcom already showed the agent ready. `--headless` returned in under one second and retained the session.
- Browser plugin bootstrap failed on blocked `node:process`; live JS/CSS/API and Mac framing were checked, but the phone layout lacks a browser screenshot.

**Open:** Untracked `wrangler.jsonc` remains untouched. Phone visual risk is limited to the new compact start row; source, syntax, route, and live interaction passed.
