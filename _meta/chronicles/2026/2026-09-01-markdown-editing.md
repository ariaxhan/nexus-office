---
type: chronicle
status: active
created: 2026-09-01
---

# Context Markdown became an autosaving editor

**What mattered:** Saves reuse the read allow-list, reject stale source, and recheck after staging before atomic replacement.

**Shipped**
- Context write route, editor, conflict guard, atomic replacement, tests, and installed `/Applications/Office.app`.

**Verified how:** 794 Python tests plus Swift suite passed; live loopback write changed an indexed probe on disk; `app-context.png` shows the source editor and saved state; fresh-context verifier passed.

**Wrong or surprising**
- Computer Use was unavailable; the full shot run captured Context but missed unrelated Gate and Settings frames. Opus verification failed before sampling because its CLI wrapper dropped the prompt.
- The verifier proved compare then rename cannot be a universal compare-and-swap against arbitrary writers. That impossible absolute was narrowed to two explicit checks plus atomic replacement.
- Logical `$PWD` still names retired `Documents/Vaults`; physical `pwd -P` is canonical `Developer/Vaults`.

**Open:** None. Feature commit `003d8ef` is merged and pushed on `main`.
