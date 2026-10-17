# Unified input watcher — #404

Checkpoint: existing watcher read end to end; Tower insertion, plan, atomic JSON state and proof pattern retained. Claims acquired using historical repo claims CLI (2babb3c:nexus/claims.py; current checkout lacks it).

Sandbox blocks VaultsInternal .git/lane-claims and ~/cm-studio-lanes writes. Registry edit cannot proceed without its required claim; VaultsInternal commit and requested external report blocked. This document is the report fallback.

Design: one deterministic job, config-only repo/class selection; REST comment/issue streams and one GraphQL check traversal per repo/poll; no cached issue gates. Checks traverse branch histories with persisted cursors and advance the completed-at watermark only after a complete traversal. No deployment or push.

## Build results (checkpoint)

- Implementation: `nexus/input_watch.py`; config: `input-watch.json`; tests: `tests/test_input_watch.py`; report: this document.
- Config: `{"repos":{"owner/repo":["issue_comments","issues","pr_comments","pr_review_comments","failed_check_runs"]}}`. Ships both original repositories with all five enabled. Config alone determines tracked repos/classes; no label, issue-state, or cached-metadata gate.
- Shared path: normalize → loop/config skip accounting → `input:<class>:<stable-id>` → existing Tower `github-work` plan `plan_ba61e8c006b6`, accepted ledger task. SQLite `BEGIN IMMEDIATE` serializes check-and-insert. No LLM trigger calls.
- First run initializes all ten repo/class watermarks to now without API calls or tasks. One atomic state JSON defaults to `~/Library/Application Support/nexus-jobs/state/input-watch.json`; includes initialization fences, watermarks, skip reason counters, check traversal cursors, backoff, class errors, last-run summary. Directory flock prevents concurrent writers.
- REST: one list/class/repo/poll; issue and PR discussion comments share GitHub's issues/comments endpoint but retain distinct class cursors. Fresh detail GETs classify PRs; reused only within the same poll. Conditional ETags bind to exact request URLs. Updated-time keyset + one-second overlap avoids offset pagination losses; stable task dedupe absorbs replay. Watermarks advance only after durable registration. A saturated timestamp boundary (100 results with no cursor progress) records an error and retains the watermark rather than dropping inputs.
- HTTP 403/429 ends the run and records backoff; active backoff still refreshes the state proof. Ordinary errors remain local to the class, retain its cursor, and produce nonzero CLI status after other classes/repos are polled.
- Targeted tests: `python3 -m unittest discover -s tests -p test_input_watch.py -v` — **14 passed**. Independent verifier reran all 14 and confirmed REST task/watermark safety.
- Live read-only smoke: all four REST class endpoints accepted queries and returned ETags; GraphQL check query accepted by GitHub (0 completed checks on sampled branch page, continuation present). No production ledger writes/state changes.
- Isolated CLI smoke: `python3 -m nexus.input_watch --state /tmp/input-unify-smoke/state.json --ledger /tmp/input-unify-smoke/ledger.sqlite` — initialized ten classes, wrote proof JSON, created no ledger.

## Incomplete: failed check coverage

Failed checks normalize/dedupe/register through the same path, using completedAt and stable check-run database IDs. One GraphQL request per repo/poll traverses branch histories, 50 commits at a time, with persisted cursors. Watermark advances to sweep-start only after the entire sweep completes; old-commit checks are not excluded by commit date. GraphQL does not provide REST ETags.

**This does not meet comprehensive repository-wide failed-check detection.** Checks only reachable from fork PR heads, deleted branches, detached commits, or tags can be missed. Full-history sweeps can delay detection substantially (10,000 commits on one branch alone takes about 16.7 hours at five-minute polls). More than 50 suites on a commit or 100 runs in a suite records overflow and holds the watermark; nested pagination is not implemented. Do not install this as a complete five-class replacement. Comprehensive checks need a durable check_run webhook delivery source or an explicitly broader API-call allowance; GitHub's REST check-run lists are scoped to a ref or suite, not an entire repo. Source: https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference

## Blocked: Vaults registry / requested report path

Required claims failed with sandbox `PermissionError: Operation not permitted` at:

- `/Users/slowember/VaultsInternal/.git/lane-claims/.mutex`
- `/Users/slowember/cm-studio-lanes/.lane-claims`

No Vaults registry file was edited because claim-before-edit could not be satisfied. No Vaults registry commit exists. The requested `~/cm-studio-lanes/input-unify-report.md` could not be created; this claimed repo document is the durable fallback report.

Proposed source row for the supervisor (not installed; old row retained):

```json
{"id":"com.nexus.input-watch","state":"enabled","command":["/usr/bin/python3","/Users/slowember/Developer/Vaults/CodingVault/nexus-office/nexus/input_watch.py"],"schedule":{"kind":"interval","seconds":300},"cadence_s":300,"timeout_s":120,"max_success_age_h":1,"owner":"nexus-office","note":"Unified deterministic config-driven GitHub inputs to Tower. Config: CodingVault/nexus-office/input-watch.json. State: ~/Library/Application Support/nexus-jobs/state/input-watch.json. Failed-check coverage incomplete; see docs/input-watch.md before installation.","proof":"/Users/slowember/Developer/Vaults/_meta/services/jobs/proof-fresh 1 \"/Users/slowember/Library/Application Support/nexus-jobs/state/input-watch.json\"","host":"arias-Mac-Studio"}
```

Existing running watcher and deployed registry untouched. No push; no background processes started or left running. Supervisor retains installation responsibility after coverage gaps are resolved.
