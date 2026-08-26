#!/usr/bin/env bash
# Set the office password.
#
#   set-password.sh              type your own, twice, hidden
#   set-password.sh --generate   make a memorable one for you
#
# The value goes to the macOS keychain (service nexus-office, account password)
# and to the Worker secret PASSWORD. It is never printed, never written to a
# file, and never passed as a command argument where `ps` could read it.
#
# Read it back later with:
#   security find-generic-password -s nexus-office -a password -w
set -euo pipefail
cd "$(dirname "$0")/.."

WORDS=(amber anchor bramble cedar clover cobble dapple ember fable ferry
       gable harbor hollow indigo juniper kettle lantern meadow mellow nectar
       opal pebble quill ripple saffron thicket umber velvet willow yarrow)

make_password() {
  local a b c n
  a=${WORDS[$((RANDOM % ${#WORDS[@]}))]}
  b=${WORDS[$((RANDOM % ${#WORDS[@]}))]}
  c=${WORDS[$((RANDOM % ${#WORDS[@]}))]}
  n=$((RANDOM % 90 + 10))
  printf '%s-%s-%s-%s' "$a" "$b" "$c" "$n"
}

if [ "${1:-}" = "--generate" ]; then
  PW="$(make_password)"
  echo "[set-password] generated a new password and stored it in your keychain."
else
  printf 'New office password: '
  read -rs PW; echo
  printf 'Again: '
  read -rs PW2; echo
  [ "$PW" = "$PW2" ] || { echo "they do not match" >&2; exit 1; }
  [ ${#PW} -ge 8 ] || { echo "use at least 8 characters" >&2; exit 1; }
fi

printf '%s\n%s\n' "$PW" "$PW" \
  | security add-generic-password -s nexus-office -a password -U -w >/dev/null 2>&1
printf '%s' "$PW" | npx wrangler secret put PASSWORD >/dev/null
unset PW PW2 2>/dev/null || true

echo "[set-password] Worker secret PASSWORD updated."
echo
echo "To see it:  security find-generic-password -s nexus-office -a password -w"
