#!/bin/sh
# Xcode pre-build: embed the exact clean source revision in Office.app.
set -eu

OUT="${1:?output header required}"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
REVISION=$(git -C "$ROOT" rev-parse --verify HEAD)
DIRTY=$(git -C "$ROOT" diff --name-only HEAD -- app client scripts package.json)
UNTRACKED=$(git -C "$ROOT" ls-files --others --exclude-standard -- app client scripts package.json)
if [ -n "$DIRTY$UNTRACKED" ]; then
  echo "build identity: relevant source is dirty; refusing an unidentifiable app" >&2
  exit 1
fi
case "$REVISION" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]* ) ;;
  * ) echo "build identity: Git revision is unknown" >&2; exit 1 ;;
esac
umask 077
mkdir -p "$(dirname "$OUT")"
TMP="$OUT.tmp.$$"
printf '#define NEXUS_SOURCE_REVISION %s\n' "$REVISION" > "$TMP"
chmod 600 "$TMP"
mv "$TMP" "$OUT"
