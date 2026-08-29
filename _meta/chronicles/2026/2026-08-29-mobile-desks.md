---
type: chronicle
status: active
created: 2026-08-29
---

# Mobile Office desks open Work and Context instead of an inline Start menu

**What mattered:** The prior pass verified hcom launch, not the phone interaction, and shipped the wrong control. Live data had 80 unique repos and zero duplicate repo ids; the repeated experience came from global sessions plus launch controls under every desk.

**Shipped**
- `98d5240`: desk tap opens Work for sessions, launch, issues, and PRs; Context indexes and opens local Markdown.

**Verified how:** `npm test` passed 767 Python tests plus macOS tests. A 390×844 live Chrome pass opened issue #59, PR #3, and README.md through two 200 Context requests with zero console errors; Claude Opus independently returned PASS.

**Wrong or surprising**
- Browser plugin initialization still fails before tab acquisition; system Chrome supplied the live rendered proof without installing anything.

**Open:** None.
