#!/usr/bin/env bash
# Install the claim guard (#311) as pre-commit in the repo's common hooks dir; idempotent.
# The hook already there moves to pre-commit.lane-next and still runs after the guard passes.
set -eu
repo="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
hooks="$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir)/hooks"
src="$(cd "$(dirname "$0")" && pwd)/hooks/lane-claim-pre-commit.sh"
mkdir -p "$hooks"
if [ -f "$hooks/pre-commit" ] && ! grep -q 'lane-claim-guard' "$hooks/pre-commit"; then
  mv "$hooks/pre-commit" "$hooks/pre-commit.lane-next"
fi
cp "$src" "$hooks/pre-commit"
chmod +x "$hooks/pre-commit"
echo "lane-claim-guard installed: $hooks/pre-commit (next: $([ -e "$hooks/pre-commit.lane-next" ] && echo pre-commit.lane-next || echo none))"
