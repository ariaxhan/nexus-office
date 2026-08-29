---
type: chronicle
status: active
created: 2026-08-29
---

# Five distinct agents moved into the Office

**What mattered:** North, Relay, Rune, Sphinx, and Parallax now replace the source-shaped roster. Each has a visible remit and cadence, a distinct opening message, and one stable thread across the Mac and phone UI.

**Shipped**
- `app/Demo/demo.json`, `client/chat.py`, Swift models/views, phone header, and compatibility tests.
- Live `_meta/bots.json` plus one assistant-only `office.introduction.v1` event per bot.

**Verified how:** `npm test` passed 736 Python tests plus OfficeTests. `./scripts/shoot.sh --offscreen` produced 16 reviewed framings; five bots render above the wall, the wall remains above desks, core questions fit, and cadence renders beside each name. Live `/api/bots` returned the five IDs and frequencies; each `/api/chat` opened assistant-only.

**Wrong or surprising**
- `npm run shot` correctly refused to drive visible windows; the documented offscreen path passed.
- A concurrent-producer review found sequential dedupe insufficient; the harness event sink gained a cross-process lock and regression test.
- The first generic introductions and raw Sphinx model error were unacceptable on screen. Their threads were archived; useful evidence-backed status replaced the intros, and Sphinx posted the first real two-option question.
- Sphinx later denied that ariaxhan had waiting issues because interactive turns could not see Office's live world. `/api/world` held 104 open issues across 12 ariaxhan repos. A producer-side evidence bridge was started in three dirty files, then paused untested; the harness consumer is still missing.

**Open:** Finish and live-verify the evidence bridge first. Typed Sphinx decisions/requeue, automated routing, schedules, and Parallax's longitudinal layer follow.
