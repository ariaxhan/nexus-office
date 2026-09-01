---
type: commission
status: complete
created: 2026-09-01
---

# Edit Markdown in the Office

✅ done

**Goal.** Context documents edit in place and save automatically with stale-draft protection.

| guarantee | proof |
| --- | --- |
| only indexed Markdown can change | context write tests |
| stale drafts and staging-time changes are refused | conflict tests |
| edits save without a button | installed app + live door probe |
| existing file permissions survive | mode test |

**Proof so far.** 794 Python tests and the Swift suite pass. The installed app
renders the source editor and saved state; the live door atomically changed a
real indexed probe on disk. Fresh-context invariant review passed.
Feature merged and pushed to `main` at `003d8ef`.

**Accepted boundary.** The file API has no portable compare-and-swap against a
non-cooperating writer inside the final compare/rename instruction gap. The
door checks at open and again immediately before atomic replacement.
