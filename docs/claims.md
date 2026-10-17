# Claim-before-edit registry (#311)

Adopted from TBS `bin/lane-claim.py` (50c7ceb6b, TBS #769). Same store, record keys, release
ledger and exit codes, so either CLI reads the other's claims. Not shared unchanged because
the TBS file hardwires its unclaimed-file rule to TBS branch prefixes and source dirs, and
Nexus lanes run on main in one checkout.

- registry: `<git-common-dir>/lane-claims/*.json` (every worktree sees it); non-git tree:
  `<root>/.lane-claims`. Releases: `releases.jsonl` (`expired` | `clean_finish`).
- record: `{file, lane_id, work_order, issue, claimed_at, heartbeat_at, expires_at}`;
  `expires_at = heartbeat_at + ttl`, a lapsed claim releases on the next read.
- CLI: `python3 nexus/claims.py claim|check|heartbeat|release|list ... [--lane L] [--work-order W] [--root DIR]`.
- lane identity: `--lane`, `$TBS_LANE_ID`, or a lane branch (`prose-build-`, `lane/`, `aria/issue-`, `aria/held/`).
  Nexus lane id = `<repo>#<issue>`, so claims registered before dispatch belong to the lane that runs it.

Enforcement:

- `git commit`: `scripts/install-lane-claim-hook.sh` installs `scripts/hooks/lane-claim-pre-commit.sh`
  as the common pre-commit (displaced hook kept as `pre-commit.lane-next`, still runs).
  Refuses another lane's live claim (everyone, people included) and, for an identified lane,
  any file it never claimed.
- Nexus lanes (commit-tree, no hooks): `executor.fly` claims the write set's literal files
  before the agent starts (another lane's claim = `lease.Owned("claimed:...")`, not started),
  exports `TBS_LANE_ID`; `lanes.land` claims every landed file, refusal = HELD `claim_refused`;
  release on finish.
- Whole-repo lanes (roads) are not claimed: the whole-repo lease already serializes them.
