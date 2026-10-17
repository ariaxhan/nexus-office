#!/usr/bin/env bash
# [lane-claim-guard] pre-commit (#311): refuse a commit of a file another live lane has claimed,
# and, for an identified lane ($TBS_LANE_ID or a lane branch), a file this lane never claimed.
# Installed by scripts/install-lane-claim-hook.sh in the common hooks dir, so every worktree runs it.
# The registry CLI is the canonical checkout's nexus/claims.py: a scratch worktree may not have it yet.
# Then hands off to the hook it displaced (pre-commit.lane-next), so existing guards still run.
set -u
hookdir="$(cd "$(dirname "$0")" && pwd)"
common="$(git rev-parse --path-format=absolute --git-common-dir)"
cli="${LANE_CLAIM_CLI:-$(dirname "$common")/nexus/claims.py}"
paths="$(git diff --cached --name-only --diff-filter=ACMRTD)"
if [ -n "$paths" ]; then
  if [ ! -f "$cli" ]; then
    echo "lane-claim-guard: registry CLI missing at $cli; refusing (a guard that cannot check does not pass)" >&2
    exit 1
  fi
  printf '%s\n' "$paths" | tr '\n' '\0' | xargs -0 python3 "$cli" check --landing --root . || {
    echo "lane-claim-guard: commit refused (claim it first: python3 $cli claim <path> --lane <lane> --work-order <wo>)" >&2
    exit 1
  }
fi
if [ -x "$hookdir/pre-commit.lane-next" ]; then
  exec "$hookdir/pre-commit.lane-next" "$@"
fi
exit 0
