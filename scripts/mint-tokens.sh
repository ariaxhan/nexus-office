#!/usr/bin/env bash
# Mint (or rotate) the two office tokens.
#
#   push  the local runner. Reads the inbox, replaces the snapshot. Full trust.
#   view  the browser. Reads the world, queues intent. Cannot execute anything.
#
# Two tokens rather than one because the browser token is the one that travels:
# it goes in a phone's localStorage and gets pasted into links. It has to be
# rotatable on its own, and it has to be worth stealing as little as possible.
#
# Values are written to the macOS keychain and to Worker secrets, and are never
# printed, never passed as an argument, and never written to a file.
set -euo pipefail
cd "$(dirname "$0")/.."

mint() {
  local account="$1" secret="$2" token
  token="$(openssl rand -hex 24)"
  # Fed on stdin, twice, because `security -w <value>` would put the token in
  # this process's argv where any other user on the box can read it out of `ps`.
  printf '%s\n%s\n' "$token" "$token" \
    | security add-generic-password -s nexus-office -a "$account" -U -w >/dev/null 2>&1
  printf '%s' "$token" | npx wrangler secret put "$secret" >/dev/null
  echo "[mint] $account -> keychain(nexus-office/$account) + Worker secret $secret"
}

case "${1:-both}" in
  push) mint push PUSH_TOKEN ;;
  view) mint view VIEW_TOKEN ;;
  both) mint push PUSH_TOKEN; mint view VIEW_TOKEN ;;
  *) echo "usage: $0 [push|view|both]" >&2; exit 2 ;;
esac

echo
echo "Open the office once with the view token in the fragment:"
echo "  security find-generic-password -s nexus-office -a view -w"
